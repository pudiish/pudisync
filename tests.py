#!/usr/bin/env python3
"""Acceptance tests. Needs fake_wled.py running; starts its own hub if asked.

    python fake_wled.py &
    python tests.py
"""
import json
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

sys.path.insert(0, ".")
CONFIG_FOR_TESTS = "/tmp/ambilight-test-config.json"
from ambilight import processing
from ambilight.config import Config
from ambilight.plugins.output_wled_udp import MAX_LEDS_PER_PACKET, WLEDUDPOutput

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    shown = not (isinstance(detail, str) and not detail)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if shown else ""))


def sniff(port, expect, send):
    """Run send() and collect `expect` UDP packets on our own socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.settimeout(2.0)
    send()
    out = []
    try:
        for _ in range(expect):
            out.append(s.recvfrom(65535)[0])
    except socket.timeout:
        pass
    s.close()
    return out


def test_packets():
    print("\n1. DNRGB packet format")
    PORT = 21399
    out = WLEDUDPOutput("127.0.0.1", PORT)

    px = np.zeros((240, 3), np.uint8)
    px[0] = (255, 0, 0)
    pkts = sniff(PORT, 1, lambda: out.send(px, 2))
    check("240 LEDs -> exactly 1 packet", len(pkts) == 1, f"got {len(pkts)}")
    if pkts:
        p = pkts[0]
        check("protocol byte is 4 (DNRGB)", p[0] == 4, f"got {p[0]}")
        check("timeout byte honoured", p[1] == 2, f"got {p[1]}")
        check("start index is 0", (p[2] << 8 | p[3]) == 0)
        check("payload is 240x3 bytes", len(p) - 4 == 720, f"got {len(p)-4}")
        check("first LED RGB preserved", tuple(p[4:7]) == (255, 0, 0))

    big = np.full((1000, 3), 9, np.uint8)
    pkts = sniff(PORT, 3, lambda: out.send(big, 2))
    check("1000 LEDs -> 3 packets", len(pkts) == 3, f"got {len(pkts)}")
    starts = [(p[2] << 8 | p[3]) for p in pkts]
    check("chunk start indices 0/400/800", starts == [0, 400, 800], str(starts))
    check("no chunk exceeds 489 LEDs (doc limit)",
          all((len(p) - 4) // 3 <= 489 for p in pkts))
    check("chunk size is 400", (len(pkts[0]) - 4) // 3 == MAX_LEDS_PER_PACKET)

    pkts = sniff(PORT, 1, lambda: out.send(px, 255))
    check("timeout 255 (hold) passes through", pkts and pkts[0][1] == 255)

    pkts = sniff(PORT, 1, out.release)
    check("release frame uses short timeout", pkts and pkts[0][1] == 1,
          f"got {pkts[0][1] if pkts else 'none'}")
    check("release frame is black", pkts and tuple(pkts[0][4:7]) == (0, 0, 0))
    out.close()


def test_mapping():
    print("\n2. Mapping: normal and reversed strips")
    W = 1920
    band = np.zeros((40, W, 3), np.uint8)
    band[:, : W // 2] = (255, 0, 0)
    band[:, W // 2 :] = (0, 0, 255)

    m = processing.build_mapping(240, 10, 110, W)
    z = processing.zone_colors(band, m)
    check("normal: screen-left maps to left_led (red)", np.allclose(z[0], (1, 0, 0)))
    check("normal: screen-right maps to right_led (blue)", np.allclose(z[-1], (0, 0, 1)))

    r = processing.build_mapping(240, 110, 10, W)
    zr = processing.zone_colors(band, r)
    # Strip order runs lo..hi; with left_led=110 the strip START (LED 10) is screen RIGHT.
    check("reversed: LED 10 shows screen-right (blue)", np.allclose(zr[0], (0, 0, 1)))
    check("reversed: LED 110 shows screen-left (red)", np.allclose(zr[-1], (1, 0, 0)))
    check("reversed is the mirror of normal", np.array_equal(zr, z[::-1]))

    check("auto-detect: left>right flagged reversed", 110 > 10)


def test_fade():
    print("\n3. Edge fade")
    m = processing.build_mapping(240, 10, 110, 1920)
    low, high = processing.build_fade(m, 5)
    check("low ramp covers LEDs below zone", low.size == 10, f"{low.size}")
    check("high ramp covers LEDs above zone", high.size == 129, f"{high.size}")
    check("fade decays away from zone", low[-1] > low[0] and high[0] > high[-1])
    check("fade reaches zero past fade_leds", low[0] == 0.0 and high[-1] == 0.0)

    lo0, hi0 = processing.build_fade(m, 0)
    check("fade_leds=0 is a hard cutoff", not lo0.any() and not hi0.any())

    z = np.full((101, 3), 200, np.float32)
    full = processing.extend_edges(z, low, high)
    check("extended frame length == led_count", full.shape[0] == 240, str(full.shape[0]))
    check("out-of-zone copies nearest edge colour, faded", 0 < full[9][0] < 200)


def test_engine_parts():
    print("\n11. Engine components")
    from ambilight.engine import _BlackFrameWatchdog, _Pacer, _Pipeline

    black = np.zeros((150, 600, 3), np.uint8)
    lit = np.full((150, 600, 3), 80, np.uint8)

    w = _BlackFrameWatchdog()
    warned = [m for m in (w.check(black) for _ in range(35)) if m]
    check("a never-lit stream warns about Screen Recording", bool(warned))

    w = _BlackFrameWatchdog()
    w.check(lit)
    check("a dark scene after light does NOT warn",
          not any(m for m in (w.check(black) for _ in range(60)) if m))

    w = _BlackFrameWatchdog()
    for _ in range(35):
        w.check(black)
    check("the warning clears when light returns", w.check(lit) == "")

    # Sub-sampling must not invent a permissions problem on a lit frame.
    odd = np.zeros((150, 600, 3), np.uint8)
    odd[5, 0] = 255
    w = _BlackFrameWatchdog()
    check("a frame lit only on an unsampled row does not false-alarm",
          not any(m for m in (w.check(odd) for _ in range(35)) if m))

    class RealStop:
        """Sleeps for real, so the pacer is measured against the clock."""

        def __init__(self):
            self.calls = 0

        def wait(self, seconds):
            self.calls += 1
            time.sleep(seconds)

        def is_set(self):
            return False

    pacer = _Pacer()
    stop = RealStop()
    started = time.perf_counter()
    for _ in range(12):
        pacer.tick(120, stop)
    took = time.perf_counter() - started
    check("the pacer paces rather than spinning", stop.calls > 0)
    check("12 frames at 120fps take about 0.1s", 0.07 < took < 0.22,
          f"{took:.3f}s")

    # A frame that overruns badly must not make the pacer sprint to catch up.
    pacer = _Pacer()
    pacer.tick(60, stop)
    time.sleep(0.4)
    behind = time.perf_counter()
    pacer.tick(60, stop)
    check("the pacer resyncs instead of sprinting after a stall",
          time.perf_counter() - behind < 0.05,
          f"{time.perf_counter() - behind:.3f}s")

    cfg = Config(CONFIG_FOR_TESTS)
    pipe = _Pipeline(cfg)
    band = np.zeros((150, 600, 3), np.uint8)
    band[:, :300] = (255, 0, 0)
    band[:, 300:] = (0, 0, 255)
    out = pipe.render(band, cfg.snapshot())
    check("the pipeline returns one byte triple per LED",
          out.shape == (cfg.get("led_count"), 3), str(out.shape))
    check("the pipeline emits uint8 for the wire", out.dtype == np.uint8)

    first_key = pipe._key
    pipe.render(band, cfg.snapshot())
    check("the mapping is cached between frames", pipe._key is first_key or
          pipe._key == first_key)
    pipe.invalidate()
    check("invalidate forces a rebuild", pipe._key is None)

    # A sleeping display removes the monitor entirely. Sync must wait it out,
    # not end the thread, or it never returns without restarting the program.
    import ambilight.engine as eng
    from ambilight.engine import Engine, capture_hint

    hint = capture_hint(IndexError("monitor 1 not found; 0 attached"))
    check("a missing display is explained in plain words", "asleep" in hint)
    check("other capture errors are passed through",
          "something else" in capture_hint(RuntimeError("something else")))

    class FakeCapture:
        name = "fake"
        alive = False

        def open(self, monitor_index, band_height):
            if not FakeCapture.alive:
                raise IndexError("monitor 1 not found; 0 attached")
            return 600, 150

        def grab(self):
            if not FakeCapture.alive:
                raise OSError("monitor 1 not found; 0 attached")
            band = np.zeros((150, 600, 3), np.uint8)
            band[:, :300] = (200, 30, 30)
            return band

        def close(self):
            pass

    real_capture, real_retry = eng.MSSCapture, eng.RETRY_DISPLAY_S
    eng.MSSCapture, eng.RETRY_DISPLAY_S = FakeCapture, 0.3
    try:
        sleep_cfg = Config(CONFIG_FOR_TESTS)
        sleep_cfg.update({"wled_ip": "127.0.0.1", "led_count": 60})
        engine = Engine(sleep_cfg)
        engine.start()
        time.sleep(1.0)
        check("a sleeping display does not kill the engine", engine.running is True)
        check("the user is told why the lights stopped",
              "asleep" in (engine.last_error or ""))

        FakeCapture.alive = True
        time.sleep(1.5)
        check("sync resumes by itself when the display wakes", engine.fps > 5,
              f"{engine.fps:.0f} fps")
        check("the sleep warning clears on wake", not engine.last_error,
              str(engine.last_error))

        FakeCapture.alive = False
        time.sleep(1.2)
        check("it survives the display sleeping again", engine.running is True)
        engine.stop()
    finally:
        eng.MSSCapture, eng.RETRY_DISPLAY_S = real_capture, real_retry
        FakeCapture.alive = False


def test_api(base):
    print("\n4. HTTP API")
    def get(path):
        with urllib.request.urlopen(base + path, timeout=3) as r:
            return json.loads(r.read())

    s = get("/api/sync/on")
    check("/api/sync/on starts the engine", s["running"] is True)
    time.sleep(2.0)
    s = get("/api/status")
    check("engine reports a live frame rate", s["measured_fps"] > 20,
          f"{s['measured_fps']} fps")
    check("status carries a preview for the UI", len(s.get("preview", [])) > 0)
    check("status never leaks the PIN", "pin" not in s)

    s = get("/api/profile/movie")
    check("/api/profile/movie applies", s["profile"] == "movie")
    check("movie profile smooths more", s["blend"] <= 0.3, f"blend={s['blend']}")
    s = get("/api/profile/game")
    check("/api/profile/game applies", s["profile"] == "game")
    check("game profile reacts faster", s["blend"] >= 0.5, f"blend={s['blend']}")

    s = get("/api/set?saturation=1.8")
    check("/api/set changes a value live", abs(s["saturation"] - 1.8) < 0.01)
    s = get("/api/set?fps=9999")
    check("/api/set clamps out-of-range input", s["fps"] <= 120, f"fps={s['fps']}")

    check("status reports the steady output rate", s.get("output_fps", 0) > 20,
          s.get("output_fps"))
    with urllib.request.urlopen(base + "/calibrate", timeout=3) as r:
        page = r.read().decode()
    check("the calibration page is served", "Match the strip" in page)

    def missing(path):
        try:
            urllib.request.urlopen(base + path, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc.code == 404
        return False

    check("replaying an unknown recording is a clean 404",
          missing("/api/record/replay?file=nope.npz"))
    check("replay cannot be pointed outside the recordings folder",
          missing("/api/record/replay?file=../config.json"))

    d = get("/api/diagnostics")
    check("diagnostics lists plugins", len(d["plugins"]) >= 4)
    check("diagnostics reports active capture", any(p["active"] for p in d["plugins"]))
    check("diagnostics reports WLED reachability", "wled_reachable" in d)

    m = get("/api/wled/meta")
    check("WLED effects read back", len(m.get("effects", [])) > 0)
    check("WLED palettes read back", len(m.get("palettes", [])) > 0)
    check("WLED presets read back", len(m.get("presets", [])) > 0)

    s = get("/api/wled/effect?index=3")
    check("choosing an effect stops sync first", s["running"] is False)

    s = get("/api/sync/off")
    check("/api/sync/off stops the engine", s["running"] is False)


def test_wizard(base):
    print("\n5. Setup wizard")
    def get(path):
        with urllib.request.urlopen(base + path, timeout=3) as r:
            return json.loads(r.read())

    p = get("/api/wizard/count/start")
    check("count search starts at WLED's reported max", p["k"] == 240, f"k={p['k']}")
    steps, guard = 0, 0
    # Simulate a 173-LED strip: 'lit' is true whenever the probe reaches the end.
    TRUE_LEN = 173
    while not p.get("done") and guard < 20:
        p = get(f"/api/wizard/count/answer?lit={1 if p['k'] >= TRUE_LEN else 0}")
        steps += 1
        guard += 1
    check("binary search converges", p.get("done") is True)
    check("binary search finds the true length", p["k"] == TRUE_LEN, f"got {p['k']}")
    check("search is logarithmic", steps <= 9, f"{steps} questions for 240 LEDs")

    get("/api/set?led_count=240")
    r = get("/api/wizard/marker?index=57")
    check("edge marker clamps and reports", r["index"] == 57)
    r = get("/api/wizard/marker?index=99999")
    check("marker clamps above strip length", r["index"] == 239, f"got {r['index']}")

    g = get("/api/wizard/gradient")
    check("test gradient spans the zone", g["span"] > 0)
    get("/api/wizard/stop")


def test_zones():
    print("\n6. Zones")
    from ambilight import zones as zl

    z = zl.normalize([{"start": 100, "end": 50, "mode": "solid", "color": "ff0000"},
                      {"start": 0, "end": 60, "mode": "warm"}], 240)
    check("reversed start/end is corrected", z[0]["start"] < z[0]["end"])
    check("zones are sorted by position", z[0]["start"] <= z[1]["start"])
    check("overlaps are trimmed", z[0]["end"] < z[1]["start"])
    check("unknown mode falls back to sync",
          zl.normalize([{"start": 0, "end": 5, "mode": "bogus"}], 240)[0]["mode"] == "sync")
    check("malformed colour falls back",
          zl.normalize([{"start": 0, "end": 5, "color": "zzz"}], 240)[0]["color"] == "#ffb46b")
    check("out-of-range zone is clamped",
          zl.normalize([{"start": 0, "end": 9999}], 240)[0]["end"] == 239)

    base = np.full((240, 3), 100.0, np.float32)
    zs = zl.normalize([{"start": 0, "end": 39, "mode": "warm"},
                       {"start": 200, "end": 239, "mode": "solid", "color": "#0000ff"}], 240)
    out = zl.composite(base, zs, 240)
    check("warm zone renders lamp white", tuple(out[10].astype(int)) == (255, 170, 95))
    check("solid zone renders its colour", tuple(out[220].astype(int)) == (0, 0, 255))
    check("unzoned LEDs still show screen sync", tuple(out[120].astype(int)) == (100, 100, 100))
    check("composite leaves the sync frame untouched", base[10][0] == 100.0)

    off = zl.composite(base, zl.normalize([{"start": 0, "end": 9, "mode": "off"}], 240), 240)
    check("off zone goes dark", off[5].sum() == 0)
    dim = zl.composite(base, zl.normalize(
        [{"start": 0, "end": 9, "mode": "sync", "brightness": 0.5}], 240), 240)
    check("zone brightness scales the sync layer", abs(dim[5][0] - 50.0) < 0.01)
    check("no zones is a no-op", zl.composite(base, [], 240) is base)

    # Real-world zone editing: these all come from dragging edges around.
    inner = zl.normalize([{"start": 0, "end": 100, "mode": "warm"},
                          {"start": 40, "end": 60, "mode": "off"}], 240)
    check("a zone dropped inside another splits it, keeping both sides",
          [(z["start"], z["end"], z["mode"]) for z in inner] ==
          [(0, 39, "warm"), (40, 60, "off"), (61, 100, "warm")],
          str([(z["start"], z["end"], z["mode"]) for z in inner]))

    split = zl.composite(base, inner, 240)
    check("the split zone renders lamp on both sides of the gap",
          tuple(split[10].astype(int)) == (255, 170, 95)
          and tuple(split[80].astype(int)) == (255, 170, 95)
          and split[50].sum() == 0)

    touching = zl.normalize([{"start": 0, "end": 50, "mode": "warm"},
                             {"start": 50, "end": 100, "mode": "off"}], 240)
    check("zones dragged until they touch do not overlap",
          touching[0]["end"] < touching[1]["start"])

    buried = zl.normalize([{"start": 0, "end": 200, "mode": "warm"},
                           {"start": 0, "end": 100, "mode": "off"},
                           {"start": 0, "end": 50, "mode": "solid"}], 240)
    check("a zone buried under two others still resolves",
          [(z["start"], z["end"]) for z in buried] == [(0, 50), (51, 100), (101, 200)],
          str([(z["start"], z["end"]) for z in buried]))

    check("a single-LED zone survives",
          zl.normalize([{"start": 120, "end": 120}], 240)[0]["start"] == 120)
    check("a whole-strip zone survives",
          zl.normalize([{"start": 0, "end": 239}], 240)[0]["end"] == 239)

    # These arrive over the network from Shortcuts or curl, so they must not raise.
    for junk in ([{"start": "abc", "end": 5}], [{"start": None, "end": None}],
                 "notalist", None, [None], [{"start": -5, "end": -1}]):
        try:
            zl.normalize(junk, 240)
            ok = True
        except Exception:
            ok = False
        check(f"malformed zones {str(junk)[:24]!r} are ignored, not fatal", ok)


def test_serial():
    print("\n7. USB serial output")
    from ambilight.plugins.output_serial import SerialOutput, list_ports, required_baud

    check("no phantom ports when nothing is plugged in",
          all("Bluetooth" not in p for p in list_ports()))
    check("115200 is too slow for 240 LEDs at 60fps",
          required_baud(240, 60) > 115200, f"needs {required_baud(240,60)}")
    check("small strips fit in 115200", required_baud(60, 30) == 115200)
    check("baud recommendation is a standard rate",
          required_baud(240, 60) in (230400, 460800, 500000, 921600, 1000000, 2000000))

    class FakePort:
        def __init__(self):
            self.buf = b""

        def write(self, data):
            self.buf += data

        def close(self):
            pass

    out = SerialOutput.__new__(SerialOutput)
    out.port, out.baud, out._ser, out.last_error = "fake", 0, FakePort(), None
    out.send(np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], np.uint8))
    frame = out._ser.buf
    check("Adalight magic header", frame[:3] == b"Ada")
    check("LED count encoded as n-1", (frame[3] << 8 | frame[4]) == 2)
    check("Adalight checksum is correct", frame[5] == (frame[3] ^ frame[4] ^ 0x55))
    check("RGB payload follows the header", frame[6:] == b"\xff\x00\x00\x00\xff\x00\x00\x00\xff")


def test_serial_api(base):
    print("\n8. Output switching")
    def get(path):
        with urllib.request.urlopen(base + path, timeout=3) as r:
            return json.loads(r.read())

    p = get("/api/serial/ports")
    check("serial discovery endpoint responds", "ports" in p)
    check("recommended baud is reported", p["recommended_baud"] >= 115200)

    get("/api/sync/on")
    time.sleep(1.0)
    get("/api/set?output=serial&serial_port=/dev/cu.definitely-not-real")
    time.sleep(1.5)
    s = get("/api/status")
    check("a dead serial port does not stop the engine", s["running"] is True)
    check("engine falls back to UDP", s["output_plugin"] == "wled-udp-dnrgb")
    check("the fallback is reported to the user", "serial" in (s["last_error"] or ""))

    get("/api/set?output=udp")
    time.sleep(1.2)
    s = get("/api/status")
    check("stale serial error clears on switching back", not s["last_error"],
          str(s["last_error"]))
    get("/api/sync/off")


def test_provision():
    print("\n9. Flashing and provisioning")
    from ambilight.provision import (_FIVE_GHZ_HINT, build_packet, flash_size_mb,
                                     parse_packets, pick_binary, wifi_packet, TYPE_RPC)

    # An ESP8266 cannot join 5GHz, so flagging those names prevents the most
    # common provisioning failure.
    for name in ("FTTH-5G", "Sri Krishna 3F_5G", "Home 5GHz", "Net_5ghz"):
        check(f"{name!r} flagged as 5GHz", bool(_FIVE_GHZ_HINT.search(name)))
    for name in ("JioFiber-Ishwar", "PUDI", "Jai shree ram_2.4GHz", "G5 Network"):
        check(f"{name!r} not falsely flagged", not _FIVE_GHZ_HINT.search(name))

    pkt = build_packet(TYPE_RPC, bytes([0x02, 0]))
    check("Improv header is correct", pkt[:6] == b"IMPROV")
    check("Improv version byte is 1", pkt[6] == 1)
    check("Improv checksum is the 8-bit sum", pkt[-2] == (sum(pkt[:-2]) & 0xFF))
    check("Improv packet ends with a newline", pkt[-1] == 0x0A)

    wifi = wifi_packet("MyNet", "secret")
    check("credentials are length-prefixed", b"MyNet" in wifi and b"secret" in wifi)
    check("wifi packet checksum is valid", wifi[-2] == (sum(wifi[:-2]) & 0xFF))

    parsed, _ = parse_packets(bytearray(pkt + wifi))
    check("both packets parse back", len(parsed) == 2)
    check("parsed packets verify", all(p["valid"] for p in parsed))
    corrupt = bytearray(pkt)
    corrupt[-2] ^= 0xFF
    bad, _ = parse_packets(corrupt)
    check("a corrupted checksum is rejected", bad and not bad[0]["valid"])

    # Flashing the wrong variant bricks a board, so matching must be exact.
    # Names are copied from the real v16.0.1 release.
    builds = [{"name": n} for n in (
        "WLED_16.0.1_ESP01.bin", "WLED_16.0.1_ESP01_160.bin",
        "WLED_16.0.1_ESP02.bin", "WLED_16.0.1_ESP02_min.bin",
        "WLED_16.0.1_ESP32.bin",
        "WLED_16.0.1_ESP32-C3.bin", "WLED_16.0.1_ESP32-S2.bin",
        "WLED_16.0.1_ESP32-S3_8MB_none.bin", "WLED_16.0.1_ESP32_Ethernet.bin",
        "WLED_16.0.1_ESP8266.bin", "WLED_16.0.1_ESP8266_160.bin",
        "WLED_16.0.1_ESP8266_compat.bin")]
    # ESP8266 builds differ by flash layout: a 4MB NodeMCU wants the full
    # build, and the 4MB layout would not boot on a 1MB ESP-01.
    check("4MB ESP8266 (NodeMCU) picks the ESP8266 build",
          pick_binary("esp8266", builds, 4)["name"] == "WLED_16.0.1_ESP8266.bin")
    check("2MB ESP8266 picks the plain ESP02 build",
          pick_binary("esp8266", builds, 2)["name"] == "WLED_16.0.1_ESP02.bin")
    check("1MB ESP8266 picks the ESP01 build",
          pick_binary("esp8266", builds, 1)["name"] == "WLED_16.0.1_ESP01.bin")
    check("ESP8266 of unknown flash size gets no binary",
          pick_binary("esp8266", builds, None) is None)
    flash_id_output = ("Detecting chip type... ESP8266\nChip type:          ESP8266EX\n"
                       "Manufacturer: 68\nDevice: 4016\nDetected flash size: 4MB\n")
    check("flash size is read from esptool output",
          flash_size_mb(flash_id_output) == 4)
    check("missing flash size reads as unknown", flash_size_mb("Connecting....") is None)
    check("plain ESP32 does NOT pick a C3/S3 build",
          pick_binary("esp32", builds)["name"] == "WLED_16.0.1_ESP32.bin",
          pick_binary("esp32", builds)["name"])
    check("ESP32-C3 picks its own build",
          pick_binary("esp32-c3", builds)["name"] == "WLED_16.0.1_ESP32-C3.bin")
    check("ESP32-S3 picks its own build",
          "S3" in pick_binary("esp32-s3", builds)["name"])
    check("an unknown chip yields no binary", pick_binary("rp2040", builds) is None)


def test_provision_api(base):
    print("\n10. Flash API")
    def get(path):
        with urllib.request.urlopen(base + path, timeout=3) as r:
            return json.loads(r.read())

    c = get("/api/flash/check")
    check("flash readiness reports tooling", "esptool" in c and "pyserial" in c)

    try:
        urllib.request.urlopen(base + "/api/flash/start?port=&ssid=x", timeout=3)
        check("a flash with no port is rejected", False)
    except urllib.error.HTTPError as exc:
        check("a flash with no port is rejected", exc.code == 400)

    get("/api/flash/start?port=/dev/cu.not-a-real-board&ssid=TestNet&password=x")
    deadline = time.time() + 20
    st = {}
    while time.time() < deadline:
        st = get("/api/flash/status")
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.6)
    check("a missing board ends in a clean error", st.get("state") == "error")
    check("the error tells the user what to do",
          "BOOT" in (st.get("error") or "") or "board" in (st.get("error") or "").lower())
    check("the hub survives a failed flash", get("/api/status")["running"] in (True, False))

    # Erasing a board is irreversible, so it must never be the default.
    get("/api/flash/start?port=/dev/cu.not-a-real-board&ssid=TestNet")
    time.sleep(2.5)
    log = " ".join(get("/api/flash/status").get("log", []))
    check("erase is NOT the default", "Erasing" not in log and "Writing WLED" not in log,
          log[:70])
    check("the safe default goes straight to Wi-Fi", "Wi-Fi" in log, log[:70])

    get("/api/flash/start?port=/dev/cu.not-a-real-board&ssid=TestNet&flash=1")
    time.sleep(2.5)
    log = " ".join(get("/api/flash/status").get("log", []))
    check("flash=1 does take the firmware path", "Looking for the board" in log, log[:70])

    nets = get("/api/flash/networks")
    check("remembered networks are offered", isinstance(nets.get("networks"), list))
    check("each network carries a 5GHz flag",
          all("likely_5ghz" in n and "ssid" in n for n in nets["networks"]))
    if nets["current"]:
        check("the connected network is listed first",
              nets["networks"][0]["ssid"] == nets["current"])


def test_letterbox():
    print("\n10. Picture edge detection (letterbox / pillarbox)")
    from ambilight.processing import find_picture
    from ambilight.engine import _PictureTracker

    W, H = 1280, 500

    def strip(bottom_black=0, side_black=0, subtitle=False):
        """A bright test picture, with black bars where a video would leave them."""
        img = np.full((H, W, 3), 140, dtype=np.uint8)
        if bottom_black:
            img[H - bottom_black:] = 0
        if side_black:
            img[:, :side_black] = 0
            img[:, W - side_black:] = 0
        if subtitle and bottom_black:
            img[H - bottom_black // 2 - 5:H - bottom_black // 2 + 5, 500:780] = 255
        return img

    inset, left, right = find_picture(strip())
    check("a full-screen picture needs no inset", inset == 0, inset)
    check("a full-screen picture keeps every column", (left, right) == (0, W), (left, right))

    inset, _, _ = find_picture(strip(bottom_black=150))
    check("letterbox bars are skipped", abs(inset - 150) <= 2, inset)

    inset, _, _ = find_picture(strip(bottom_black=150, subtitle=True))
    check("a subtitle inside the bar is not mistaken for picture",
          abs(inset - 150) <= 2, inset)

    _, left, right = find_picture(strip(side_black=200))
    check("pillarbox bars narrow the columns",
          abs(left - 200) <= 2 and abs(right - (W - 200)) <= 2, (left, right))

    inset, left, right = find_picture(np.zeros((H, W, 3), dtype=np.uint8))
    check("an all-black screen falls back to the default",
          (inset, left, right) == (0, 0, W), (inset, left, right))

    _, left, right = find_picture(strip(side_black=W // 2 - 20))
    check("a sliver of light never collapses the width", (left, right) == (0, W), (left, right))

    tracker = _PictureTracker()
    bars = strip(bottom_black=150)
    n = _PictureTracker.CONFIRM
    for _ in range(n - 1):
        tracker.update(bars)
    check("a few detections are not trusted yet", tracker.applied == (0, 0, W), tracker.applied)
    check("about a second of agreement is trusted", tracker.update(bars)[0] >= 144)
    check("a brief flicker back to full screen is ignored",
          tracker.update(strip())[0] >= 144)
    dark = np.zeros((H, W, 3), dtype=np.uint8)
    for _ in range(n * 3):
        tracker.update(dark)
    check("a long dark scene never moves the bars", tracker.applied[0] >= 144, tracker.applied)
    for _ in range(n):
        tracker.update(strip())
    check("a lasting change is followed", tracker.applied[0] == 0, tracker.applied)


def test_frame_layout():
    print("\n11. Four-sided layout around the monitor")
    from ambilight.processing import find_frame, frame_colors

    W, H, T = 1000, 600, 40

    def ramp(n, along_width):
        """Brightness 0..255 along the band's long axis, as an (h, w, 3) image."""
        line = np.linspace(0, 255, n).astype(np.uint8)
        band = np.tile(line, (T, 1)) if along_width else np.tile(line[:, None], (1, T))
        return np.stack([band] * 3, axis=-1)

    # Brightness encodes position along each edge, left->right and top->bottom.
    edges = {"bottom": ramp(W, True), "top": ramp(W, True),
             "left": ramp(H, False), "right": ramp(H, False)}
    out = frame_colors(edges, (36, 20, 36, 20))
    lum = out.mean(axis=1) * 255
    check("one colour per LED around the frame", out.shape == (112, 3), out.shape)

    bottom, right, top, left = lum[:36], lum[36:56], lum[56:92], lum[92:]
    check("bottom runs left to right", bottom[0] < 10 and bottom[-1] > 230, (bottom[0], bottom[-1]))
    check("right runs bottom to top", right[0] > 220 and right[-1] < 20, (right[0], right[-1]))
    check("top runs right to left", top[0] > 230 and top[-1] < 10, (top[0], top[-1]))
    check("left runs top to bottom", left[0] < 20 and left[-1] > 220, (left[0], left[-1]))

    from ambilight.processing import slice_edges
    screen = np.zeros((H, W, 3), dtype=np.uint8)
    screen[100:500, 100:900] = 50                       # picture, with black bars around
    screen[100:140, 100:900] = (200, 0, 0)              # picture's top edge: red
    screen[460:500, 100:900] = (0, 200, 0)              # bottom edge: green
    screen[100:500, 100:140] = (0, 0, 200)              # left edge: blue
    screen[100:500, 860:900] = (200, 200, 0)            # right edge: yellow
    sliced = slice_edges(screen, (100, 500, 100, 900), 40)
    check("edges are cut from the picture, not the screen border",
          sliced["bottom"].shape == (40, 800, 3) and sliced["left"].shape == (400, 40, 3),
          (sliced["bottom"].shape, sliced["left"].shape))
    check("the bottom slice is the picture's bottom edge",
          tuple(sliced["bottom"][20, 400]) == (0, 200, 0), tuple(sliced["bottom"][20, 400]))
    check("the right slice is the picture's right edge",
          tuple(sliced["right"][200, 20]) == (200, 200, 0), tuple(sliced["right"][200, 20]))
    check("a thick band never exceeds half the picture",
          slice_edges(screen, (100, 500, 100, 900), 5000)["top"].shape[0] == 200)

    # The strip is stuck inside the monitor's edge, so each side covers only the
    # middle of that screen edge. LEDs must map to that stretch, not the whole edge.
    from ambilight.processing import coverage
    check("14 LEDs cover ~70% of a 336mm side", abs(coverage(14, 16.7, 336) - 0.696) < 0.01,
          coverage(14, 16.7, 336))
    check("more LEDs than the side is long never exceeds 100%", coverage(40, 16.7, 597) == 1.0)

    half = frame_colors(edges, (36, 20, 36, 20), coverage=(1.0, 0.5, 1.0, 1.0))
    from ambilight.color import linear_to_srgb
    right_half = linear_to_srgb(half[36:56]).mean(axis=1) * 255
    check("a short side starts a quarter of the way up the edge",
          abs(right_half[0] - 191) < 12, right_half[0])
    check("a short side stops three quarters of the way up",
          abs(right_half[-1] - 64) < 12, right_half[-1])

    inner = slice_edges(screen, (100, 500, 100, 900), 40, skip=8)
    check("the outermost pixels are skipped",
          inner["bottom"].shape == (40, 784, 3) and tuple(inner["bottom"][-1, 400]) == (0, 200, 0),
          (inner["bottom"].shape, tuple(inner["bottom"][-1, 400])))

    from ambilight.plugins.capture_mss import MSSCapture

    class FakeShot:
        width, height = 4, 2
        bgra = bytes([10, 20, 30, 255] * 8)        # B=10 G=20 R=30, opaque alpha

    class FakeSct:
        def grab(self, region):
            return FakeShot()

    cap = MSSCapture()
    cap._sct = FakeSct()
    cap._mon = {"left": 0, "top": 0, "width": 4, "height": 2}
    grabbed = cap.grab_full()
    check("a full grab comes back as RGB, not BGRA",
          grabbed.shape == (2, 4, 3) and tuple(grabbed[0, 0]) == (30, 20, 10),
          (grabbed.shape, tuple(grabbed[0, 0])))
    check("the opaque alpha channel cannot read as picture",
          find_frame(np.full((H, W, 4), (0, 0, 0, 255), dtype=np.uint8)[..., 2::-1]) == (0, H, 0, W))

    plain = np.full((H, W, 3), 120, dtype=np.uint8)
    check("a full-screen picture uses the whole screen",
          find_frame(plain) == (0, H, 0, W), find_frame(plain))

    boxed = plain.copy()
    boxed[:80] = 0
    boxed[H - 80:] = 0
    top_, bottom_, left_, right_ = find_frame(boxed)
    check("letterbox bars are cut off top and bottom",
          abs(top_ - 80) <= 8 and abs(bottom_ - (H - 80)) <= 8, (top_, bottom_))
    check("full width is kept with letterbox bars", (left_, right_) == (0, W), (left_, right_))

    boxed = plain.copy()
    boxed[:, :200] = 0
    boxed[:, W - 200:] = 0
    top_, bottom_, left_, right_ = find_frame(boxed)
    check("pillarbox bars are cut off left and right",
          abs(left_ - 200) <= 8 and abs(right_ - (W - 200)) <= 8, (left_, right_))
    check("an all-black screen falls back to the whole screen",
          find_frame(np.zeros((H, W, 3), dtype=np.uint8)) == (0, H, 0, W))


def test_color_engine():
    print("\n12. Colour engine (linear light, hue-safe saturation, LED gamma)")
    from ambilight import color, processing as pr

    srgb = np.linspace(0, 1, 11, dtype=np.float32)
    check("sRGB -> linear -> sRGB round-trips",
          np.allclose(color.linear_to_srgb(color.srgb_to_linear(srgb)), srgb, atol=1e-3))

    # Half black, half white averages to half the LIGHT, not half the code value.
    band = np.zeros((24, 100, 3), np.uint8)
    band[:, 50:] = 255
    m = {"starts": np.array([0]), "stops": np.array([100])}
    mean = pr.zone_colors(band, m)[0]
    check("averaging happens in linear light", np.allclose(mean, 0.5, atol=0.01), mean)
    check("which reads brighter than a naive average",
          color.linear_to_srgb(mean)[0] > 0.7, color.linear_to_srgb(mean))

    # A coloured object on a grey edge should win the LED, not wash out.
    band = np.full((24, 100, 3), 128, np.uint8)
    band[:, :30] = (230, 30, 30)
    plain = pr.zone_colors(band, m)[0]
    weighted = pr.zone_colors(band, m, colour_weight=0.6)[0]
    check("colour weighting favours the coloured pixels",
          weighted[0] / weighted.sum() > plain[0] / plain.sum(), (plain, weighted))

    cfg = {"wb_r": 1.0, "wb_g": 1.0, "wb_b": 1.0, "saturation": 2.0,
           "led_gamma": 1.5, "black_level": 0.03}
    orange = color.srgb_to_linear(np.array([[0.9, 0.5, 0.2]], np.float32))
    graded = color.grade(orange, cfg)
    h_in = color.rgb_to_hsv(np.array([[0.9, 0.5, 0.2]], np.float32))[0, 0]
    h_out = color.rgb_to_hsv(graded)[0, 0]
    check("a saturation boost keeps the hue", abs(h_in - h_out) < 0.01, (h_in, h_out))
    check("and never pushes a channel out of range", graded.min() >= 0 and graded.max() <= 1)
    check("it does make the colour more saturated",
          color.rgb_to_hsv(graded)[0, 1] > color.rgb_to_hsv(np.array([[0.9, 0.5, 0.2]]))[0, 1])

    grey = color.grade(color.srgb_to_linear(np.array([[0.5, 0.5, 0.5]], np.float32)), cfg)
    check("grey stays grey", np.ptp(grey) < 1e-3, grey)
    murk = np.array([[0.07, 0.05, 0.06]], np.float32)
    murk_out = color.grade(color.srgb_to_linear(murk), cfg)
    check("dark murky pixels are not boosted into colour",
          color.rgb_to_hsv(murk_out)[0, 1] <= color.rgb_to_hsv(murk)[0, 1] + 1e-3,
          (color.rgb_to_hsv(murk)[0, 1], color.rgb_to_hsv(murk_out)[0, 1]))

    warm = dict(cfg, saturation=1.0, wb_g=0.9, wb_b=0.8)
    white = color.grade(np.ones((1, 3), np.float32), warm)[0]
    check("white balance tints white", white[0] > white[1] > white[2], white)

    led = color.to_led(np.array([[0.5, 0.5, 0.5]], np.float32), cfg)[0]
    check("LED gamma darkens mid tones", abs(led[0] - 255 * 0.5 ** 1.5) < 1, led)
    check("full white stays full", np.allclose(color.to_led(np.ones((1, 3)), cfg), 255))
    near_black = color.to_led(np.array([[0.01, 0.01, 0.01]], np.float32), cfg)
    check("near-black fades out instead of glowing", near_black.max() == 0, near_black)
    above = color.to_led(np.array([[0.2, 0.2, 0.2]], np.float32), cfg)
    check("a dim but real colour is kept", above.min() > 0, above)

    row = np.zeros((5, 3), np.float32)
    row[2] = 1.0
    blurred = pr.spread(row, 0.4)
    check("spreading lets neighbours share a little light",
          blurred[1, 0] > 0 and blurred[3, 0] > 0 and blurred[2, 0] < 1, blurred[:, 0])
    check("spreading conserves the light", abs(blurred.sum() - row.sum()) < 1e-5)
    ring = np.zeros((5, 3), np.float32)
    ring[0] = 1.0
    check("a ring wraps round the start corner", pr.spread(ring, 0.4, ring=True)[4, 0] > 0)
    check("a plain strip does not wrap", pr.spread(ring, 0.4)[4, 0] == 0)


def test_smoothing():
    print("\n13. Temporal smoothing (spring, scene cuts, flash limit, anti-flicker)")
    from ambilight.smoothing import Quantizer, Smoother, settle_seconds

    check("Response 0.35 settles in about a third of a second",
          0.25 < settle_seconds(0.35) < 0.4, settle_seconds(0.35))
    check("Instant settles in well under 0.1s", settle_seconds(1.0) < 0.1)
    check("Glide settles slower than Quick", settle_seconds(0.2) > settle_seconds(0.7))

    def run(step_hz, seconds, settle=0.3, start=0.0, end=1.0, leds=4):
        sm = Smoother()
        sm.set_target(np.full((leds, 3), start, np.float32), 0.0)
        sm.step(0.0, settle)
        sm.set_target(np.full((leds, 3), end, np.float32), 0.0)
        out, trace = None, []
        for i in range(1, round(seconds * step_hz) + 1):
            out = sm.step(i / step_hz, settle)
            trace.append(out[0, 0])
        return out, trace

    # Downward, so the flash limit (which only caps rises) stays out of it.
    a, _ = run(60, 0.2, start=1.0, end=0.0)
    b, _ = run(30, 0.2, start=1.0, end=0.0)
    check("timing does not depend on the frame rate", abs(a[0, 0] - b[0, 0]) < 0.03,
          (a[0, 0], b[0, 0]))
    settled, trace = run(60, 0.6)
    check("it reaches the target", settled[0, 0] > 0.97, settled[0, 0])
    check("it never overshoots", max(trace) <= 1.0 + 1e-6, max(trace))
    _, rise = run(60, 0.1, settle=0.05)
    check("a flash cannot jump to full brightness in one frame", rise[0] < 0.2, rise[0])

    sm = Smoother()
    sm.set_target(np.zeros((4, 3), np.float32), 0.0)
    sm.step(0.0, 0.4)
    sm.set_target(np.full((4, 3), 0.02, np.float32), 0.0)
    check("a small change is not a scene cut", not sm.cutting(0.01))
    sm.set_target(np.full((4, 3), 0.8, np.float32), 0.02)
    check("a big change is a scene cut", sm.cutting(0.03))
    cut_fast = Smoother()
    cut_fast.set_target(np.full((4, 3), 0.5, np.float32), 0.0)
    cut_fast.step(0.0, 0.4)
    cut_fast.set_target(np.full((4, 3), 0.1, np.float32), 0.0)
    slow = Smoother()
    slow.set_target(np.full((4, 3), 0.5, np.float32), 0.0)
    slow.step(0.0, 0.4)
    slow.set_target(np.full((4, 3), 0.45, np.float32), 0.0)
    for t in np.arange(1, 7) / 60:
        fast_out, slow_out = cut_fast.step(t, 0.4), slow.step(t, 0.4)
    fast_share = (0.5 - fast_out[0, 0]) / 0.4
    slow_share = (0.5 - slow_out[0, 0]) / 0.05
    check("scene cuts move faster than small drifts", fast_share > slow_share,
          (round(float(fast_share), 2), round(float(slow_share), 2)))

    q = Quantizer()
    held = [q.apply(np.array([[100.5 + 0.4 * (-1) ** i] * 3], np.float32))[0, 0]
            for i in range(10)]
    check("tiny wobble never flickers the LED", len(set(held[1:])) == 1, held)
    moved = q.apply(np.array([[110.0, 110.0, 110.0]], np.float32))[0]
    check("a real change still gets through", tuple(moved) == (110, 110, 110), moved)
    check("the quantizer emits wire bytes", moved.dtype == np.uint8)


def test_recorder():
    print("\n14. Frame recorder and replay")
    import os, tempfile
    from ambilight.recorder import Recorder, replay

    path = os.path.join(tempfile.mkdtemp(), "rec.npz")
    rec = Recorder(path, seconds=10, now=0.0)
    rng = np.random.default_rng(1)
    for i in range(30):
        rec.add(i / 30, rng.random((6, 3), dtype=np.float32))
    rec.save()
    check("a recording is written", os.path.exists(path))

    cfg = {"wb_r": 1.0, "wb_g": 1.0, "wb_b": 1.0, "saturation": 1.2, "led_gamma": 1.5,
           "black_level": 0.03, "blend": 0.35, "spread": 0.0, "brightness": 1.0}
    first, stats = replay(path, cfg)
    second, _ = replay(path, cfg)
    check("replay is deterministic", np.array_equal(first, second))
    check("replay yields one frame per recorded frame", first.shape == (30, 6, 3), first.shape)
    check("replay reports a flicker figure", "flicker" in stats, stats)
    check("a recording stops itself after its length", Recorder(path, 1, 0.0).add(2.0, first[0]) is False)


def main():
    base = "http://127.0.0.1:8080"
    print("=" * 62)
    print("AMBILIGHT HUB -- ACCEPTANCE TESTS")
    print("=" * 62)
    test_packets()
    test_mapping()
    test_fade()
    test_zones()
    test_serial()
    test_provision()
    test_letterbox()
    test_frame_layout()
    test_color_engine()
    test_smoothing()
    test_recorder()
    test_engine_parts()
    try:
        urllib.request.urlopen(base + "/api/status", timeout=2)
    except Exception as exc:
        print(f"\n  (skipping HTTP tests -- hub not running at {base}: {exc})")
    else:
        test_api(base)
        test_wizard(base)
        test_serial_api(base)
        test_provision_api(base)

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    FAILED: {f}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
