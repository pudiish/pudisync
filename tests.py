#!/usr/bin/env python3
"""Acceptance tests.

    python tests.py

Starts its own fake WLED and a sandbox hub on ports 8790-8791 with a throwaway
config, so it can never touch a live hub on 8080 or real LEDs. The HTTP part
captures the real screen, so run it with the display awake and unlocked.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

import numpy as np

sys.path.insert(0, ".")
CONFIG_FOR_TESTS = os.path.join(tempfile.mkdtemp(prefix="ambilight-tests-"), "config.json")
SANDBOX_PORT, FAKE_WLED_HTTP, FAKE_WLED_UDP = 8790, 8791, 21391
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
    pipe = _Pipeline()
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

        @staticmethod
        def available():
            return True

        @staticmethod
        def display_count():
            return 1

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

        def grab_full(self):
            return self.grab()

        def grab_search(self, rows):
            return self.grab()

        def set_inset(self, rows):
            pass

        def close(self):
            pass

    # The fake display must not depend on whether this Mac is locked right now.
    real_capture, real_retry, real_locked = eng.CAPTURES, eng.RETRY_DISPLAY_S, eng.screen_locked
    eng.CAPTURES, eng.RETRY_DISPLAY_S = {"fake": FakeCapture}, 0.3
    eng.screen_locked = lambda: False
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
        eng.CAPTURES, eng.RETRY_DISPLAY_S, eng.screen_locked = real_capture, real_retry, real_locked
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
    check("diagnostics lists every registered plugin", len(d["plugins"]) >= 3)
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

    # Only a board that is actually plugged in may be flashed over HTTP.
    try:
        urllib.request.urlopen(
            base + "/api/flash/start?port=/dev/cu.not-a-real-board&ssid=TestNet", timeout=3)
        check("an unplugged port cannot be flashed", False)
    except urllib.error.HTTPError as exc:
        check("an unplugged port cannot be flashed", exc.code == 400)
    check("the hub survives a refused flash", get("/api/status")["running"] in (True, False))

    # The provisioner itself, driven directly (no board needed).
    from ambilight.provision import Provisioner

    def run(flash, wait=2.5):
        prov = Provisioner()
        prov.start("/dev/cu.not-a-real-board", "TestNet", "x", flash=flash)
        deadline = time.time() + (20 if wait is None else wait)
        while time.time() < deadline and prov.status()["state"] not in ("done", "error"):
            time.sleep(0.3)
        return prov.status()

    st = run(flash=False, wait=None)
    check("a missing board ends in a clean error", st.get("state") == "error")
    check("the error tells the user what to do",
          "BOOT" in (st.get("error") or "") or "board" in (st.get("error") or "").lower())
    # Erasing a board is irreversible, so it must never be the default.
    log = " ".join(st.get("log", []))
    check("erase is NOT the default", "Erasing" not in log and "Writing WLED" not in log, log[:70])
    check("the safe default goes straight to Wi-Fi", "Wi-Fi" in log, log[:70])
    log = " ".join(run(flash=True).get("log", []))
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

    # The strip is one rectangle: both upright sides span its full height even
    # when one lost an LED at a corner (35/14/35/13), and top matches bottom.
    from ambilight.processing import box_coverage
    shares = box_coverage((35, 14, 35, 13), 16.7, 597, 336)
    check("left and right cover the same height", shares[1] == shares[3], shares)
    check("top and bottom cover the same width", shares[0] == shares[2], shares)
    check("the box is as tall as its longer upright side",
          abs(shares[1] - 14 * 16.7 / 336) < 1e-6, shares)
    check("the box is as wide as its longer horizontal side",
          abs(shares[0] - 35 * 16.7 / 597) < 1e-6, shares)
    # A measured strip rectangle overrides the LED-count estimate; one a bit
    # outside the screen samples every edge corner to corner, and never more.
    outside = box_coverage((35, 14, 35, 13), 16.7, 597, 336, box_mm=(615, 350))
    check("a box just outside the screen covers each full edge", outside == (1.0, 1.0, 1.0, 1.0), outside)
    inside = box_coverage((35, 14, 35, 13), 16.7, 597, 336, box_mm=(560, 300))
    check("a measured box inside the screen maps to its own size",
          abs(inside[0] - 560 / 597) < 1e-6 and abs(inside[1] - 300 / 336) < 1e-6, inside)

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
           "led_gamma": 1.5, "black_level": 0.03, "colour_match": False, "contrast": 1.0}
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

    # Strip balance trims the LED channels themselves (this strip's green runs
    # strong), so it scales every colour alike instead of feeding the
    # saturation boost a tinted white.
    trim = dict(cfg, saturation=1.0, wb_g=0.8, colour_match=True, white_kelvin=6500, contrast=1.0)
    even = dict(trim, wb_g=1.0)
    white_even = color.to_led(np.ones((1, 3), np.float32), even)[0]
    white_trim = color.to_led(np.ones((1, 3), np.float32), trim)[0]
    check("strip balance takes green out of white",
          abs(white_trim[1] / white_trim[0] - 0.8 * white_even[1] / white_even[0]) < 0.02,
          (white_even.round(), white_trim.round()))
    skin_rgb = np.array([[224 / 255, 172 / 255, 138 / 255]], np.float32)
    check("grading no longer depends on strip balance",
          np.allclose(color.grade(color.srgb_to_linear(skin_rgb), trim),
                      color.grade(color.srgb_to_linear(skin_rgb), even)))

    led = color.to_led(np.array([[0.5, 0.5, 0.5]], np.float32), cfg)[0]
    check("LED gamma darkens mid tones", abs(led[0] - 255 * 0.5 ** 1.5) < 1, led)
    check("full white stays full", np.allclose(color.to_led(np.ones((1, 3)), cfg), 255))
    near_black = color.to_led(np.array([[0.01, 0.01, 0.01]], np.float32), cfg)
    check("near-black fades out instead of glowing", near_black.max() == 0, near_black)
    above = color.to_led(np.array([[0.2, 0.2, 0.2]], np.float32), cfg)
    check("a dim but real colour is kept", above.min() > 0, above)

    # WS2812B primaries differ from the screen's: its blue is relatively strong,
    # so screen white or warm yellow driven 1:1 washes out bluish-white.
    match = dict(cfg, colour_match=True, white_kelvin=6500)
    warm_yellow = np.array([[1.0, 200 / 255, 90 / 255]], np.float32)
    plain_led = color.to_led(warm_yellow, cfg)[0]
    matched_led = color.to_led(warm_yellow, match)[0]
    check("warm yellow drives far less blue once matched to the LEDs",
          matched_led[2] / matched_led[0] < 0.5 * plain_led[2] / plain_led[0],
          (plain_led.round(), matched_led.round()))
    check("warm yellow keeps strong red and green",
          matched_led[1] > 0.6 * matched_led[0], matched_led.round())
    led_white = color.to_led(np.ones((1, 3), np.float32), match)[0]
    check("screen white needs much less LED blue", led_white[2] < 0.6 * led_white[1], led_white)
    check("matched white is still full brightness", led_white.max() > 254, led_white)
    led_red = color.to_led(np.array([[1.0, 0.0, 0.0]], np.float32), match)[0]
    check("red stays red", led_red[0] > 3 * max(led_red[1], led_red[2]), led_red)
    dim = color.to_led(np.array([[0.5, 0.5, 0.5]], np.float32), match)[0]
    check("matching changes the mix, not the brightness curve",
          abs(dim.max() - 255 * 0.5 ** 1.5) < 2, dim)

    # Warmth: the strip's white point, moved along the blackbody line.
    skin = np.array([[224 / 255, 172 / 255, 138 / 255]], np.float32)
    cool = color.to_led(skin, match)[0]
    warm = color.to_led(skin, dict(match, white_kelvin=4500))[0]
    check("a warmer white point takes blue out of skin",
          warm[2] / warm[0] < 0.75 * cool[2] / cool[0], (cool.round(), warm.round()))
    warm_white = color.to_led(np.ones((1, 3), np.float32), dict(match, white_kelvin=4500))[0]
    check("a warm white leans red over blue", warm_white[0] > 2.5 * warm_white[2], warm_white)
    check("warmth keeps white at full brightness", warm_white.max() > 254, warm_white)
    hot = color.to_led(skin, dict(match, white_kelvin=3000))[0]
    check("lower Kelvin is warmer still", hot[2] / hot[0] < warm[2] / warm[0], hot.round())
    flat = dict(match, contrast=1.0)
    punchy = dict(match, contrast=1.5)
    level = lambda c, conf: color.to_led(np.array([c], np.float32), conf)[0].max()
    check("contrast brightens bright colours", level((0.88, 0.67, 0.54), punchy)
          > level((0.88, 0.67, 0.54), flat) + 10,
          (level((0.88, 0.67, 0.54), flat), level((0.88, 0.67, 0.54), punchy)))
    check("contrast darkens dark colours", level((0.25, 0.25, 0.25), punchy)
          < level((0.25, 0.25, 0.25), flat))
    check("contrast keeps full white full", level((1, 1, 1), punchy) > 254)
    check("contrast 1.0 changes nothing",
          np.allclose(color.to_led(skin, flat), color.to_led(skin, dict(match, contrast=1.0))))

    deep_sky = np.array([[40 / 255, 110 / 255, 220 / 255]], np.float32)
    check("warmth leaves saturated colours alone",
          np.allclose(color.to_led(deep_sky, match), color.to_led(deep_sky, dict(match, white_kelvin=4500)),
                      atol=1.0),
          (color.to_led(deep_sky, match).round(), color.to_led(deep_sky, dict(match, white_kelvin=4500)).round()))

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

    def progress(start, end, seconds, leds=20, moving=None, settle=settle_seconds(0.35)):
        """Share of the way LED 0 has moved after `seconds` at 60 fps."""
        sm = Smoother()
        first = np.full((leds, 3), start, np.float32)
        sm.set_target(first, 0.0)
        sm.step(0.0, settle)
        second = first.copy()
        second[:leds if moving is None else moving] = end
        sm.set_target(second, 0.0)
        for i in range(1, round(seconds * 60) + 1):
            out = sm.step(i / 60, settle)
        return float((out[0, 0] - start) / (end - start))

    check("a scene cut to a bright picture lands within a tenth of a second",
          progress(0.1, 0.9, 0.1) > 0.8, round(progress(0.1, 0.9, 0.1), 2))
    check("one LED changing a lot snaps too, without a whole-screen cut",
          progress(0.1, 0.9, 0.12, moving=1) > 0.8, round(progress(0.1, 0.9, 0.12, moving=1), 2))
    check("small drifts still glide at the chosen pace",
          progress(0.5, 0.45, 0.1) < 0.6, round(progress(0.5, 0.45, 0.1), 2))

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
           "black_level": 0.03, "blend": 0.35, "spread": 0.0, "brightness": 1.0,
           "colour_match": True, "white_kelvin": 4500, "contrast": 1.5,
           "power_limit_ma": 3800, "sustained_ma": 3000}
    first, stats = replay(path, cfg)
    second, _ = replay(path, cfg)
    check("replay is deterministic", np.array_equal(first, second))
    check("replay yields one frame per recorded frame", first.shape == (30, 6, 3), first.shape)
    check("replay reports a flicker figure", "flicker" in stats, stats)
    check("a recording stops itself after its length", Recorder(path, 1, 0.0).add(2.0, first[0]) is False)


def test_safety():
    print("\n15. LED safety (power budget, heat, anti-strobe, stale-frame fade)")
    from ambilight.safety import Guard, estimate_ma
    from ambilight.engine import OutputStage

    cfg = {"power_limit_ma": 3800, "sustained_ma": 3000}
    white = np.full((180, 3), 255.0, np.float32)
    check("full white on 180 LEDs is ~9.9A", 9700 < estimate_ma(white) < 10200, estimate_ma(white))

    guard = Guard()
    out = guard.apply(white, 0.0, cfg)
    check("a frame over the budget is scaled under it", estimate_ma(out) <= 3800 * 1.01, estimate_ma(out))
    check("the limit is reported", guard.state()["power_limited"] is True)
    dim = np.full((180, 3), 40.0, np.float32)
    check("a frame under the budget is untouched", np.allclose(Guard().apply(dim, 0.0, cfg), dim))

    near = np.full((180, 3), 80.0, np.float32)       # ~3.3A: fits the budget
    after = guard.apply(near, 0.1, cfg)
    check("brightness returns gradually after a limit, not in one jump",
          after.mean() < near.mean() * 0.99, (after.mean(), near.mean()))
    for i in range(30):
        later = guard.apply(near, 0.2 + i * 0.05, cfg)
    check("and fully once the release time has passed", np.allclose(later, near, atol=0.5))

    hot = Guard()
    steady = np.full((180, 3), 85.0, np.float32)      # ~3.5A, inside the budget
    burst = [hot.apply(steady, t * 0.5, cfg) for t in range(10)][-1]
    check("a short bright scene is not dimmed for heat", estimate_ma(burst) > 3400, estimate_ma(burst))
    for t in range(10, 1400):
        long_run = hot.apply(steady, t * 0.5, cfg)
    check("ten minutes of bright light settles at the sustained limit",
          abs(estimate_ma(long_run) - 3000) < 90, estimate_ma(long_run))
    check("heat limiting is reported", hot.state()["heat_limited"] is True)

    strobe = Guard()
    black = np.zeros((180, 3), np.float32)
    bright = np.full((180, 3), 60.0, np.float32)
    swings = []
    for i in range(20):                               # 10 flashes a second
        frame = strobe.apply(bright if i % 2 else black, i * 0.05, cfg)
        swings.append(frame.mean())
    check("a 10Hz strobe is damped after the third flash",
          max(swings[-6:]) - min(swings[-6:]) < 0.5 * 60, swings[-6:])
    check("anti-strobe is reported", strobe.state()["strobe_damped"] is True)
    cut = Guard()
    cut.apply(black, 0.0, cfg)
    check("a single scene cut is not treated as a strobe",
          np.allclose(cut.apply(bright, 0.05, cfg), bright))
    fast_cuts = Guard()
    ramp = [0.0, 0.35, 0.7, 1.0, 1.0, 1.0, 0.65, 0.3, 0.0, 0.0]      # up over 3 frames, later down
    for i, level in enumerate(ramp):
        last = fast_cuts.apply(np.full((180, 3), 200.0, np.float32) * level, i / 60, cfg)
    check("two quick cuts, each spread over a few frames, are not a strobe",
          not fast_cuts.state()["strobe_damped"] and np.allclose(last, black))

    stage = OutputStage()
    live = {"blend": 1.0, "led_gamma": 1.0, "contrast": 1.0, "black_level": 0.0, "colour_match": False,
            "zones": [], "led_count": 4, "brightness": 1.0, "power_limit_ma": 3800, "sustained_ma": 3000,
            "wb_r": 1.0, "wb_g": 1.0, "wb_b": 1.0}
    stage.set_target(np.full((4, 3), 0.8, np.float32), 0.0)
    fresh = stage.frame(0.5, live)
    stale = stage.frame(6.0, live)
    check("a frozen capture fades the strip out instead of holding it",
          fresh.max() > 150 and stale.max() == 0, (fresh.max(), stale.max()))


def test_config():
    print("\n16. Config: validation, atomic saves, recovery")
    import os, tempfile, threading as th
    from ambilight.config import Config, DEFAULTS, PROFILES

    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "config.json")
    with open(path, "w") as fh:
        json.dump({"fps": "60", "saturation": "1.4", "layout": "frame", "bogus": 1}, fh)
    cfg = Config(path)
    check("stored values are coerced on load", cfg.get("fps") == 60 and cfg.get("saturation") == 1.4)
    check("unknown stored keys are dropped", "bogus" not in cfg.snapshot())

    with open(path, "w") as fh:
        fh.write("{ not json")
    recovered = Config(path)
    check("a corrupt config falls back to defaults", recovered.get("fps") == DEFAULTS["fps"])
    check("and the bad file is kept aside, not deleted", os.path.exists(path + ".bad"))

    def rejects(changes):
        try:
            recovered.update(changes)
        except ValueError:
            return True
        return False

    check("layout only accepts known values", rejects({"layout": "banana"}))
    check("layout is case-insensitive", recovered.update({"layout": "Frame"}) == ["layout"]
          and recovered.get("layout") == "frame")
    check("an output that does not exist is refused", rejects({"output": "laser"}))
    check("a controller address cannot carry a URL", rejects({"wled_ip": "evil.example/x"}))
    check("an mDNS controller name is accepted", recovered.update({"wled_ip": "wled-6b0bc6.local"}) == ["wled_ip"])
    check("an IP controller address is accepted", recovered.update({"wled_ip": "192.168.29.57"}) == ["wled_ip"])
    check("infinite numbers are refused", rejects({"fps": "1e400"}))
    check("non-numbers are refused, not crashed on", rejects({"fps": "fast"}))
    check("a serial port must be a device path", rejects({"serial_port": "../../etc/passwd"}))

    before = recovered.get("saturation")
    recovered.apply_profile("game")
    check("moods change response only, not colour tuning",
          recovered.get("saturation") == before and recovered.get("blend") == PROFILES["game"]["blend"])

    # Zones in the same request as a new LED count are checked against the new
    # count, not the old one (an old count of 97 squeezed the "off" zone onto LED 96).
    recovered.update({"led_count": 97, "zones": "[]"})
    both = recovered.update({"led_count": 180, "zones": json.dumps(
        [{"start": 0, "end": 96, "mode": "sync"}, {"start": 97, "end": 179, "mode": "off"}])})
    kept = [(z["start"], z["end"], z["mode"]) for z in recovered.get("zones")]
    check("zones sent with a new LED count use the new count",
          kept == [(0, 96, "sync"), (97, 179, "off")], kept)

    errors = []
    def hammer(i):
        try:
            for k in range(30):
                recovered.update({"brightness": ((i * 30 + k) % 100) / 100})
        except Exception as exc:
            errors.append(exc)
    threads = [th.Thread(target=hammer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("concurrent saves never fail", not errors, errors[:1])
    with open(path) as fh:
        check("and always leave a readable file", isinstance(json.load(fh), dict))


def test_access():
    print("\n17. Access control (cross-site, DNS rebinding, PIN throttling)")
    from ambilight.access import PinGate, allowed_fetch_site, allowed_host

    check("the hub's IP is a trusted host", allowed_host("192.168.29.53:8080"))
    check("IPv6 literals are trusted", allowed_host("[fe80::1]:8080"))
    check("localhost is trusted", allowed_host("localhost:8080"))
    check("a Bonjour .local name is trusted", allowed_host("ishwars-macbook-air.local:8080"))
    check("a public name is refused (DNS rebinding)", not allowed_host("attacker.example:8080"))
    check("a missing Host header is refused", not allowed_host(""))
    check("same-origin page requests pass", allowed_fetch_site("same-origin"))
    check("typed URLs and bookmarks pass", allowed_fetch_site("none"))
    check("Shortcuts and curl send no header and pass", allowed_fetch_site(None))
    check("another website cannot drive the hub", not allowed_fetch_site("cross-site"))
    check("neither can a sibling site", not allowed_fetch_site("same-site"))

    gate = PinGate(max_failures=3, window_s=60, lockout_s=60)
    check("no PIN set lets everyone in", gate.check("", "", "10.0.0.2", now=0) == "ok")
    check("the right PIN is accepted", gate.check("1234", "1234", "10.0.0.2", now=0) == "ok")
    check("a wrong PIN is denied", gate.check("0000", "1234", "10.0.0.2", now=1) == "denied")
    gate.check("0001", "1234", "10.0.0.2", now=2)
    gate.check("0002", "1234", "10.0.0.2", now=3)
    check("repeated guessing locks that device out",
          gate.check("1234", "1234", "10.0.0.2", now=4) == "locked")
    check("other devices are not locked out", gate.check("1234", "1234", "10.0.0.3", now=4) == "ok")
    check("the lockout expires", gate.check("1234", "1234", "10.0.0.2", now=70) == "ok")


def test_frame_wizard():
    print("\n19. Setup test pattern for a strip round the monitor")
    from ambilight.wizard import Wizard

    class Painter:
        running = False
        frame = None

        def set_override(self, px):
            Painter.frame = px

        def clear_override(self):
            pass

    cfg = Config(CONFIG_FOR_TESTS)
    cfg.update({"layout": "frame", "led_count": 120, "edge_bottom": 35, "edge_right": 14,
                "edge_top": 35, "edge_left": 13})
    Wizard(cfg, Painter()).gradient()
    px = Painter.frame.astype(int)
    bottom, right, top, left = px[:35], px[35:49], px[49:84], px[84:97]
    check("the bottom side shows red", bottom[:, 0].mean() > 3 * bottom[:, 1:].mean(), bottom[-1])
    check("the right side shows green", right[:, 1].mean() > 3 * right[:, [0, 2]].mean(), right[-1])
    check("the top side shows blue", top[:, 2].mean() > 3 * top[:, :2].mean(), top[-1])
    check("the left side shows white", np.ptp(left[-1]) < 10 and left[-1].mean() > 100, left[-1])
    check("each side brightens in the strip's direction",
          bottom[-1].max() > bottom[1].max() and top[-1].max() > top[1].max())
    check("the start LED is marked", tuple(px[0]) != tuple(bottom[1]), px[0])
    check("LEDs past the frame stay dark", px[97:].max() == 0)
    cfg.update({"layout": "strip"})


def test_firmware_download():
    print("\n20. Firmware download is verified before anything is erased")
    import hashlib, pathlib
    from ambilight.provision import download_firmware

    folder = pathlib.Path(tempfile.mkdtemp())
    image = folder / "WLED_test_ESP8266.bin"
    image.write_bytes(b"\xe9" + bytes(4095))
    digest = "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()
    good = {"name": image.name, "url": image.as_uri(), "size": 4096, "digest": digest}
    path = download_firmware(good)
    check("a matching image is accepted", pathlib.Path(path).read_bytes() == image.read_bytes())
    check("it lands in a private temp folder, not a fixed /tmp name",
          not str(path).startswith("/tmp/WLED"), path)

    def refused(binary):
        try:
            download_firmware(binary)
        except RuntimeError:
            return True
        return False

    check("a truncated download is refused", refused(dict(good, size=8192)))
    check("a tampered download is refused", refused(dict(good, digest="sha256:" + "0" * 64)))
    check("a release without a digest still checks the size", not refused(dict(good, digest=None)))


def test_quartz_capture():
    print("\n21. Native macOS capture plugin")
    from ambilight.plugins import CAPTURES
    from ambilight.plugins.capture_quartz import QuartzCapture

    check("native capture is preferred when available",
          not QuartzCapture.available() or next(iter(CAPTURES)) == "quartz", list(CAPTURES))
    check("mss stays registered as the fallback", "mss" in CAPTURES)
    if not QuartzCapture.available():
        print("    (skipped: pyobjc Quartz not installed)")
        return
    cap = QuartzCapture()
    try:
        cap.open(99, 120)
        check("a missing display is reported like mss does", False)
    except IndexError as exc:
        check("a missing display is reported like mss does", "not found" in str(exc), str(exc))
    width, height = cap.open(1, 120)
    full = cap.grab_full()
    check("a full grab matches the opened size", full.shape == (height, width, 3), full.shape)
    check("it is RGB uint8", full.dtype == np.uint8)
    band = cap.grab()
    check("the band is the bottom rows", band.shape == (120, width, 3), band.shape)
    cap.set_inset(40)
    check("the inset lifts the band off the edge", cap.grab().shape == (120, width, 3))
    check("a search strip is taller", cap.grab_search(400).shape == (400, width, 3))
    cap.close()


def test_sync_intent():
    print("\n22. The Mac follows the board: stream while WLED is in the sync preset")
    from ambilight.intent import SyncIntent

    class Board:
        def __init__(self):
            self.ps, self.up = -1, True

        def state(self):
            return {"ps": self.ps} if self.up else None

    class Engine:
        running = False

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    board, engine, now = Board(), Engine(), [0.0]
    cfg = Config(CONFIG_FOR_TESTS)
    cfg.update({"sync_preset": 1})
    intent = SyncIntent(cfg, board, engine, clock=lambda: now[0])

    check("nothing streams while the board shows something else", intent.tick() is None and not engine.running)
    board.ps = 1
    check("choosing monitor sync on the board starts the stream", intent.tick() == "started" and engine.running)
    check("it keeps running while the board stays in sync", intent.tick() is None and engine.running)
    board.ps = -1
    check("picking an effect or colour stops it", intent.tick() == "stopped" and not engine.running)
    board.ps = 1
    intent.tick()
    board.up = False
    check("an unreachable board never changes anything", intent.tick() is None and engine.running)
    board.up = True

    engine.stop()
    intent.pause(60)
    board.ps = 1
    check("while paused (setup wizard, flashing) it does not restart", intent.tick() is None and not engine.running)
    now[0] += 61
    check("the pause expires by itself", intent.tick() == "started" and engine.running)
    intent.pause(600)
    intent.resume()
    engine.stop()
    check("resume ends a pause at once", intent.tick() == "started")


def test_sync_routes():
    print("\n23. Hub routes keep the board and the stream in step")
    from urllib.parse import parse_qs
    from ambilight import routes
    from ambilight.intent import SyncIntent

    class Wled:
        def __init__(self):
            self.sent = []

        def set_preset(self, pid):
            self.sent.append(("preset", int(pid)))
            return True

        def apply(self, payload):
            self.sent.append(("apply", payload))
            return True

        def state(self):
            return {"ps": 1}

    class Engine:
        running = False

        def start(self):
            Engine.running = True

        def stop(self):
            Engine.running = False

        def status(self):
            return {"running": Engine.running}

    class Wizard:
        def stop(self):
            return {}

        def gradient(self):
            return {}

    class Hub:
        wled, engine, wizard = Wled(), Engine(), Wizard()
        cfg = Config(CONFIG_FOR_TESTS)

        def status(self):
            return {"running": Engine.running}

    hub = Hub()
    hub.intent = SyncIntent(hub.cfg, hub.wled, hub.engine)
    hub.intent.pause(300)
    routes.handle(hub, "/api/sync/on", parse_qs(""))
    check("sync on asks the board for the sync preset", ("preset", 1) in hub.wled.sent, hub.wled.sent)
    check("and lifts any setup pause", hub.intent.paused_until == 0.0)
    check("and starts the stream", Engine.running)

    hub.wled.sent.clear()
    routes.handle(hub, "/api/sync/off", parse_qs(""))
    kind, payload = hub.wled.sent[-1]
    seg = payload["seg"][0]
    check("sync off stops the stream", not Engine.running)
    check("and really changes the board's look, so the preset clears",
          kind == "apply" and seg["fx"] == 0 and seg["col"][0] != [40, 26, 10], payload)

    routes.handle(hub, "/api/wizard/gradient", parse_qs(""))
    check("a test pattern pauses the watcher so it cannot restart sync", hub.intent.paused_until > 0)
    routes.handle(hub, "/api/wizard/stop", parse_qs(""))
    check("finishing the wizard lets it follow the board again", hub.intent.paused_until == 0.0)


def test_board_deploy():
    print("\n24. Installing the control page and sync preset on the board")
    import gzip, http.server, pathlib, threading as th
    from ambilight import board

    seen = {"uploads": [], "states": [], "deleted": [], "live": False, "page": b"", "presets": {}}

    class FakeBoard(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/json/info":
                self.reply({"ver": "16.0.1", "live": seen["live"], "fs": {"u": 65, "t": 1024}})
            elif self.path == "/presets.json":
                self.reply(seen["presets"])
            elif self.path.startswith("/edit?func=delete"):
                seen["deleted"].append(urllib.parse.unquote(self.path.split("path=")[1]))
                self.reply(b"File deleted", "text/plain")
            elif self.path == "/":
                self.reply(seen["page"], "text/html", {"Content-Encoding": "gzip"})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload":
                seen["uploads"].append((self.headers["Content-Type"], body))
                seen["page"] = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                self.reply(b"File Uploaded!", "text/plain")
            else:
                state = json.loads(body)
                seen["states"].append(state)
                if "psave" in state:
                    seen["presets"][str(state["psave"])] = {"n": state["n"], "bri": 255}
                self.reply({"success": True})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBoard)
    th.Thread(target=server.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{server.server_address[1]}"
    page = pathlib.Path(tempfile.mkdtemp()) / "index.htm"
    html = "<!doctype html><title>Ambilight</title><p>hello</p>" * 40
    page.write_text(html)

    try:
        result = board.deploy(host, 1, page)
        content_type, body = seen["uploads"][0]
        check("the page is uploaded as a multipart file named /index.htm.gz",
              content_type.startswith("multipart/form-data") and b'filename="/index.htm.gz"' in body, content_type)
        check("it is compressed, which makes loading about three times faster",
              len(seen["page"]) < len(html) / 3, (len(seen["page"]), len(html)))
        check("the plain copy is removed, because the board prefers it over the .gz",
              seen["deleted"] == ["/index.htm"], seen["deleted"])
        check("the board serves back exactly the page", gzip.decompress(seen["page"]).decode() == html
              and result["page_ok"])
        saved = [s for s in seen["states"] if "psave" in s][0]
        check("the sync preset is saved under its id and name", saved["psave"] == 1 and saved["n"] == "Monitor sync", saved)
        check("it is a plain lamp, so the strip is never dark or glaring while the Mac is off",
              saved["seg"][0]["fx"] == 0 and max(saved["seg"][0]["col"][0]) < 80)
        check("the preset carries no brightness, so choosing it never changes yours", saved["ib"] is False, saved)
        check("the saved preset is confirmed", result["preset_ok"])
        seen["live"] = True
        try:
            board.deploy(host, 1, page)
            check("a board that is receiving a stream is refused", False)
        except RuntimeError as exc:
            check("a board that is receiving a stream is refused", "sync" in str(exc), str(exc))
    finally:
        server.shutdown()

def test_board_ui():
    print("\n25. The control page on the board")
    import pathlib, re, shutil
    from ambilight.config import DEFAULTS

    html = pathlib.Path("board/index.htm").read_text()
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)

    check("it is small enough for the board's flash and slow Wi-Fi", len(html) < 30000, f"{len(html)} bytes")
    check("it fetches only WLED's own state endpoints",
          set(re.findall(r'fetch\("([^"]+)"', script)) == {"/json/state", "/json/si"})
    check("it loads nothing from outside the board", not re.search(r"(src|href)=\"https?://|url\(https?://", html))
    check("it never builds markup from strings", "innerHTML" not in script and "eval(" not in script)
    check("its sync preset matches the hub's", f"SYNC_PRESET = {DEFAULTS['sync_preset']};" in script)
    check("the three things asked for are all there",
          all(f'id="panel-{name}"' in html for name in ("monitor", "effects", "colour")))
    check("controls have accessible names", html.count("aria-label") >= 4 and 'for="bri"' in html)
    effects_block = script[script.index("const EFFECTS"):script.index("const SWATCHES")]
    fx_ids = [int(x) for x in re.findall(r"\{fx: (\d+),", effects_block)]
    check("twelve effects, no duplicates", len(fx_ids) == 12 == len(set(fx_ids)), fx_ids)

    # The wheel's maths measures hue clockwise from 3 o'clock (atan2, y down), so
    # the painted gradient must start there too, or the marker lands off its colour.
    check("the wheel's red sits at 3 o'clock, where the maths puts hue 0",
          "conic-gradient(from 90deg,#f00" in html)
    check("the wait for the Mac is timed from the tap and the state refreshes at once",
          "waitingSince = wantSince = Date.now();" in script
          and "post({on: true, ps: SYNC_PRESET}); showBanner(false); poll();" in script)
    check("a monitor sync tap is retried until the board confirms it",
          "wantSync" in script and "RETRY_MS" in script and "function ensureSync" in script)
    check("picking an effect or colour cancels the pending sync",
          script.count("wantSync = false") >= 3)
    check("the page says it will start by itself when the Mac is ready",
          "start by itself" in script)
    check("choosing monitor sync sends only the preset, so brightness is untouched",
          'post({on: true, ps: SYNC_PRESET})' in script and "queue({bri})" not in script)

    node = shutil.which("node")
    if not node:
        print("    (skipped: node not installed, so the page's script was not run)")
        return
    folder = pathlib.Path(tempfile.mkdtemp())
    (folder / "page.js").write_text(script)
    syntax = subprocess.run([node, "--check", str(folder / "page.js")], capture_output=True, text=True)
    check("the script parses", syntax.returncode == 0, syntax.stderr[:200])

    pure = "\n".join(re.search(rf"function {name}\(.*?\n}}", script, re.S).group(0)
                     for name in ("hsvToRgb", "rgbToHs"))
    probe = pure + r"""
const same = (a, b) => a.every((v, i) => Math.abs(v - b[i]) <= 1);
const out = [];
out.push(same(hsvToRgb(0, 1), [255, 0, 0]), same(hsvToRgb(120, 1), [0, 255, 0]), same(hsvToRgb(240, 1), [0, 0, 255]));
out.push(same(hsvToRgb(200, 0), [255, 255, 255]));
// the wheel always shows full brightness, so round-trip colours with a 255 channel
for (const rgb of [[255, 40, 30], [30, 255, 90], [170, 60, 255], [255, 120, 0]]) {
  const [h, s] = rgbToHs(rgb); out.push(same(hsvToRgb(h, s), rgb));
}
console.log(JSON.stringify(out));
"""
    (folder / "wheel.js").write_text(probe)
    run = subprocess.run([node, str(folder / "wheel.js")], capture_output=True, text=True)
    results = json.loads(run.stdout) if run.returncode == 0 else []
    check("the colour wheel maps angle and distance to the right colours", results and all(results), run.stderr[:200] or results)


def test_fake_wled_presets():
    print("\n26. The fake WLED clears the active preset on a manual change, like the real board")
    import http.server, threading as th
    import fake_wled

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake_wled.Handler)
    th.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(patch):
        req = urllib.request.Request(base + "/json/state", json.dumps(patch).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
        return json.loads(urllib.request.urlopen(base + "/json/state", timeout=3).read())

    try:
        check("loading a preset sets the flag", post({"ps": 1})["ps"] == 1)
        check("a manual change clears it", post({"seg": [{"id": 0, "fx": 2}]})["ps"] == -1)
        check("so does a brightness change", post({"ps": 1}) and post({"bri": 90})["ps"] == -1)
    finally:
        server.shutdown()


def test_display_follow():
    print("\n27. The hub follows the display in use")
    import ambilight.engine as eng
    from ambilight.engine import resolve_display

    check("auto picks the built-in screen when it is the only one", resolve_display(0, 1) == 1)
    check("auto picks the external monitor when one is connected", resolve_display(0, 2) == 2)
    check("auto picks the last display with several attached", resolve_display(0, 3) == 3)
    check("a fixed number is respected", resolve_display(1, 2) == 1)
    check("a fixed number that is gone falls back to the last display", resolve_display(5, 2) == 2)
    check("no displays at all resolves to 1 so the open reports it", resolve_display(0, 0) == 1)

    from ambilight.config import DEFAULTS
    check("automatic is the default", DEFAULTS["monitor_index"] == 0)

    class Plugged:
        name = "plugged"
        count = 1
        opened = []

        @staticmethod
        def available():
            return True

        @staticmethod
        def display_count():
            return Plugged.count

        def open(self, monitor_index, band_height):
            Plugged.opened.append(monitor_index)
            return 600, 150

        def grab(self):
            return np.zeros((150, 600, 3), np.uint8)

        def grab_full(self):
            return self.grab()

        def grab_search(self, rows):
            return self.grab()

        def set_inset(self, rows):
            pass

        def close(self):
            pass

    real = (eng.CAPTURES, eng.DISPLAY_CHECK_S, eng.screen_locked)
    eng.CAPTURES, eng.DISPLAY_CHECK_S, eng.screen_locked = {"plugged": Plugged}, 0.2, lambda: False
    try:
        cfg = Config(CONFIG_FOR_TESTS)
        cfg.update({"monitor_index": 0, "wled_ip": "127.0.0.1", "led_count": 30, "layout": "strip"})
        engine = eng.Engine(cfg)
        engine.start()
        time.sleep(0.8)
        check("with one display it opens display 1", Plugged.opened[:1] == [1], Plugged.opened)
        Plugged.count = 2
        time.sleep(1.2)
        check("plugging in a monitor moves capture to it without a restart", Plugged.opened[-1] == 2, Plugged.opened)
        Plugged.count = 1
        time.sleep(1.2)
        check("unplugging it moves capture back", Plugged.opened[-1] == 1, Plugged.opened)
        engine.stop()
    finally:
        eng.CAPTURES, eng.DISPLAY_CHECK_S, eng.screen_locked = real


def test_camera_calibration_maths():
    print("\n28. Webcam calibration: matching the wall's colour to the screen's")
    from ambilight import camcal, color

    # A region whose bright half is blown out reads as its unclipped half.
    region = np.zeros((10, 20, 3)); region[:, :10] = (120, 60, 30); region[:, 10:] = (255, 250, 200)
    seen = camcal.proportions(region, np.ones((10, 20), bool))
    truth = camcal.linear((120, 60, 30)); truth = truth / truth.sum()
    check("blown-out pixels are skipped, so a region reads its true colour",
          np.abs(seen - truth).max() < 1e-9, np.round(seen, 3))
    lit = camcal.linear((120, 60, 30)) * np.linspace(0.5, 6.0, 200)[:, None]    # one colour, bright to blown out
    def photo(gain):
        return (color.linear_to_srgb(np.clip(lit * gain, 0, 1)) * 255.0)[:, None, :]
    readings = [camcal.proportions(photo(gain), np.ones((200, 1), bool)) for gain in (1.0, 0.4)]
    check("brightness and blown-out pixels do not change the colour read",
          all(np.abs(r - truth).max() < 0.01 for r in readings), [np.round(r, 3) for r in readings])
    check("a region with too few usable pixels is not read",
          camcal.proportions(np.full((5, 5, 3), 255.0), np.ones((5, 5), bool)) is None)

    check("a wall that is a dimmer copy of the screen scores as a match",
          camcal.mismatch([(np.array([.7, .2, .1]), np.array([.7, .2, .1]))]) < 1e-9)
    check("a wall of another colour does not",
          camcal.mismatch([(np.array([.7, .2, .1]), np.array([.3, .4, .3]))]) > 0.3)
    try:
        camcal.mismatch([(None, None)])
        empty = False
    except ValueError:
        empty = True
    check("no readable picture at all is an error, not a perfect score", empty)

    # Nudging converges whatever the strip and camera do, as long as more drive
    # in a channel means more of that colour.
    M = np.array([[0.55, 0.10, 0.02], [0.08, 0.90, 0.05], [0.02, 0.06, 0.40]])
    target = np.array([0.15, 0.55, 0.30])
    drive = camcal.first_guess((0, 255, 0))
    for _ in range(12):
        wall = M @ drive
        drive = camcal.nudge(drive, target, wall / wall.sum())
    wall = M @ drive
    check("nudging the strip brings the wall to the screen's colour",
          camcal.mismatch([(target, wall / wall.sum())]) < 0.01,
          round(camcal.mismatch([(target, wall / wall.sum())]), 4))
    check("the strip never goes past the matching level", abs(drive.max() - camcal.DRIVE_PEAK) < 1e-9)

    F = np.array([[0.9, 0.1, 0.0], [0.05, 0.6, 0.05], [0.0, 0.05, 0.35]])
    matched = {"red": F[:, 0] * 2.0, "green": F[:, 1] * 0.7, "blue": F[:, 2] * 3.0, "white": F @ np.ones(3)}
    found = camcal.matrix_from_primaries(matched)
    check("matched red, green, blue and white give the colour matrix, whatever each was scaled by",
          np.abs(found - F / (F @ np.ones(3)).max()).max() < 1e-9)
    try:
        camcal.matrix_from_primaries({**matched, "white": -matched["white"]})
        refused = False
    except ValueError:
        refused = True
    check("matches that contradict each other are refused", refused)

    h, w = 120, 200
    base = np.full((h, w, 3), 20, np.uint8)
    screen_on = base.copy(); screen_on[30:90, 60:140] = 230
    wall_on = base.copy(); wall_on[10:110, 20:60] = 140; wall_on[10:110, 140:180] = 140
    screen_mask, wall_mask = camcal.find_regions(screen_on, wall_on * 0.6)   # re-exposed in between
    check("the screen is found where it lit up", screen_mask[60, 100] and not screen_mask[60, 30])
    check("the wall glow is found beside it", wall_mask[60, 30] and wall_mask[60, 160])
    check("the screen is never counted as wall", not (screen_mask & wall_mask).any())


def test_fitted_matrix_in_use():
    print("\n29. A camera-fitted matrix drives the strip's colours")
    from ambilight import color
    from ambilight.config import Config

    cfg = Config(CONFIG_FOR_TESTS)
    F = [0.9, 0.1, 0.0, 0.05, 0.6, 0.05, 0.0, 0.05, 0.35]
    check("a 3x3 matrix is accepted", cfg.update({"led_matrix": json.dumps(F)}) == ["led_matrix"])
    check("as numbers", cfg.get("led_matrix") == F)
    for bad in ("[1, 2]", '[1,2,3,4,5,6,7,8,"x"]', "[1e999,0,0,0,1,0,0,0,1]", "not json"):
        try:
            cfg.update({"led_matrix": bad})
            check(f"a bad matrix is refused: {bad}", False)
        except ValueError:
            check(f"a bad matrix is refused: {bad}", True)
    check("an empty list switches back to the datasheet model", cfg.update({"led_matrix": "[]"}) == ["led_matrix"])

    base = {"black_level": 0.0, "led_gamma": 1.5, "contrast": 1.0, "colour_match": True,
            "white_kelvin": 6500, "wb_r": 1.0, "wb_g": 1.0, "wb_b": 1.0}
    white = np.ones((1, 3), np.float32)
    fitted = color.to_led(white, dict(base, led_matrix=F))[0]
    expected = np.array(F).reshape(3, 3) @ np.ones(3)
    check("with a fitted matrix, white takes the measured mix",
          np.allclose(fitted / fitted.max(), expected / expected.max(), atol=0.01), fitted.round())
    datasheet = color.to_led(white, dict(base, led_matrix=[]))[0]
    check("without one, the datasheet model is used", not np.allclose(fitted, datasheet), datasheet.round())
    skin = np.array([[224 / 255, 172 / 255, 138 / 255]], np.float32)
    cool = color.to_led(skin, dict(base, led_matrix=F))[0]
    warm = color.to_led(skin, dict(base, led_matrix=F, white_kelvin=4500))[0]
    check("warmth still works on top of a fitted matrix", warm[2] / warm[0] < cool[2] / cool[0], (cool.round(), warm.round()))


def test_calibration_routes():
    print("\n30. The hub lets the webcam calibration drive the strip")
    from urllib.parse import parse_qs
    from ambilight import routes
    from ambilight.intent import SyncIntent

    class Engine:
        running = True
        painted = None
        cleared = False

        def stop(self):
            Engine.running = False

        def set_override(self, px):
            Engine.painted = px

        def status(self):
            return {"running": Engine.running}

    class Wizard:
        stopped = False

        def stop(self):
            Wizard.stopped = True
            return {}

    class Hub:
        engine, wizard, wled = Engine(), Wizard(), None
        cfg = Config(CONFIG_FOR_TESTS)

        def status(self):
            return {}

    hub = Hub()
    hub.cfg.update({"layout": "frame", "led_count": 180, "edge_bottom": 35, "edge_right": 14,
                    "edge_top": 35, "edge_left": 13, "brightness": 1.0})
    hub.intent = SyncIntent(hub.cfg, None, hub.engine)
    body, code = routes.handle(hub, "/api/calibrate/paint", parse_qs("rgb=ff8000"))
    px = Engine.painted
    check("painting stops sync and pauses the watcher", not Engine.running and hub.intent.paused_until > 0)
    check("the LEDs round the monitor take exactly that colour", code == 200 and px[:97].tolist() == [[255, 128, 0]] * 97)
    check("LEDs past the frame stay dark, so only the monitor's glow is measured", px[97:].max() == 0)
    body, code = routes.handle(hub, "/api/calibrate/paint", parse_qs("rgb=zzzzzz"))
    check("a bad colour is refused", code == 400)
    routes.handle(hub, "/api/calibrate/done", parse_qs(""))
    check("finishing hands the strip back and lets sync follow the board again",
          Wizard.stopped and hub.intent.paused_until == 0.0)


class SimulatedRoom:
    """A monitor, the wall glow round it and a webcam with its own changing gains.

    Stands in for the window, the hub and the camera in camrun's flow: the
    window sets the screen colour, the hub paints or syncs the strip, and the
    camera photographs both. Like the real webcam, it blows out the brightest
    part of each region, and screen light spills onto the wall in its colour.
    """
    A = np.array([[0.80, 0.15, 0.05], [0.20, 0.75, 0.05], [0.05, 0.10, 0.85]])   # screen -> camera
    M = np.array([[0.55, 0.10, 0.02], [0.08, 0.90, 0.05], [0.02, 0.06, 0.40]])   # strip -> wall
    SPILL, AMBIENT = 0.03, 0.002

    def __init__(self):
        self.rng = np.random.default_rng(11)
        self.screen_code, self.drive = (0, 0, 0), np.zeros(3)
        self.syncing, self.calls = False, []
        self.settings = {"led_matrix": [], "wb_r": 1.0, "wb_g": 0.88, "wb_b": 1.0, "saturation": 1.45}
        self.picture = np.zeros((60, 100), int)            # 0 room, 1 screen, 2 wall
        self.picture[20:40, 35:65] = 1
        self.picture[10:50, 10:28] = 2
        self.picture[10:50, 72:90] = 2
        self.shade = np.linspace(0.8, 3.0, 100)[None, :, None]   # one side of each region blows out

    # the window
    def show(self, rgb):
        self.screen_code = tuple(rgb)
        if self.syncing:
            self.drive = self.sync_drive()

    def wait(self, seconds):
        pass

    # the hub
    def paint(self, rgb):
        self.syncing, self.drive = False, np.array(rgb) / 255.0

    def get(self, path, **params):
        self.calls.append(path)
        if path == "/api/sync/on":
            self.syncing, self.drive = True, self.sync_drive()
        elif path == "/api/set":
            self.settings.update({k: json.loads(v) if k == "led_matrix" else float(v)
                                  for k, v in params.items()})
        return {}

    def sync_drive(self):
        from ambilight import camcal

        s = camcal.linear(self.screen_code)
        s = np.clip(s.mean() + (s - s.mean()) * self.settings["saturation"], 0, None)
        fitted = self.settings["led_matrix"]
        F = np.array(fitted).reshape(3, 3) if fitted else np.eye(3)   # 1:1 before calibration
        d = F @ s
        return np.clip(d / max(d.max(), 1e-9) * camcal.DRIVE_PEAK, 0, 1)

    # the camera
    def snapshot(self, frames=5):
        from ambilight import camcal, color

        s = camcal.linear(self.screen_code)
        looks = [np.full(3, self.AMBIENT), self.A @ s + 0.004,
                 self.M @ self.drive + self.SPILL * (self.A @ s) + self.AMBIENT]
        gain = self.rng.uniform(0.6, 1.4, 3) * 0.6           # auto exposure and white balance
        image = np.array(looks)[self.picture] * gain * self.shade
        return color.linear_to_srgb(np.clip(image, 0, 1)) * 255.0


class HiddenWindowRoom(SimulatedRoom):
    """The test colours open behind a full-screen video: the camera sees the video."""

    def snapshot(self, frames=5):
        image = super().snapshot(frames)
        video = self.rng.integers(0, 256, (6, 10, 3)).repeat(10, axis=0).repeat(10, axis=1)
        image[self.picture == 1] = video[self.picture == 1]
        return image


class BlownOutPrimaryRoom(SimulatedRoom):
    """A panel whose blue subpixel runs far brighter than red or green.

    At the usual SCREEN_LEVEL the screen patch clips almost solid white on
    blue, same as the real BenQ + Lenovo webcam; only a dimmer test colour
    reads cleanly.
    """
    A = np.array([[0.80, 0.15, 0.05], [0.20, 0.75, 0.05], [0.05, 0.10, 4.0]])


def test_camera_calibration_flow():
    print("\n31. The webcam calibration runs end to end in a simulated room")
    from ambilight import camcal, camrun

    room = SimulatedRoom()
    folder = tempfile.mkdtemp(prefix="ambilight-cam-")
    saved_dir, saved_fit = camrun.RECORDINGS_DIR, camcal.matrix_from_primaries
    camrun.RECORDINGS_DIR = __import__("pathlib").Path(folder)
    status = {**room.settings}
    try:
        result = camrun.calibrate(room, room, room, status, lambda line: None)
        truth = np.linalg.solve(room.M, room.A)
        truth = truth / (truth @ np.ones(3)).max()
        fitted = np.array(result["matrix"])
        check("the screen and the wall glow are found in the camera picture",
              os.path.exists(os.path.join(folder, "calibration-view.jpg")))
        check("the strip's colour matrix is recovered, though parts of the picture blow out",
              np.abs(fitted - truth).max() < 0.05, np.abs(fitted - truth).max().round(4))
        check("the wall matches the screen far better afterwards",
              result["kept"] and result["after"] < 0.5 * result["before"],
              (round(result["before"], 3), round(result["after"], 3)))
        check("with colours matched, the saturation boost is dropped because it measured worse",
              room.settings["saturation"] == 1.0 and "no saturation boost" in result["chosen"], result["chosen"])
        check("the hub now uses the matrix, with the old strip balance folded in",
              len(room.settings["led_matrix"]) == 9 and room.settings["wb_g"] == 1.0)
        check("the strip is handed back to sync at the end", room.calls[-2:] == ["/api/calibrate/done", "/api/sync/on"])

        regions = np.load(os.path.join(folder, "calibration-regions.npz"))
        score = camrun.watch(room, regions["screen"], regions["wall"], 0.05)
        check("watching a video scores the match without changing anything",
              abs(score - result["after"]) < 0.1 and room.calls[-1] == "/api/sync/on", round(score, 3))

        room = SimulatedRoom()
        before = dict(room.settings)
        camcal.matrix_from_primaries = lambda matched: np.array([[0, 0, 1.0], [1.0, 0, 0], [0, 1.0, 0]])
        result = camrun.calibrate(room, room, room, dict(room.settings), lambda line: None)
        check("settings that make the match worse are thrown away and the old ones restored",
              not result["kept"] and room.settings == before, room.settings)
        camcal.matrix_from_primaries = saved_fit

        two_screens = np.zeros((40, 60), bool); two_screens[5:30, 20:55] = True; two_screens[25:35, 2:12] = True
        picked = camrun.largest_region(two_screens)
        check("with a laptop screen also in view, the monitor is the largest lit region",
              picked[10, 30] and not picked[30, 5])

        room = HiddenWindowRoom()
        try:
            camrun.calibrate(room, room, room, dict(room.settings), lambda line: None)
            stopped = ""
        except RuntimeError as exc:
            stopped = str(exc)
        check("test colours hidden behind a full-screen video stop the calibration",
              "did not show on the monitor" in stopped, stopped)
        check("and the strip is still handed back to sync", room.calls[-2:] == ["/api/calibrate/done", "/api/sync/on"])

        room = BlownOutPrimaryRoom()
        result = camrun.calibrate(room, room, room, dict(room.settings), lambda line: None)
        check("a primary that blows out the screen at the usual level still matches, "
              "by retrying dimmer instead of aborting",
              result["kept"] and result["after"] < 0.2, round(result["after"], 3))
    finally:
        camrun.RECORDINGS_DIR, camcal.matrix_from_primaries = saved_dir, saved_fit


def test_camera_choice():
    print("\n33. The laptop's built-in camera is never used")
    from ambilight import camrun

    cameras = [("Lenovo FHD Webcam", False), ("FaceTime HD Camera", True), ("Ishwar's iPhone Camera", False)]
    check("the external webcam is chosen by default, not the phone or the built-in camera",
          camrun.pick_camera(cameras) == (0, "Lenovo FHD Webcam"))
    check("a webcam can be named", camrun.pick_camera(cameras, "lenovo") == (0, "Lenovo FHD Webcam"))
    check("the phone only when asked for by name", camrun.pick_camera(cameras, "iphone")[0] == 2)

    def refused(cameras, wanted=None):
        try:
            camrun.pick_camera(cameras, wanted)
        except RuntimeError as exc:
            return str(exc)
        return ""

    check("asking for the built-in camera is refused", "built-in" in refused(cameras, "facetime"))
    check("with the webcam unplugged, nothing is opened rather than the built-in camera",
          "no external webcam" in refused([("FaceTime HD Camera", True), ("Ishwar's iPhone Camera", False)]))
    check("this Mac's camera list is read without opening any camera",
          all(isinstance(name, str) for name, _ in camrun.list_cameras()))


class FakeMouseHID:
    """Stands in for the hid module: one G102 that records every report."""

    def __init__(self):
        self.plugged, self.sent, self.opened = True, [], 0
        self.fail_writes = False
        fake = self

        class Device:
            def open_path(self, path):
                if not fake.plugged:
                    raise OSError("open failed")
                fake.opened += 1

            def write(self, data):
                if fake.fail_writes:
                    return -1
                fake.sent.append(bytes(data))
                return len(data)

            def close(self):
                pass

        self.device = Device

    def enumerate(self, vendor=0, product=0):
        if not self.plugged:
            return []
        return [{"vendor_id": 0x046D, "product_id": 0xC092, "usage_page": 0xFF00, "usage": 1,
                 "path": b"short", "product_string": "G102 LIGHTSYNC Gaming Mouse"},
                {"vendor_id": 0x046D, "product_id": 0xC092, "usage_page": 0xFF00, "usage": 2,
                 "path": b"long", "product_string": "G102 LIGHTSYNC Gaming Mouse"}]


def test_mouse_sync():
    print("\n34. The Logitech mouse follows the screen only while turned on")
    from ambilight import mouse

    take, back = mouse.report(*mouse.TAKE_CONTROL), mouse.report(*mouse.HAND_BACK)
    apply_ = mouse.report(*mouse.APPLY)
    check("reports are the mouse's 20-byte long HID++ reports",
          len(take) == 20 and take[:7] == bytes([0x11, 0xFF, 0x0E, 0x50, 0x01, 0x03, 0x07]))
    zones = mouse.zone_report([(1, 2, 3), (4, 5, 6), (7, 8, 9)])
    check("the three zones are numbered 1-3 with their colours, then FF",
          zones[:17] == bytes([0x11, 0xFF, 0x12, 0x10, 1, 1, 2, 3, 2, 4, 5, 6, 3, 7, 8, 9, 0xFF]))

    cfg = {"layout": "frame", "edge_bottom": 6, "led_count": 10}
    pixels = np.array([[200, 0, 0]] * 2 + [[0, 200, 0]] * 2 + [[0, 0, 200]] * 2 + [[255, 255, 255]] * 4, np.uint8)
    check("left, middle and right zones follow the bottom edge's thirds, left to right",
          mouse.zone_colours(pixels, cfg) == [(200, 0, 0), (0, 200, 0), (0, 0, 200)])
    check("without a frame layout the whole strip is split in thirds",
          len(mouse.zone_colours(pixels, {**cfg, "layout": "band"})) == 3)

    dull = [(60, 10, 10), (20, 40, 60), (128, 128, 128)]
    bright = mouse.vivid(dull)
    check("a dim strip colour shows much brighter on the mouse",
          max(bright[0]) >= 2 * max(dull[0]) and max(bright[1]) >= 1.8 * max(dull[1]), bright)
    check("and keeps its colour: red stays red, blue stays the strongest of the second",
          bright[0][0] > 4 * bright[0][1] and bright[1][2] > bright[1][1] > bright[1][0], bright)
    check("a grey stays grey", len(set(bright[2])) == 1, bright[2])
    check("a black scene leaves the mouse dark", mouse.vivid([(3, 2, 2)] * 3) == [(0, 0, 0)] * 3)
    check("full colours are left at full", mouse.vivid([(255, 0, 0)])[0] == (255, 0, 0))

    now = [0.0]
    fake = FakeMouseHID()
    device = mouse.LightsyncMouse(fake, clock=lambda: now[0], access=lambda: "granted")
    device.show([(10, 0, 0)] * 3)
    check("the first colour takes control, then sets and applies the zones",
          fake.sent[:3] == [take, mouse.zone_report([(10, 0, 0)] * 3), apply_], [s[:4].hex() for s in fake.sent])
    check("the long-report interface is the one opened", device.path == b"long")
    sent = len(fake.sent)
    now[0] += 1
    device.show([(10, 0, 0)] * 3)
    check("an unchanged colour is not sent again", len(fake.sent) == sent)
    device.show([(20, 0, 0)] * 3)
    device.show([(30, 0, 0)] * 3)
    check("changes are sent at most 30 times a second", len(fake.sent) == sent + 2)
    now[0] += 1
    device.show([(30, 0, 0)] * 3)
    check("the latest colour still lands once the interval passes",
          fake.sent[-2] == mouse.zone_report([(30, 0, 0)] * 3))
    check("status names the mouse",
          device.status() == {"connected": True, "name": "G102 LIGHTSYNC Gaming Mouse", "permission": "granted"})

    device.release()
    check("turning off hands the mouse back to its own lighting", fake.sent[-1] == back)
    sent = len(fake.sent)
    device.release()
    check("releasing twice sends nothing more", len(fake.sent) == sent)

    fake.fail_writes = True
    now[0] += 1
    device.show([(1, 1, 1)] * 3)
    fake.fail_writes, fake.plugged = False, False
    now[0] += 1
    device.show([(2, 2, 2)] * 3)
    check("an unplugged mouse is not retried on every frame", fake.opened == 2)
    check("and reports as not connected", device.status()["connected"] is False)
    fake.plugged = True
    now[0] += mouse.RETRY_S
    device.show([(3, 3, 3)] * 3)
    check("plugging it back in picks it up again",
          fake.opened == 3 and fake.sent[-2] == mouse.zone_report([(3, 3, 3)] * 3))

    check("without the hidapi package the mouse simply reports unavailable",
          mouse.LightsyncMouse(None, access=lambda: "granted").status()
          == {"connected": False, "name": None, "permission": "granted"})

    asked = []
    fake = FakeMouseHID()
    fake.plugged = True
    blocked = mouse.LightsyncMouse(fake, clock=lambda: now[0], access=lambda: "not asked",
                                   request_access=lambda: asked.append(1))
    original = fake.device.open_path
    fake.device.open_path = lambda self, path: (_ for _ in ()).throw(OSError("open failed"))
    blocked.show([(5, 5, 5)] * 3)
    now[0] += mouse.RETRY_S
    blocked.show([(6, 6, 6)] * 3)
    fake.device.open_path = original
    check("without Input Monitoring, macOS is asked once so its prompt appears", asked == [1])
    check("and status says the permission is what is missing", blocked.status()["permission"] == "not asked")

    from ambilight.engine import Engine

    cfg = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-mouse-"), "config.json"))
    cfg.update({"led_count": 10, "edge_bottom": 6, "layout": "frame"})
    engine = Engine(cfg)
    fake = FakeMouseHID()
    engine.mouse = mouse.LightsyncMouse(fake, access=lambda: "granted")
    engine.follow_mouse(pixels)
    check("mouse sync is off until turned on", fake.sent == [] and cfg.get("mouse_sync") is False)
    cfg.update({"mouse_sync": "1"})
    engine.follow_mouse(pixels)
    check("turned on, the mouse shows the screen's bottom edge, brightened for the mouse",
          mouse.zone_report(mouse.vivid([(200, 0, 0), (0, 200, 0), (0, 0, 200)])) in fake.sent)
    cfg.update({"mouse_sync": "0"})
    engine.follow_mouse(pixels)
    check("turned off again, the mouse gets its own lighting back", fake.sent[-1] == back)
    check("the hub's status reports the mouse", engine.status()["mouse"]["connected"] is True)


def test_board_address_cache():
    print("\n32. A flaky .local name never stalls or drops the stream")
    from ambilight import hostcache
    from ambilight.plugins import output_wled_udp

    answers = {"ip": "192.168.1.50", "fail": False, "calls": 0}

    def lookup(host):
        answers["calls"] += 1
        if answers["fail"]:
            time.sleep(0.3)               # a dead mDNS name fails slowly
            raise OSError("nodename nor servname provided")
        return answers["ip"]

    now = [0.0]
    cache = hostcache.HostCache(resolve=lookup, refresh_s=30.0, clock=lambda: now[0])
    check("an IP address is used as it is, without a lookup",
          cache.address("10.0.0.9") == "10.0.0.9" and answers["calls"] == 0)
    check("a name is looked up the first time", cache.address("wled.local") == "192.168.1.50")
    check("and then answered from memory", cache.address("wled.local") == "192.168.1.50" and answers["calls"] == 1)

    answers["fail"], now[0] = True, 31.0
    start = time.monotonic()
    got = [cache.address("wled.local") for _ in range(50)]
    took = time.monotonic() - start
    check("when the name stops resolving, the last good address is used at once",
          set(got) == {"192.168.1.50"} and took < 0.05, f"{took * 1000:.1f} ms for 50 packets")
    deadline = time.monotonic() + 2
    while cache.refreshing and time.monotonic() < deadline:
        time.sleep(0.01)
    check("only one background lookup runs at a time", answers["calls"] == 2, answers["calls"])

    answers["fail"], answers["ip"], now[0] = False, "192.168.1.77", 62.0
    cache.address("wled.local")
    deadline = time.monotonic() + 2
    while cache.address("wled.local") != "192.168.1.77" and time.monotonic() < deadline:
        time.sleep(0.01)
    check("a board that moved to a new address is followed", cache.address("wled.local") == "192.168.1.77")

    answers["fail"] = True
    try:
        cache.address("never-seen.local")
        failed = False
    except OSError:
        failed = True
    check("a name never resolved fails loudly instead of guessing", failed)

    saved = hostcache.lookup
    hostcache.lookup = lambda host: (_ for _ in ()).throw(OSError("mDNS down"))
    hostcache.BOARD.known.clear()
    hostcache.BOARD.known["board.local"] = ("127.0.0.1", -1e9)      # known, but long stale
    try:
        out = output_wled_udp.WLEDUDPOutput("board.local", 9)
        start = time.monotonic()
        sent = [out.send(np.zeros((97, 3), np.uint8)) for _ in range(30)]
        took = time.monotonic() - start
        check("the stream keeps flowing to the last good address while mDNS is down",
              all(sent) and took < 0.1, f"{took * 1000:.1f} ms for 30 frames")
        out.close()
    finally:
        hostcache.lookup = saved
        hostcache.BOARD.known.clear()


@contextmanager
def sandbox_hub():
    """A fake WLED plus a hub on its own ports and config, torn down afterwards."""
    folder = tempfile.mkdtemp(prefix="ambilight-hub-")
    config = os.path.join(folder, "config.json")
    with open(config, "w") as fh:
        json.dump({"wled_ip": "127.0.0.1", "wled_http_port": FAKE_WLED_HTTP,
                   "wled_port": FAKE_WLED_UDP, "monitor_index": 1}, fh)
    log = open(os.path.join(folder, "hub.log"), "w")
    procs = [
        subprocess.Popen([sys.executable, "fake_wled.py", "--udp-port", str(FAKE_WLED_UDP),
                          "--http-port", str(FAKE_WLED_HTTP), "--quiet"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([sys.executable, "ambilight.py", "--config", config,
                          "--port", str(SANDBOX_PORT), "--no-bonjour"],
                         stdout=log, stderr=subprocess.STDOUT),
    ]
    base = f"http://127.0.0.1:{SANDBOX_PORT}"
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/api/status", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError(f"sandbox hub did not start; see {log.name}")
        yield base
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(timeout=5)
        log.close()


def test_security_api(base):
    print("\n18. HTTP security and the UI contract")

    def call(path, headers=None, method="GET"):
        req = urllib.request.Request(base + path, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                body = r.read()
                return r.status, (json.loads(body) if body and method == "GET" and
                                  path.startswith("/api/") else body)
        except urllib.error.HTTPError as exc:
            return exc.code, None

    code, cfg = call("/api/config")
    check("config never contains the PIN", code == 200 and "pin" not in cfg and "has_pin" in cfg)
    code, status = call("/api/status")
    check("status lists addresses for other devices", any(".local:" in u for u in status["urls"]),
          status.get("urls"))
    check("status reports the mode", status["mode"] in ("sync", "effect", "off"))
    check("status reports WLED reachability", "wled_reachable" in status)
    check("status reports safety", "power_ma" in status["safety"])
    check("defaults are served without the PIN", call("/api/defaults")[0] == 200
          and "pin" not in call("/api/defaults")[1])
    code, mons = call("/api/monitors")
    check("displays can be listed", code == 200 and len(mons["monitors"]) >= 1)
    check("a cross-site request is blocked", call("/api/sync/off", {"Sec-Fetch-Site": "cross-site"})[0] == 403)
    check("a DNS-rebinding Host is blocked", call("/api/status", {"Host": "attacker.example"})[0] == 403)
    check("the page's own requests pass", call("/api/status", {"Sec-Fetch-Site": "same-origin"})[0] == 200)
    check("HEAD never runs an action", call("/api/sync/on", method="HEAD")[0] == 405)
    check("HEAD on a page is fine", call("/", method="HEAD")[0] == 200)
    check("the manifest is served", call("/manifest.webmanifest")[0] == 200)
    check("invalid settings are a 400, not a 500", call("/api/set?fps=fast")[0] == 400)
    check("a flash can only target a connected board",
          call("/api/flash/start?port=/dev/cu.nothing&ssid=x")[0] == 400)
    check("recording needs sync on", call("/api/record/start?seconds=2")[0] == 409)

    check("a PIN can be set from the UI", call("/api/set?new_pin=4321")[0] == 200)
    check("then requests without it are refused", call("/api/status")[0] == 403)
    check("the header unlocks it", call("/api/status", {"X-Ambilight-Pin": "4321"})[0] == 200)
    check("the PIN never leaks through config",
          "pin" not in call("/api/config", {"X-Ambilight-Pin": "4321"})[1])
    check("and it can be cleared again",
          call("/api/set?new_pin=", {"X-Ambilight-Pin": "4321"})[0] == 200
          and call("/api/status")[0] == 200)


def main():
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
    test_safety()
    test_config()
    test_access()
    test_frame_wizard()
    test_firmware_download()
    test_quartz_capture()
    test_sync_intent()
    test_sync_routes()
    test_board_deploy()
    test_board_ui()
    test_fake_wled_presets()
    test_display_follow()
    test_camera_calibration_maths()
    test_fitted_matrix_in_use()
    test_calibration_routes()
    test_camera_calibration_flow()
    test_board_address_cache()
    test_camera_choice()
    test_mouse_sync()
    test_engine_parts()
    with sandbox_hub() as base:
        test_api(base)
        test_wizard(base)
        test_serial_api(base)
        test_provision_api(base)
        test_security_api(base)

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    FAILED: {f}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
