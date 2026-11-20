#!/usr/bin/env python3
"""Acceptance tests.

    python3 tests/test_hub.py

Starts its own fake WLED and a sandbox hub on ports 8790-8791 with a throwaway
config, so it can never touch a live hub on 8080 or real LEDs. The HTTP part
captures the real screen, so run it with the display awake and unlocked.

    AMBILIGHT_TEST_PORT_BASE=8800 python3 tests/test_hub.py

moves every port the run binds (8800-8801, UDP 21401, 21402 and 21409), so parallel
runs don't collide. Give each parallel run its own base, 10 apart.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)  # the checks read repo files and start scripts by relative path
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
CONFIG_FOR_TESTS = os.path.join(tempfile.mkdtemp(prefix="ambilight-tests-"), "config.json")
PORT_BASE = int(os.environ.get("AMBILIGHT_TEST_PORT_BASE", "8790"))
if PORT_BASE in (8079, 8080):
    sys.exit("AMBILIGHT_TEST_PORT_BASE would land on the live hub's port 8080")
SANDBOX_PORT, FAKE_WLED_HTTP, FAKE_WLED_UDP = PORT_BASE, PORT_BASE + 1, PORT_BASE + 12601
SNIFF_UDP = PORT_BASE + 12609   # 21399 by default; bases 10 apart never overlap
FAKE_WLED_DDP = PORT_BASE + 12602
SILENT_PORT = PORT_BASE + 2     # accepts connections and never answers
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
    PORT = SNIFF_UDP
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
    # A frame held with 255 latches on the board; only timeout 0 ends live mode.
    check("release frame ends live mode at once (timeout 0)", pkts and pkts[0][1] == 0,
          f"got {pkts[0][1] if pkts else 'none'}")
    check("release frame is black", pkts and tuple(pkts[0][4:7]) == (0, 0, 0))
    out.close()


def test_ddp_packets():
    print("\n1b. DDP packet format (as WLED 16.0.1 parses it)")
    from ambilight import hostcache
    from ambilight.plugins import OUTPUTS
    from ambilight.plugins.output_wled_ddp import MAX_LEDS_PER_PACKET as DDP_CHUNK, WLEDDDPOutput

    check("DDP is a registered output", OUTPUTS.get("ddp") is WLEDDDPOutput and WLEDDDPOutput.available())
    out = WLEDDDPOutput("127.0.0.1", SNIFF_UDP, SNIFF_UDP)

    def fields(p):
        return {"flags": p[0], "seq": p[1], "type": p[2], "dest": p[3],
                "offset": int.from_bytes(p[4:8], "big"), "length": int.from_bytes(p[8:10], "big"),
                "body": p[10:]}

    px = np.zeros((240, 3), np.uint8)
    px[0] = (255, 0, 0)
    pkts = [fields(p) for p in sniff(SNIFF_UDP, 1, lambda: out.send(px, 2))]
    check("240 LEDs -> exactly 1 packet", len(pkts) == 1, f"got {len(pkts)}")
    if pkts:
        p = pkts[0]
        check("version 1 with push on a one-packet frame", p["flags"] == 0x41, hex(p["flags"]))
        check("data type is RGB24 (0x0B)", p["type"] == 0x0B)
        check("destination is the display (1)", p["dest"] == 1)
        check("offset 0, length 720 bytes", (p["offset"], p["length"]) == (0, 720))
        check("payload matches the length field", len(p["body"]) == p["length"])
        check("first LED RGB preserved", tuple(p["body"][:3]) == (255, 0, 0))

    big = np.full((1000, 3), 9, np.uint8)
    pkts = [fields(p) for p in sniff(SNIFF_UDP, 3, lambda: out.send(big, 2))]
    check("1000 LEDs -> 3 packets", len(pkts) == 3, f"got {len(pkts)}")
    check("chunks are 480/480/40 LEDs", [p["length"] // 3 for p in pkts] == [DDP_CHUNK, DDP_CHUNK, 40],
          [p["length"] // 3 for p in pkts])
    check("offsets count bytes: 0/1440/2880", [p["offset"] for p in pkts] == [0, 1440, 2880])
    check("push only on the frame's last packet", [p["flags"] & 1 for p in pkts] == [0, 0, 1])
    check("no packet exceeds WLED's 1440 data bytes", all(p["length"] <= 1440 for p in pkts))
    check("one sequence number for the whole frame", len({p["seq"] for p in pkts}) == 1)

    seqs = []
    for _ in range(16):
        got = sniff(SNIFF_UDP, 1, lambda: out.send(px, 2))
        seqs.append(got[0][1] if got else None)
    check("the sequence runs 1-15 and wraps, never 0",
          None not in seqs and 0 not in seqs and all(b == a % 15 + 1 for a, b in zip(seqs, seqs[1:])), seqs)

    pkts = sniff(SNIFF_UDP, 1, lambda: out.send(px, 255))
    check("a held frame goes out as DNRGB with timeout 255 (DDP cannot hold)",
          pkts and pkts[0][0] == 4 and pkts[0][1] == 255)
    pkts = sniff(SNIFF_UDP, 1, out.release)
    check("release goes out as DNRGB timeout 0, ending live mode at once",
          pkts and pkts[0][0] == 4 and pkts[0][1] == 0)
    out.close()

    saved = hostcache.lookup
    try:
        def no_dns(host):
            raise OSError("mDNS down")
        hostcache.lookup = no_dns
        dark = WLEDDDPOutput("board-missing.local", SNIFF_UDP, SNIFF_UDP)
        check("a failed send is reported, not raised",
              dark.send(px, 2) is False and "mDNS down" in (dark.last_error or ""), dark.last_error)
        dark.close()
    finally:
        hostcache.lookup = saved
        hostcache.BOARD.known.clear()


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
        grabs = 0
        scale = 1.0

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

        def _screen(self):
            if not FakeCapture.alive:
                raise OSError("monitor 1 not found; 0 attached")
            band = np.zeros((150, 600, 3), np.uint8)
            band[:, :300] = (200, 30, 30)
            return band

        def grab_full(self):
            FakeCapture.grabs += 1      # per-frame reads, in either layout; not letterbox searches
            return self._screen()

        def grab(self):
            return self.grab_full()

        def grab_search(self, rows):
            return self._screen()

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

        # Each grab costs macOS a full-screen composite, so capture is capped
        # below the output rate; the output stage glides between readings.
        sleep_cfg.update({"fps": 60})
        FakeCapture.grabs, started = 0, time.perf_counter()
        time.sleep(1.0)
        grab_rate = FakeCapture.grabs / (time.perf_counter() - started)
        check("capture is capped at the screen-reading rate, not the output rate",
              eng.CAPTURE_FPS_MAX - 6 <= grab_rate <= eng.CAPTURE_FPS_MAX + 3, f"{grab_rate:.0f} grabs/s")
        check("the lights still update at the full output rate",
              engine.output_fps > 45, f"{engine.output_fps:.0f} fps")

        # Each lock check is a round trip to WindowServer; once a second is plenty.
        lock_checks = []
        eng.screen_locked = lambda: lock_checks.append(1) and False
        time.sleep(2.0)
        check("the lock screen is checked about once a second, not every frame",
              1 <= len(lock_checks) <= 3, f"{len(lock_checks)} checks in 2 s")
        eng.screen_locked = lambda: False

        FakeCapture.alive = False
        time.sleep(1.2)
        check("it survives the display sleeping again", engine.running is True)
        engine.stop()

        # ScreenCaptureKit can refuse to start (it does for a process launched by
        # one that captured the screen); the next source must take over.
        class Refusing(FakeCapture):
            name = "refusing"

            def open(self, monitor_index, band_height):
                raise TimeoutError("ScreenCaptureKit did not answer while starting the stream")

        FakeCapture.alive = True
        eng.CAPTURES = {"refusing": Refusing, "fake": FakeCapture}
        engine = Engine(sleep_cfg)
        engine.start()
        time.sleep(1.0)
        check("a source that will not start falls back to the next one",
              engine.capture_name == "fake" and engine.fps > 5, (engine.capture_name, engine.fps))
        check("the fallback leaves no error behind", not engine.last_error, engine.last_error)
        engine.stop()
    finally:
        eng.CAPTURES, eng.RETRY_DISPLAY_S, eng.screen_locked = real_capture, real_retry, real_locked
        FakeCapture.alive = False


def test_api(base):
    print("\n4. HTTP API")
    def get(path, timeout=3):
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return json.loads(r.read())

    s = get("/api/sync/on")
    check("/api/sync/on starts the engine", s["running"] is True)
    time.sleep(2.0)
    s = get("/api/status")
    check("engine reports a live frame rate", s["measured_fps"] > 20,
          f"{s['measured_fps']} fps")
    check("status carries a preview for the UI", len(s.get("preview", [])) > 0)
    from ambilight import version
    check("status names the running build", s.get("version") == version.build_id(), s.get("version"))
    check("while syncing, status still carries the board's brightness for the master slider",
          isinstance(s.get("wled_bri"), int) and s.get("wled_on") is True, (s.get("wled_bri"), s.get("wled_on")))
    check("status never leaks the PIN", "pin" not in s)
    bed_keys = ("bed_leds", "bed_pin", "bed_mode", "bed_look", "bed_side", "bed_colour", "bed_effect",
                "bed_speed", "bed_brightness", "bed_power_ma", "bed_reverse")
    check("status carries every bed setting the web page and the Mac app read",
          all(k in s for k in bed_keys), [k for k in bed_keys if k not in s])
    changed = get("/api/set?bed_mode=colour&bed_colour=2040ff&bed_brightness=0.4&bed_effect=63&bed_speed=200&bed_look=side&bed_side=bottom&bed_reverse=1")
    check("the bed's controls change what the status reports",
          (changed["bed_mode"], changed["bed_colour"], changed["bed_effect"], changed["bed_speed"], changed["bed_look"],
           changed["bed_side"], changed["bed_reverse"]) == ("colour", "2040ff", 63, 200, "side", "bottom", True), changed["changed"])
    try:
        get("/api/set?bed_mode=disco")
        refused_mode = False
    except urllib.error.HTTPError as exc:
        refused_mode = exc.code == 400
    check("an unknown bed mode is refused", refused_mode)
    get("/api/set?bed_mode=screen&bed_look=blend&bed_side=whole&bed_reverse=0")

    # The page renders the reply to each tap; a reply built from the hub's
    # cached board state would flick the switch back for a couple of seconds.
    lit = get("/api/effect/run?fx=0&color=ff2010")
    check("a colour lights everything, and the reply already says so",
          lit["running"] is False and lit["mode"] == "effect" and lit["wled_on"] is True, (lit["mode"], lit["wled_on"]))
    check("...and names the colour, so every device highlights the same one",
          lit.get("wled_fx") == 0 and lit.get("wled_col") == "ff2010", (lit.get("wled_fx"), lit.get("wled_col")))
    fx = get("/api/effect/run?fx=63&speed=90")
    check("an effect is named by its id", fx.get("wled_fx") == 63, fx.get("wled_fx"))
    dark = get("/api/lights/off")
    check("all off answers with everything off, not the cached board state",
          dark["running"] is False and dark["mode"] == "off" and dark["wled_on"] is False, (dark["mode"], dark["wled_on"]))
    check("...and the next poll agrees", get("/api/status")["mode"] == "off")
    s = get("/api/sync/on")
    check("screen sync comes back from all off", s["running"] is True and s["mode"] == "sync")

    from ambilight.config import PROFILES
    s = get("/api/profile/immersive")
    check("/api/profile/immersive applies", s["profile"] == "immersive")
    check("Immersive arrives as its whole look", all(s[k] == v for k, v in PROFILES["immersive"].items()),
          {k: s[k] for k in PROFILES["immersive"]})
    s = get("/api/profile/vivid")
    check("/api/profile/vivid applies", s["profile"] == "vivid")
    check("Vivid arrives as its whole look", all(s[k] == v for k, v in PROFILES["vivid"].items()),
          {k: s[k] for k in PROFILES["vivid"]})
    try:
        get("/api/profile/movie")
        refused_retired = False
    except urllib.error.HTTPError as exc:
        refused_retired = exc.code == 400
    check("a retired mode is refused, not half-applied", refused_retired and get("/api/status")["profile"] == "vivid")

    s = get("/api/set?immersion=0.7")
    s = get("/api/profiles/save?name=Late%20Night")
    check("the current look can be saved as a profile", s["profile"] == "Late Night")
    get("/api/set?immersion=0")
    s = get("/api/profile/Late%20Night")
    check("and switched back to by name", s["profile"] == "Late Night" and abs(s["immersion"] - 0.7) < 1e-6)
    listed = get("/api/profiles")
    check("profiles are listed, built-in and your own",
          listed["builtin"] == ["vivid", "immersive"] and "Late Night" in listed["user"] and listed["active"] == "Late Night")
    s = get("/api/profiles/delete?name=Late%20Night")
    check("and deleted", "Late Night" not in get("/api/profiles")["user"])

    # Listing runs in a short-lived process of its own: under heavy load it took 3-6.5 s here.
    listed = get("/api/camera/list", timeout=15)
    check("the web page can list webcams, the built-in one marked as never used",
          all(c["builtin"] == ("FaceTime" in c["name"]) for c in listed["cameras"])
          and listed["state"] in ("idle", "done", "error"), listed)

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

    def board_ddp():
        with urllib.request.urlopen(f"http://127.0.0.1:{FAKE_WLED_HTTP}/stats", timeout=3) as r:
            return json.loads(r.read())["ddp"]

    before = board_ddp()
    get("/api/set?output=ddp")
    time.sleep(1.5)
    s = get("/api/status")
    after = board_ddp()
    check("DDP can be chosen while sync runs", s["running"] and s["output_plugin"] == "wled-ddp",
          s["output_plugin"])
    check("frames reach the board as DDP", after["pushes"] - before["pushes"] > 20,
          f"{after['pushes'] - before['pushes']} frames in 1.5 s")
    check("the board finds nothing wrong with them", not after["violations"], after["violations"][:3])
    check("no error while streaming DDP", not s["last_error"], str(s["last_error"]))
    get("/api/set?output=udp")
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
    check("warm yellow drives less blue once matched to the LEDs",
          matched_led[2] / matched_led[0] < plain_led[2] / plain_led[0],
          (plain_led.round(), matched_led.round()))
    # Unmatched, the drive keeps the screen's own mix of light: curving each
    # channel apart turned pale teal lime and stripped weak channels on the strip.
    plain = dict(cfg, colour_match=False, contrast=2.0, hue_balance=0.0, adaptive=0.0)
    teal = np.array([[0x9c / 255, 0xbb / 255, 0xaf / 255]], np.float32)
    drive = color.to_led(teal, plain)[0]
    light = color.srgb_to_linear(teal)[0]
    check("unmatched, pale teal keeps its hue: drive in proportion to the screen's light",
          np.allclose(drive / drive.max(), light / light.max(), atol=0.01), (drive.round(), light.round(3)))
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


def test_adaptive_pace():
    print("\n14b. Adaptive pace: the light's speed follows how fast the picture changes")
    from ambilight.smoothing import BUSY_FACTOR, CALM_FACTOR, Smoother

    def fed(rate_per_s, seconds=2.0, fps=30):
        """A smoother fed a picture whose LEDs change by `rate_per_s` (0..1 units) every second."""
        s, now, level = Smoother(), 0.0, 0.2
        for _ in range(int(seconds * fps)):
            level = 0.2 + (now * rate_per_s) % 0.6
            s.set_target(np.full((10, 3), level, np.float32), now)
            s.step(now, 0.2, 1.0)
            now += 1 / fps
        return s

    calm, busy = fed(0.02), fed(3.0)
    check("a calm picture slows the light down", abs(calm.pace(1.0) - CALM_FACTOR) < 0.05, round(calm.pace(1.0), 2))
    check("fast action speeds it up", abs(busy.pace(1.0) - BUSY_FACTOR) < 0.05, round(busy.pace(1.0), 2))
    check("at zero the Response setting alone decides", calm.pace(0.0) == 1.0 and busy.pace(0.0) == 1.0)

    def biggest_step(start, end):
        s, now, frames = Smoother(), 0.0, []
        s.set_target(np.full((10, 3), start, np.float32), now)
        s.step(now, 0.2, 1.0)
        s.set_target(np.full((10, 3), end, np.float32), 0.0)
        for _ in range(30):
            now += 1 / 60
            frames.append(s.step(now, 0.2, 1.0)[0, 0])
        return float(np.max(np.abs(np.diff([start] + frames))))
    check("a cut to full eases in over several frames, never one jump", biggest_step(0.0, 1.0) < 0.35, round(biggest_step(0.0, 1.0), 3))
    check("a cut to black is eased the same way", biggest_step(1.0, 0.0) < 0.35, round(biggest_step(1.0, 0.0), 3))
    s = Smoother()
    s.set_target(np.full((10, 3), 0.5, np.float32), 1.0)
    s.set_target(np.full((10, 3), 0.9, np.float32), 1.0)       # two targets at the same moment
    check("two pictures at the same instant do not break the rate", np.isfinite(s.rate))


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
           "power_limit_ma": 3800, "sustained_ma": 3000, "supply_ma": 5000}
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
            "zones": [], "led_count": 4, "brightness": 1.0, "power_limit_ma": 3800, "sustained_ma": 3000, "supply_ma": 5000,
            "wb_r": 1.0, "wb_g": 1.0, "wb_b": 1.0, "bed_leds": 0}
    stage.set_target(np.full((4, 3), 0.8, np.float32), 0.0)
    fresh = stage.frame(0.5, live)
    stale = stage.frame(6.0, live)
    check("a frozen capture fades the strip out instead of holding it",
          fresh.max() > 150 and stale.max() == 0, (fresh.max(), stale.max()))


def test_run_record():
    print("\n15b. Run record: the build is named, and every settings change is journalled")
    import re
    from ambilight import journal, version

    check("the build is a semantic version, with the commit when there is one",
          re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?(\+[0-9a-f]{7,})?", version.build_id()), version.build_id())
    check("it starts with the VERSION file", version.build_id().startswith(open("VERSION").read().strip()))

    folder = tempfile.mkdtemp(prefix="ambilight-journal-")
    cfg = Config(os.path.join(folder, "config.json"))
    path = os.path.join(folder, "config.journal.jsonl")
    check("nothing is written before something changes", not os.path.exists(path))
    cfg.update({"brightness": 0.5, "pin": "4321"})
    cfg.update({"brightness": 0.5})
    cfg.update({"profile": "immersive", "zones": []})
    lines = [json.loads(line) for line in open(path)]
    check("each change that matters is one line; an unchanged value writes none", len(lines) == 2, len(lines))
    first = lines[0]
    check("the line says when, which build, and the old and new value",
          first["build"] == version.build_id() and re.match(r"\d{4}-\d\d-\d\dT", first["t"])
          and first["changed"]["brightness"] == [1.0, 0.5], first)
    check("the PIN is recorded as changed, never its value",
          first["changed"]["pin"] == "changed" and "4321" not in open(path).read())
    cfg.update({"zones": [{"start": 0, "end": 9, "mode": "warm", "color": "#ff2010", "brightness": 1.0, "keep": True}]})
    check("big values are noted as changed, not copied",
          json.loads(open(path).readlines()[-1])["changed"]["zones"] == "changed")
    journal.record(path, {"fps": (60, 61)}, event="start", max_bytes=100)
    check("the journal rolls over instead of growing without limit", os.path.exists(path + ".1"))
    journal.record(os.path.join(folder, "missing-dir", "x.jsonl"), {"fps": (60, 61)})
    check("an unwritable journal never stops a settings change", cfg.update({"fps": 62}) == ["fps"])


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
    check("DDP is an output choice", recovered.update({"output": "DDP"}) == ["output"]
          and recovered.get("output") == "ddp")
    check("the DDP port is a setting with a range",
          recovered.update({"ddp_port": "4049"}) == ["ddp_port"] and recovered.get("ddp_port") == 4049
          and rejects({"ddp_port": 0}) is False and recovered.get("ddp_port") == 1)
    recovered.update({"output": "udp", "ddp_port": 4048})
    check("a controller address cannot carry a URL", rejects({"wled_ip": "evil.example/x"}))
    check("an mDNS controller name is accepted", recovered.update({"wled_ip": "wled-6b0bc6.local"}) == ["wled_ip"])
    check("an IP controller address is accepted", recovered.update({"wled_ip": "192.168.29.57"}) == ["wled_ip"])
    check("infinite numbers are refused", rejects({"fps": "1e400"}))
    check("non-numbers are refused, not crashed on", rejects({"fps": "fast"}))
    check("a serial port must be a device path", rejects({"serial_port": "../../etc/passwd"}))

    recovered.update({"brightness": 0.55, "white_kelvin": 3200, "contrast": 1.1})
    recovered.apply_profile("immersive")
    check("a sync mode never touches brightness, and brings its own warmth and contrast",
          recovered.get("brightness") == 0.55
          and recovered.get("white_kelvin") == PROFILES["immersive"]["white_kelvin"]
          and recovered.get("contrast") == PROFILES["immersive"]["contrast"]
          and recovered.get("blend") == PROFILES["immersive"]["blend"], recovered.get("white_kelvin"))
    recovered.apply_profile("vivid")
    check("switching back to Vivid brings back its own warm white, not Immersive's",
          recovered.get("white_kelvin") == PROFILES["vivid"]["white_kelvin"] == 3000)

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

    protected = PinGate()
    check("protected with no PIN set refuses everyone, it never falls open",
          protected.check("", "", "10.0.0.2", now=0, unprotected=False) == "unconfigured")
    check("protected with a PIN still accepts the right one",
          protected.check("1234", "1234", "10.0.0.2", now=0, unprotected=False) == "ok")
    check("protected with a PIN refuses a wrong one",
          protected.check("0000", "1234", "10.0.0.2", now=0, unprotected=False) == "denied")
    check("unprotected is the default and keeps today's open-LAN behaviour",
          PinGate().check("", "", "10.0.0.2", now=0) == "ok")

    import os, tempfile
    from ambilight.config import Config, DEFAULTS
    cfg = Config(os.path.join(tempfile.mkdtemp(), "config.json"))
    check("a new install is unprotected for now", DEFAULTS["unprotected"] is True and cfg.get("unprotected") is True)
    try:
        cfg.update({"unprotected": "0"})
        refused = False
    except ValueError:
        refused = True
    check("protected mode cannot be switched on without a PIN", refused and cfg.get("unprotected") is True)
    cfg.update({"pin": "4321", "unprotected": "0"})
    check("a PIN and protected mode can be set in one request", cfg.get("unprotected") is False)
    try:
        cfg.update({"pin": ""})
        cleared = True
    except ValueError:
        cleared = False
    check("the PIN cannot be cleared while protected", not cleared and cfg.get("pin") == "4321")
    cfg.update({"pin": "", "unprotected": "1"})
    check("going back to unprotected and clearing the PIN works together",
          cfg.get("unprotected") is True and cfg.get("pin") == "")


def test_board_answering():
    print("\n41. Clients are told when the LED board stops answering")
    from ambilight.server import board_answering

    check("syncing with frames getting through: answering", board_answering(False, True, None))
    check("syncing while sends fail: not answering, so the page can say NO LINK",
          not board_answering(True, True, "could not reach the lights: [Errno 64] Host is down"))
    check("not syncing: the board's JSON API decides",
          board_answering(True, False, None) and not board_answering(False, False, None))
    check("an output error from an earlier sync does not count once sync is off",
          board_answering(True, False, "release failed: [Errno 65] No route to host"))


def test_board_urls():
    print("\n42. Where the board's own page is, for when the hub is out of reach")
    from ambilight.discovery import board_urls
    from ambilight.hostcache import HostCache

    check("a named board lists its name, then the address it last answered on",
          board_urls("wled-6b0bc6.local", 80, "192.168.29.40")
          == ["http://wled-6b0bc6.local", "http://192.168.29.40"])
    check("a board set by address is listed once", board_urls("192.168.29.40", 80, "192.168.29.40")
          == ["http://192.168.29.40"])
    check("a name never resolved is still offered", board_urls("wled.local", 80, None) == ["http://wled.local"])
    check("a non-standard web port is kept", board_urls("127.0.0.1", 8821, "127.0.0.1") == ["http://127.0.0.1:8821"])

    cache = HostCache(resolve=lambda host: "10.0.0.7")
    check("nothing is known about a name before its first lookup", cache.last_known("wled.local") is None)
    cache.address("wled.local")
    check("after a lookup, its address is known without asking again", cache.last_known("wled.local") == "10.0.0.7")
    check("an address is its own last-known address", cache.last_known("10.0.0.9") == "10.0.0.9")


def decode_qr(rows, scale=8, quiet=4):
    """Read a module matrix back the way a phone camera would, with CoreImage's detector."""
    import Quartz
    from Foundation import NSData

    side = (len(rows) + 2 * quiet) * scale
    dark = [((x + quiet) * scale, (y + quiet) * scale, scale, scale)
            for y, row in enumerate(rows) for x, bit in enumerate(row) if bit == "1"]
    return decode_rects(side, dark)


def decode_rects(side, dark):
    """Decode a white square of `side` pixels with black (x, y, w, h) rectangles on it."""
    import Quartz
    from Foundation import NSData

    pixels = bytearray(b"\xff" * (side * side))
    for x, y, w, h in dark:
        for row in range(y, y + h):
            pixels[row * side + x:row * side + x + w] = bytes(w)
    provider = Quartz.CGDataProviderCreateWithCFData(NSData.dataWithBytes_length_(bytes(pixels), len(pixels)))
    image = Quartz.CGImageCreate(side, side, 8, 8, side, Quartz.CGColorSpaceCreateDeviceGray(),
                                 Quartz.kCGImageAlphaNone, provider, None, False,
                                 Quartz.kCGRenderingIntentDefault)
    detector = Quartz.CIDetector.detectorOfType_context_options_(
        Quartz.CIDetectorTypeQRCode, None, {Quartz.CIDetectorAccuracy: Quartz.CIDetectorAccuracyHigh})
    return [feature.messageString() for feature in detector.featuresInImage_(Quartz.CIImage.imageWithCGImage_(image))]


def test_pairing_qr():
    print("\n44. QR codes for pairing a phone or tablet")
    from ambilight import qr

    url = "http://ishwars-macbook-air.local:8080"
    rows = qr.modules(url)
    check("the code is square, 21 + 4n modules a side",
          len(rows) == len(rows[0]) and (len(rows) - 21) % 4 == 0 and all(len(r) == len(rows) for r in rows),
          (len(rows), len(rows[0])))
    check("it holds only dark and light modules", set("".join(rows)) <= {"0", "1"})
    check("the finder squares sit top-left, top-right and bottom-left, so it reads the right way up",
          rows[0].startswith("1111111") and rows[0].endswith("1111111")
          and rows[-1].startswith("1111111") and not rows[-1].endswith("1111111"))
    check("a camera reads back exactly the address", decode_qr(rows) == [url], decode_qr(rows))
    check("the same address is drawn once and then remembered", qr.modules(url) is rows)

    saved = sys.modules.get("Quartz")
    sys.modules["Quartz"] = None    # what an install without pyobjc looks like
    try:
        qr.modules("http://elsewhere.local:1")
        check("without CoreImage it says what is missing", False)
    except RuntimeError as exc:
        check("without CoreImage it says what is missing", "pyobjc" in str(exc), str(exc))
    finally:
        sys.modules["Quartz"] = saved


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

    check("ScreenCaptureKit is preferred, then Quartz",
          list(CAPTURES)[:2] == ["screencapturekit", "quartz"], list(CAPTURES))
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


def test_sck_capture():
    print("\n21b. ScreenCaptureKit capture plugin")
    from ambilight.plugins.capture_sck import SCKCapture, _Latest

    # Before the first frame the count is 0; a wait that treated that as news
    # returned no frame at once, and the stream was closed before it began.
    latest = _Latest()
    started = time.perf_counter()
    count, buffer = latest.wait(0, 0.15)
    check("waiting for the first frame really waits",
          buffer is None and time.perf_counter() - started >= 0.14, time.perf_counter() - started)
    latest.put("frame")
    check("a delivered frame wakes the wait", latest.wait(0, 1.0) == (1, "frame"))

    if not SCKCapture.available():
        print("    (skipped: pyobjc ScreenCaptureKit not installed)")
        return
    # Once a process has captured the screen (Quartz or ScreenCaptureKit), macOS
    # will not start a ScreenCaptureKit stream for processes it launches
    # afterwards. So the live checks run in a child of their own, and main()
    # runs this test before any other test captures. Sandbox hubs launched
    # later fall back to Quartz, which the engine tests cover.
    run = subprocess.run([sys.executable, "-c", "import test_hub; test_hub.sck_live_report()"],
                         cwd=os.path.join(ROOT, "tests"), capture_output=True, text=True, timeout=60)
    try:
        results = json.loads(run.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        check("the live ScreenCaptureKit checks ran", False, run.stderr[-800:])
        return
    for name, ok, detail in results:
        check(name, ok, detail)


def sck_live_report():
    """The live half of test_sck_capture, run in a child process; prints its results as JSON."""
    from ambilight import engine
    from ambilight.plugins import capture_sck
    from ambilight.plugins.capture_sck import MAX_WIDTH, SCKCapture

    results = []

    def note(name, ok, detail=""):
        results.append([name, bool(ok), str(detail)])

    cap = SCKCapture()
    try:
        cap.open(99, 120)
        note("a missing display is reported like the other plugins", False)
    except IndexError as exc:
        note("a missing display is reported like the other plugins", "not found" in str(exc), exc)
    width, height = cap.open(1, 120)
    try:
        note("frames arrive scaled down to the stream width", width <= MAX_WIDTH, (width, height))
        full = cap.grab_full()
        note("a full grab matches the opened size", full.shape == (height, width, 3), full.shape)
        note("it is RGB uint8", full.dtype == np.uint8)
        band_rows = round(120 * cap.scale)
        note("the band is the bottom rows, in the stream's own scale",
             cap.grab().shape == (band_rows, width, 3), (cap.grab().shape, cap.scale))
        started = time.perf_counter()
        for _ in range(30):
            cap.grab()
        note("a grab never waits for the screen to change",
             time.perf_counter() - started < 0.1, time.perf_counter() - started)
        note("the stream runs no faster than the engine reads",
             capture_sck.STREAM_FPS <= engine.CAPTURE_FPS_MAX)
        cap.close()
        reopened = SCKCapture()
        reopened.open(1, 120)
        note("the same process can reopen a stream it closed", reopened.grab().size > 0)
        reopened.close()
        # Only how much sound arrived and its format are noted, never the sound itself.
        heard = []
        hearing = SCKCapture()
        hearing.listener = lambda samples, at: heard.append((len(samples), samples.dtype.str))
        hearing.open(1, 120)
        time.sleep(1.0)
        hearing.close()
        note("with a listener, the stream hands over the Mac's sound as mono float samples, every ~20 ms",
             len(heard) >= 20 and all(n > 0 and kind == "<f4" for n, kind in heard), (len(heard), heard[:2]))
    finally:
        cap.close()
    print(json.dumps(results))


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

    # Power alone does not end WLED's active preset, and realtime packets light a
    # board that is off, so "everything off" must change the look and stop the stream.
    routes.handle(hub, "/api/sync/on", parse_qs(""))
    order = []
    stop = Engine.stop
    Engine.stop = lambda self: (order.append(len(hub.wled.sent)), stop(self))
    hub.wled.sent.clear()
    try:
        body, code = routes.handle(hub, "/api/lights/off", parse_qs(""))
    finally:
        Engine.stop = stop
    kind, payload = hub.wled.sent[-1] if hub.wled.sent else (None, {})
    check("all off turns the board off", kind == "apply" and payload.get("on") is False, hub.wled.sent)
    check("...in a command that changes the look, so the sync preset clears",
          payload.get("seg", [{}])[0].get("fx") == 0 and "ps" not in payload, payload)
    check("...and stops the stream", not Engine.running and order, order)
    check("...after the board has been told, so the watcher cannot restart sync",
          order == [1] and code == 200, (order, hub.wled.sent))
    check("...and answers with the status the page renders", body.get("running") is False, body)

    # A manual brightness change may end the board's active preset, and with it
    # "monitor sync" (board.py). The master brightness must never stop sync.
    hub.wled.sent.clear()
    routes.handle(hub, "/api/wled/brightness", parse_qs("value=90"))
    check("brightness with sync off is the brightness alone", hub.wled.sent == [("apply", {"bri": 90})], hub.wled.sent)
    routes.handle(hub, "/api/sync/on", parse_qs(""))
    hub.wled.sent.clear()
    routes.handle(hub, "/api/wled/brightness", parse_qs("value=300"))
    check("brightness while syncing keeps the sync preset chosen, in the same command",
          hub.wled.sent == [("apply", {"bri": 255, "ps": 1})], hub.wled.sent)
    check("...and holds the watcher off while the board settles", hub.intent.paused())
    hub.intent.resume()
    check("the watcher is not held off otherwise", not hub.intent.paused())
    routes.handle(hub, "/api/sync/off", parse_qs(""))

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
    check("it fetches only WLED's own state endpoints, all through one wrapper",
          set(re.findall(r'ask\("([^"]+)"', script)) == {"/json/state", "/json/si"}
          and script.count("fetch(") == 1)
    check("a board that drops off Wi-Fi is noticed within a few seconds",
          "abort.abort()" in script and "TIMEOUT_MS = 4000" in script)
    check("Wi-Fi coming back refreshes at once",
          'addEventListener("online", poll)' in script and '"pageshow"' in script)
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

    power = run_node("const SYNC_PRESET = 1;\n" + js_block(script, "function powerChange") + r"""
const last = {fx: 63, sx: 90, ix: 128, rgb: [255, 170, 80]};
console.log(JSON.stringify({
  offSyncing: powerChange({on: true, ps: 1}, last),
  offEffect: powerChange({on: true, ps: -1}, last),
  on: powerChange({on: false, ps: -1}, last),
}));
""") if "function powerChange" in script else None
    check("switching off during sync leaves sync too, or the Mac's stream would light the strip again",
          isinstance(power, dict) and power["offSyncing"][0] == {"on": False, "lor": 1}
          and power["offSyncing"][1].get("fx") == 63, power)
    check("...and otherwise power is all it touches",
          isinstance(power, dict) and power["offEffect"] == [{"on": False}] and power["on"] == [{"on": True}], power)
    check("the power button uses it", "queue(...change)" in script)

    constants = re.search(r"^const POLL_MS.*$", script, re.M).group(0)
    with silent_server() as silent:
        result = run_node(constants + "\n" + js_block(script, "async function ask") + f"""
const started = Date.now();
ask("{silent}/json/si").then(() => console.log(JSON.stringify({{kind: "answered"}})),
  () => console.log(JSON.stringify({{kind: "gave up", ms: Date.now() - started}})));
""")
    check("a board that accepts but never answers is given up on within five seconds",
          result and result.get("kind") == "gave up" and result["ms"] < 5000, result)


PAGE = "ambilight/web/index.html"
PAGE_FILES = (PAGE, "ambilight/web/app.css", "ambilight/web/app.js")


def page_script():
    """The phone page's script, which lives in app.js."""
    return open("ambilight/web/app.js").read()


def page_text():
    """The phone page as one text: markup, styles and script."""
    return "\n".join(open(path).read() for path in PAGE_FILES)


def js_block(script, start):
    """One top-level declaration of the page, from `start` to its closing brace."""
    import re
    return re.search(rf"^{re.escape(start)}.*?\n}};?$", script, re.S | re.M).group(0)


@contextmanager
def silent_server():
    """A port that accepts connections and never answers: a sleeping Mac, as a phone sees it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", SILENT_PORT))
    sock.listen(8)
    try:
        yield f"http://127.0.0.1:{SILENT_PORT}"
    finally:
        sock.close()


def run_node(code, timeout=12):
    """Run page JavaScript under node; None when node is missing or the code never finishes."""
    import pathlib, shutil
    node = shutil.which("node")
    if not node:
        return None
    path = pathlib.Path(tempfile.mkdtemp()) / "probe.js"
    path.write_text(code)
    try:
        run = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"hung": True}
    if run.returncode != 0:
        return {"error": run.stderr[-400:]}
    return json.loads(run.stdout)


def test_phone_page_connection():
    print("\n40. The phone page notices a hub that stops answering")
    import re, shutil
    if not shutil.which("node"):
        print("    (skipped: node not installed, so the page's script was not run)")
        return
    script = page_script()
    plumbing = "\n".join(js_block(script, start) for start in
                         ("class HubError", "async function api"))
    timed = js_block(script, "async function timedFetch") if "async function timedFetch" in script else ""
    constants = re.search(r"^const POLL_MS.*$", script, re.M).group(0)

    with silent_server() as silent:
        result = run_node(constants + "\n" + timed + "\n" + plumbing + f"""
const realFetch = fetch;
globalThis.fetch = (path, options) => realFetch("{silent}" + path, options);
let pin = "";
const pinHeaders = () => ({{}});
function showLocked() {{}}
const started = Date.now();
api("/api/status").then(
  () => console.log(JSON.stringify({{kind: "answered", ms: Date.now() - started}})),
  err => console.log(JSON.stringify({{kind: err.kind, ms: Date.now() - started}})));
""")
    check("a hub that accepts but never answers is reported offline, not waited on forever",
          result and result.get("kind") == "offline", result)
    check("...within five seconds", result and result.get("kind") == "offline" and result["ms"] < 5000, result)

    answers = run_node(constants + "\n" + timed + "\n" + plumbing + r"""
let pin = "";
const pinHeaders = () => ({});
function showLocked() {}
const replies = [[403, {error: "PIN required or incorrect"}],
                 [429, {error: "too many wrong PINs from this device; try again in a minute"}],
                 [500, {error: "the hub hit an internal error; see its log"}]];
(async () => {
  const kinds = [];
  for (const [code, body] of replies) {
    globalThis.fetch = async () => new Response(JSON.stringify(body), {status: code});
    try { await api("/api/status"); kinds.push("answered"); } catch (err) { kinds.push(err.kind); }
  }
  console.log(JSON.stringify(kinds));
})();
""")
    check("a PIN refusal, a lockout and a hub error are told apart",
          answers == ["locked", "throttled", "failed"], answers)

    views = run_node("\n".join(js_block(script, start) for start in
                               ("function ago", "function linkView", "function friendly")) + r"""
const now = 1000000;
const out = {
  asleep: linkView({kind: "offline", lastSeen: now - 12000, now}),
  withBoard: linkView({kind: "offline", lastSeen: now, now,
                       boardUrls: ["http://wled-6b0bc6.local", "http://192.168.29.40"]}),
  never: linkView({kind: "offline", lastSeen: 0, now}),
  long: linkView({kind: "offline", lastSeen: now - 3 * 60000, now}),
  throttled: linkView({kind: "throttled", lastSeen: now, now}),
  boardDown: linkView({kind: "ok", lastSeen: now, now, wledReachable: false, syncing: false}),
  boardDownSyncing: linkView({kind: "ok", lastSeen: now, now, wledReachable: false, syncing: true}),
  fine: linkView({kind: "ok", lastSeen: now, now, wledReachable: true, syncing: true}),
  locked: linkView({kind: "locked", lastSeen: now, now}),
  screenAsleep: friendly("no display to capture -- the screen is asleep or the lid is closed. Sync resumes on its own when it wakes."),
  boardGone: friendly("could not reach the lights: 192.168.29.40:21324 [Errno 64] Host is down"),
};
console.log(JSON.stringify(out));
""")
    if views and "error" not in views:
        check("an unreachable hub says when it was last seen",
              "last seen 12 s ago" in views["asleep"]["offline"], views["asleep"])
        check("...in minutes once it has been a while", "last seen 3 min ago" in views["long"]["offline"])
        check("...and leaves the time out when it was never seen", "last seen" not in views["never"]["offline"])
        check("it says the hub only answers at home, for a phone that is out",
              "home Wi-Fi" in views["asleep"]["offline"])
        check("with the board's address remembered, it offers the strip's own page",
              "strip's own page" in views["withBoard"]["offline"], views["withBoard"])
        check("...at the address the board last answered on",
              views["withBoard"]["boardUrl"] == "http://192.168.29.40", views["withBoard"]["boardUrl"])
        check("...and offers no link it doesn't have",
              views["asleep"]["boardUrl"] is None and "strip's own page" not in views["asleep"]["offline"])
        check("a PIN lockout says to wait a minute instead of going quiet",
              "Wait a minute" in (views["throttled"]["throttled"] or ""))
        check("a board that stops answering gets its own banner",
              "isn't answering" in (views["boardDown"]["board"] or ""))
        check("...except while syncing, where NO LINK and the engine's error say it",
              views["boardDownSyncing"]["board"] is None)
        check("all well, or a PIN to enter, shows no connection banner",
              all(v is None for key in ("fine", "locked") for v in views[key].values()), views["fine"])
        check("a sleeping screen is not blamed on the display choice",
              "asleep" in views["screenAsleep"] and "Pick another display" not in views["screenAsleep"],
              views["screenAsleep"])
        check("a send that fails with 'Host is down' is explained as the LED board",
              "LED controller" in views["boardGone"], views["boardGone"])
    else:
        check("the connection states run under node", False, views)

    check("Wi-Fi coming back retries at once", 'addEventListener("online", poll)' in script)
    check("a page restored from the back-forward cache refreshes", '"pageshow"' in script)
    check("the page still never builds markup from strings", "innerHTML" not in script)

    from ambilight import qr
    url = "http://ishwars-macbook-air.local:8080"
    drawn = run_node("\n".join(js_block(script, start) for start in ("function qrLayout", "function drawQr")) +
                     f"\nconst modules = {json.dumps(list(qr.modules(url)))};" + r"""
const rects = [];
let fill = "";
const ctx = {set fillStyle(v) { fill = v; }, fillRect(x, y, w, h) { if (fill === "#000") rects.push([x, y, w, h]); }};
drawQr({width: 264, height: 264, getContext: () => ctx}, modules);
console.log(JSON.stringify({rects, layout: qrLayout(264, modules.length)}));
""")
    if drawn and "rects" in drawn:
        layout = drawn["layout"]
        check("the code is drawn in whole pixels with a four-module margin",
              layout["cell"] == 7 and layout["offset"] >= 4 * layout["cell"], layout)
        check("a phone camera reads back what the page draws", decode_rects(264, drawn["rects"]) == [url])
    else:
        check("the pairing code is drawn", False, drawn)

    origins = run_node(re.search(r"^const ipOrigin.*$", script, re.M).group(0).replace("const ipOrigin", "const f") + r"""
console.log(JSON.stringify(["192.168.29.53", "[fe80::1]", "ishwars-macbook-air.local", "localhost"].map(f)));
""") if "const ipOrigin" in script else None
    check("a page opened by IP address is told to use the stable name instead",
          origins == [True, True, False, False], origins)


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


def test_fake_wled_ddp():
    print("\n26b. The fake WLED checks DDP packets the way WLED 16.0.1 reads them")
    import fake_wled
    from ambilight.plugins.output_wled_ddp import header

    good = fake_wled.parse_ddp(header(1440, 6, 3, True) + bytes(6))
    check("the hub's packet is accepted",
          good["violations"] == [] and (good["offset"], good["leds"], good["push"], good["seq"]) == (1440, 2, True, 3),
          good)

    def flagged(packet, tag):
        return any(v.startswith(tag) for v in fake_wled.parse_ddp(packet)["violations"])

    check("a short packet is flagged", flagged(b"\x41\x01", "SHORT"))
    check("a wrong version is flagged", flagged(b"\x81" + header(0, 3, 1, True)[1:] + bytes(3), "NOT-V1"))
    check("a length that disagrees with the payload is flagged", flagged(header(0, 6, 1, True) + bytes(3), "LENGTH"))
    check("an offset between LEDs is flagged", flagged(header(4, 3, 1, True) + bytes(3), "UNALIGNED"))
    check("more than 1440 data bytes is flagged", flagged(header(0, 1443, 1, True) + bytes(1443), "OVER-1440"))


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

    # Light the screen throws on the desk and keyboard beside it can join its region.
    from ambilight import camrun
    spill_on = base.copy(); spill_on[20:70, 50:150] = 230
    spill_on[70:115, 140:150] = 105; spill_on[60:65, 150:195] = 105
    spill_on = np.pad(spill_on, ((0, 5), (0, 0), (0, 0)), constant_values=20)[:h + 5]
    strip_only = np.full(spill_on.shape, 20, np.uint8); strip_only[:, :20] = 140
    plain = camrun.largest_region(camcal.find_regions(spill_on, strip_only)[0])
    check("with the default cut that spill makes the screen region ragged",
          not camcal.looks_like_screen(plain), round(float(plain.sum() / ((np.ptp(np.flatnonzero(plain.any(axis=1))) + 1)
                                                         * (np.ptp(np.flatnonzero(plain.any(axis=0))) + 1))), 2))
    clean, _ = camrun.find_screen(spill_on, strip_only)
    check("a stricter cut drops it, leaving the monitor as one clean rectangle",
          camcal.looks_like_screen(clean) and clean[40, 100] and not clean[90, 145])
    scattered = base.copy(); scattered[10:20, 10:20] = 230; scattered[80:90, 150:160] = 230
    ragged, _ = camrun.find_screen(scattered, np.full(scattered.shape, 20, np.uint8))
    check("a picture with no rectangle still comes back as not a screen, so the setup is refused",
          not camcal.looks_like_screen(ragged))


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


class BlownOutWallRoom(SimulatedRoom):
    """A wall that turns the strip's blue into far more light than the camera can take.

    At the usual drive the glow clips solid white; only a dimmer strip reads
    cleanly. Like the real wall and webcam, where blue was the step that stopped.
    """
    M = np.array([[0.55, 0.10, 0.02], [0.08, 0.90, 0.05], [0.02, 0.06, 5.0]])


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

        room = SimulatedRoom()
        saved_score = camrun.score
        camrun.score = lambda window, hub, cam, screen, wall: (
            0.1 if room.settings["led_matrix"] and room.settings["saturation"] == 1.45 else 0.3)
        try:
            result = camrun.calibrate(room, room, room, dict(room.settings), lambda line: None)
        finally:
            camrun.score = saved_score
        check("when the measured colours with the boost win, the boost is what is kept "
              "(not left at the last candidate tried)",
              result["chosen"] == "the measured colours" and room.settings["saturation"] == 1.45,
              (result["chosen"], room.settings["saturation"]))

        room = BlownOutPrimaryRoom()
        result = camrun.calibrate(room, room, room, dict(room.settings), lambda line: None)
        check("a primary that blows out the screen at the usual level still matches, "
              "by retrying dimmer instead of aborting",
              result["kept"] and result["after"] < 0.2, round(result["after"], 3))

        room = BlownOutWallRoom()
        try:
            result, stopped = camrun.calibrate(room, room, room, dict(room.settings), lambda line: None), ""
        except RuntimeError as exc:
            result, stopped = {"kept": False, "after": 1.0}, str(exc)
        check("a wall glow too bright for the camera at the usual drive still matches, "
              "by dimming the strip instead of stopping",
              not stopped and result["kept"] and result["after"] < 0.25, (stopped, round(result["after"], 3)))
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
    check("the hub lists them from a separate process, with the same result",
          camrun.list_cameras_in_child() == camrun.list_cameras())


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


def test_immersion():
    print("\n35. Immersion: a red scene floods the room; greys, skin and black stay true")
    from ambilight import color

    cfg = {"saturation": 1.45, "immersion": 0.0}
    deep = {"deep red": (0.45, 0.06, 0.06), "maroon": (0.30, 0.04, 0.08), "navy": (0.05, 0.08, 0.35)}
    rows = np.array(list(deep.values()) + [(0.5, 0.5, 0.5), (0.85, 0.62, 0.50), (0.0, 0.0, 0.0)], np.float32)
    linear = color.srgb_to_linear(rows)

    def hsv(out):
        return color.rgb_to_hsv(out)

    def each(settings):
        return np.vstack([color.grade(linear[k:k + 1], settings) for k in range(len(linear))])

    off = each(cfg)
    check("immersion 0 grades exactly as before",
          np.allclose(color.grade(linear, cfg), color.grade(linear, {"saturation": 1.45})), off)
    on = each({**cfg, "immersion": 1.0})
    for k, name in enumerate(deep):
        check(f"a {name} glows much brighter and at least as pure",
              hsv(on)[k, 2] >= 1.4 * hsv(off)[k, 2] and hsv(on)[k, 1] >= hsv(off)[k, 1] - 1e-3,
              (hsv(off)[k].round(3), hsv(on)[k].round(3)))
        check(f"and keeps its hue ({name})", abs(hsv(on)[k, 0] - hsv(off)[k, 0]) < 0.02)
    check("a grey stays grey and as bright",
          hsv(on)[3, 1] < 0.02 and abs(hsv(on)[3, 2] - hsv(off)[3, 2]) < 0.02, on[3])
    check("skin keeps its natural look",
          hsv(on)[4, 2] < 1.08 * hsv(off)[4, 2] and hsv(on)[4, 1] < hsv(off)[4, 1] + 0.08,
          (hsv(off)[4].round(3), hsv(on)[4].round(3)))

    scene = np.array([(0.55, 0.05, 0.05)] * 30 + [(0.03, 0.03, 0.03)] * 10, np.float32)
    flooded = color.grade(color.srgb_to_linear(scene), {**cfg, "immersion": 1.0})
    plain = color.grade(color.srgb_to_linear(scene), cfg)
    check("in a red scene the dark parts of the strip fill with the scene's red",
          flooded[-1, 0] > 0.15 and flooded[-1, 0] > 3 * flooded[-1, 1], (plain[-1], flooded[-1]))
    split = np.array([(0.6, 0.05, 0.05)] * 22 + [(0.05, 0.06, 0.5)] * 8 + [(0.03, 0.03, 0.03)] * 10, np.float32)
    mixed = color.grade(color.srgb_to_linear(split), {**cfg, "immersion": 1.0})
    check("a mostly red scene with some blue floods toward red, not a muddy purple",
          mixed[-1, 0] > 2 * mixed[-1, 2] and mixed[-1, 0] > 0.12, mixed[-1])
    grey_scene = np.array([(0.4, 0.4, 0.4)] * 30 + [(0.03, 0.03, 0.03)] * 10, np.float32)
    calm = color.grade(color.srgb_to_linear(grey_scene), {**cfg, "immersion": 1.0})
    check("a scene with no strong colour is not flooded",
          np.allclose(calm, color.grade(color.srgb_to_linear(grey_scene), cfg), atol=0.02))
    one_light = np.array([(0.8, 0.05, 0.05)] + [(0.03, 0.03, 0.03)] * 39, np.float32)
    night = color.grade(color.srgb_to_linear(one_light), {**cfg, "immersion": 1.0})
    check("one small red light in a dark scene does not paint the whole room",
          night[-1].max() < 0.08, night[-1])
    check("black stays black", np.allclose(on[5], 0.0, atol=1e-3), on[5])
    from ambilight.config import PROFILES
    check("immersion is a setting from 0 to 1, starting at the default mode's (Vivid's) level",
          Config(os.path.join(tempfile.mkdtemp(), "c.json")).get("immersion") == PROFILES["vivid"]["immersion"])


def test_bass_beats():
    print("\n35c. Bass: beats heard in the sound, and motion that never strobes")
    from ambilight import beat

    rate = beat.RATE
    rng = np.random.default_rng(7)

    def track(seconds, bpm=120, kick=0.8, hats=0.05, bass_note=0.0):
        """A kick drum every beat, hi-hat hiss, and optionally a steady bass note, with the true kick times."""
        t = np.arange(int(seconds * rate)) / rate
        # Hi-hats are hiss above a few kHz: noise differenced three times keeps its highs, not its lows.
        hiss = np.diff(rng.standard_normal(len(t) + 3), n=3) / 6.0
        sound = hats * hiss * (np.sin(2 * np.pi * 8 * t) > 0)
        sound += bass_note * np.sin(2 * np.pi * 55 * t)
        times = np.arange(0.5, seconds - 0.2, 60.0 / bpm)
        for at in times:
            start = int(at * rate)
            n = min(int(0.12 * rate), len(t) - start)
            hit = np.arange(n) / rate
            sound[start:start + n] += kick * np.exp(-hit / 0.04) * np.sin(2 * np.pi * (90 - 300 * hit) * hit)
        return sound.astype(np.float32), times

    def listen(sound, block=480):
        """Feed it as ScreenCaptureKit does, 10 ms blocks; returns the times beats were heard."""
        meter, heard = beat.BassMeter(rate), []
        for start in range(0, len(sound), block):
            end = min(start + block, len(sound))
            if meter.feed(sound[start:end], end / rate):
                heard.append(meter.last_beat)
        return meter, heard

    sound, kicks = track(8)
    meter, heard = listen(sound)
    late = [min(abs(h - k) for k in kicks) for h in heard]
    check("every kick of a 120 BPM track is heard", len(heard) == len(kicks), (len(heard), len(kicks)))
    check("...within 40 ms of the kick itself, so the light lands on the beat",
          heard and max(late) < 0.04, round(max(late or [0]), 3))
    quiet, kicks_q = track(8, kick=0.8 * 10 ** (-30 / 20), hats=0.05 * 10 ** (-30 / 20))
    check("a track played 30 dB quieter is heard just as well", len(listen(quiet)[1]) == len(kicks_q))
    check("a steady bass note is not a beat", len(listen(track(6, kick=0.0, bass_note=0.5)[0])[1]) <= 1)
    check("hi-hats alone are not bass", len(listen(track(6, kick=0.0, hats=0.3)[0])[1]) == 0)
    check("silence has no beats", len(listen(np.zeros(rate * 3, np.float32))[1]) == 0)
    fast, _ = track(8, bpm=200)
    gaps = np.diff(listen(fast)[1])
    check("even at 200 BPM beats stay at least 0.35 s apart: never more than three a second",
          len(gaps) > 0 and gaps.min() >= beat.REFRACTORY_S - 1e-6, round(float(gaps.min()), 3) if len(gaps) else None)
    check("a beat's pulse rises within a frame or two and is gone within a second",
          meter.pulse(meter.last_beat + beat.ATTACK_S) > 0.3 and meter.pulse(meter.last_beat + 2 * beat.PULSE_S + 0.01) == 0.0,
          (round(meter.pulse(meter.last_beat + beat.ATTACK_S), 3), meter.strength))

    scene = np.tile(np.array([[200.0, 120.0, 40.0]], np.float32), (240, 1))
    worst = max(abs(beat.bounce(scene, 120, age, 1.0, 1.0).mean() - scene.mean())
                for age in np.arange(0, beat.PULSE_S * 2, 0.01))
    check("a full Bounce on a bright scene moves the whole strip's brightness less than the guard's 10% flash",
          worst < 0.10 * 255 and worst <= beat.BEAT_BUDGET * 255 + 0.5, round(worst / 255, 3))
    early = beat.bounce(scene, 120, 0.05, 1.0, 1.0)
    check("the bounce is a wave running outward from the middle of the screen",
          early[120 + int(beat.WAVE_SPEED * 0.05)].sum() > early[120].sum() > scene[120].sum())
    check("Bounce at 0 leaves the picture alone", np.array_equal(beat.bounce(scene, 120, 0.05, 1.0, 0.0), scene))
    rippled = beat.shake(scene, 0.05, 1.0, 1.0)
    moved = np.abs(rippled - scene).max(axis=1) > 0.1 * scene.max(axis=1)
    check("Shake shows on an even colour, where a sideways jolt changed nothing (G4)", moved.sum() > 100, int(moved.sum()))
    check("...and keeps the strip's average where it was, so it is never a flash",
          abs(rippled.mean() - scene.mean()) < 0.02 * 255, round(float(abs(rippled.mean() - scene.mean())), 2))
    check("...and settles back within 0.7 s", np.allclose(beat.shake(scene, 0.7, 1.0, 1.0), scene))
    ring = beat.shake(scene[:200], 0.05, 1.0, 1.0, ring=True)
    check("round the monitor the ripple meets itself without a seam",
          abs(float(ring[0, 0] - ring[-1, 0])) < 0.35 * float(scene[0, 0]) * 0.35)
    dark = np.tile(np.array([[2.0, 1.2, 0.4]], np.float32), (240, 1))
    lifted = beat.bounce(dark, 120, 0.05, 1.0, 0.8)
    check("a dark scene glows on the beat in its own hue (G3: was 2/255)",
          lifted.max() - dark.max() > 10 and lifted[:, 0].max() > lifted[:, 2].max(), round(float(lifted.max()), 1))
    black = np.zeros((240, 3), np.float32)
    check("...while true black (bars, a black frame) stays black", np.array_equal(beat.bounce(black, 120, 0.05, 1.0, 1.0), black))
    wide = beat.bounce(scene, 120, 0.2, 1.0, 0.8)
    check("the wave reaches far along the strip (G1: was about a tenth)",
          int((np.abs(wide - scene).max(axis=1) > 0.1 * scene.max(axis=1)).sum()) > 60)

    import ambilight.engine as eng
    from ambilight.config import DEFAULTS
    cfg = {**DEFAULTS, "led_count": 240, "left_led": 0, "right_led": 239, "fade_leds": 0}

    def frame(**settings):
        stage = eng.OutputStage()
        stage.meter = beat.BassMeter()
        stage.meter.last_beat, stage.meter.strength = 10.0, 1.0
        stage.set_target(np.tile(np.float32([[0.6, 0.4, 0.2]]), (240, 1)), 9.0)
        return stage.frame(10.05, {**cfg, **settings})

    check("off by default: a beat changes nothing", np.array_equal(frame(), frame(bass_bounce=0.0, bass_shake=0.0)))
    check("with Bounce on, a beat lights the strip up from the middle", int(frame(bass_bounce=1.0)[100:140].sum())
          > int(frame()[100:140].sum()))
    check("only a beat effect makes the hub listen", not eng.wants_sound(cfg) and eng.wants_sound({**cfg, "bass_shake": 0.2}))

    class Ears:
        """A capture that can hear, noting whether the engine asked it to."""
        name, hears, opened = "ears", True, []

        @staticmethod
        def available():
            return True

        @staticmethod
        def display_count():
            return 1

        def open(self, monitor_index, band_height):
            Ears.opened.append(self.listener is not None)
            return 600, 150

        def grab(self):
            return np.full((150, 600, 3), 90, np.uint8)

        grab_full = grab

        def grab_search(self, rows):
            return self.grab()

        def set_inset(self, rows):
            pass

        def close(self):
            pass

    real = (eng.CAPTURES, eng.screen_locked)
    eng.CAPTURES, eng.screen_locked = {"ears": Ears}, (lambda: False)
    try:
        config = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-beat-"), "config.json"))
        config.update({"wled_ip": "127.0.0.1", "wled_port": FAKE_WLED_UDP, "led_count": 60, "letterbox": False})
        engine = eng.Engine(config)
        engine.start()
        time.sleep(0.4)
        config.update({"bass_bounce": 0.6})
        time.sleep(0.6)
        config.update({"bass_bounce": 0.0})
        time.sleep(0.6)
        engine.stop()
    finally:
        eng.CAPTURES, eng.screen_locked = real
    check("the sound is heard only while a beat effect is on: off, then on, then off again",
          Ears.opened[:3] == [False, True, False], Ears.opened)


def test_fire_glow():
    print("\n35b. Fire: a flame's white-hot core lights the wall warm, not white")
    from ambilight import color
    from ambilight.config import DEFAULTS, PROFILES

    immersive = {**DEFAULTS, **PROFILES["immersive"]}

    def strip(rows, cfg):
        """Screen colours (0..255 sRGB) -> LED drive, through grading and the LED stage as sync runs them."""
        linear = color.SRGB_LUT[np.array(rows, np.int64)]
        return color.to_led(color.grade(linear, cfg), cfg)

    def warmth(led):
        """An LED's saturation: 0 is white on the wall, 1 a pure colour."""
        return 1.0 - led.min() / max(led.max(), 1e-6)

    cores = [(255, 252, 240), (255, 246, 222), (255, 255, 255)]
    flames = [(255, 214, 130), (255, 180, 70), (250, 140, 40), (230, 100, 25), (170, 40, 10)]
    dark = [(10, 8, 8)] * 30
    fire = strip(cores + flames * 2 + dark, immersive)
    plain = strip(cores + flames * 2 + dark, {**immersive, "immersion": 0.0})
    for k, name in enumerate(("white-hot", "pale yellow", "clipped white")):
        check(f"a {name} flame core in Immersive shows as fire, not white",
              warmth(fire[k]) >= 0.8 and fire[k][2] < 0.25 * fire[k][0], fire[k].round())
        check(f"...and stays as bright as before ({name})", fire[k].max() >= 0.95 * plain[k].max(),
              (plain[k].round(), fire[k].round()))
    check("the flames themselves keep their colour", all(warmth(fire[k]) >= 0.9 for k in range(3, 8)))
    check("with immersion off a flame core is left as the screen shows it",
          warmth(plain[0]) < warmth(fire[0]) - 0.15, plain[0].round())

    page = strip([(250, 248, 240)] * 30 + [(40, 40, 40)] * 10, immersive)
    check("a cream page with no fire in the frame stays white", warmth(page[0]) < 0.6, page[0].round())
    sky = strip([(255, 255, 255)] * 5 + [(60, 120, 230)] * 20 + dark, immersive)
    same_sky = strip([(255, 255, 255)] * 5 + [(60, 120, 230)] * 20 + dark, {**immersive, "immersion": 0.0})
    check("white clouds in a blue sky are not warmed", warmth(sky[0]) <= warmth(same_sky[0]) + 0.05,
          (same_sky[0].round(), sky[0].round()))
    face = [(225, 170, 140)]
    lit = strip(face + flames * 2 + dark, immersive)
    unlit = strip(face + [(80, 80, 80)] * 10 + dark, immersive)
    check("a face lit by a fire keeps its skin tone", abs(warmth(lit[0]) - warmth(unlit[0])) < 0.1,
          (unlit[0].round(), lit[0].round()))


def test_user_profiles():
    print("\n36. Your own profiles: save the whole look, switch, delete")
    from ambilight.config import MAX_PROFILES, PROFILE_KEYS, PROFILES

    path = os.path.join(tempfile.mkdtemp(prefix="ambilight-profiles-"), "config.json")
    cfg = Config(path)
    cfg.update({"immersion": 0.8, "saturation": 1.6, "blend": 0.45, "mouse_sync": True})
    cfg.save_profile("Immersive")
    check("saving keeps every part of the look", set(cfg.get("user_profiles")["Immersive"]) == set(PROFILE_KEYS))
    check("and marks it as the active profile", cfg.get("profile") == "Immersive")
    cfg.update({"immersion": 0.0, "saturation": 1.2, "blend": 0.3, "mouse_sync": False})
    cfg.save_profile("Everyday")
    cfg.apply_profile("Immersive")
    look = {k: cfg.get(k) for k in ("immersion", "saturation", "blend", "mouse_sync")}
    check("switching back restores all of it", look == {"immersion": 0.8, "saturation": 1.6, "blend": 0.45,
                                                        "mouse_sync": True}, look)
    check("profiles survive a restart", set(Config(path).get("user_profiles")) == {"Immersive", "Everyday"})
    cfg.apply_profile("immersive")
    check("a built-in mode brings its own whole look", all(cfg.get(k) == v for k, v in PROFILES["immersive"].items()))

    def refused(action, name):
        try:
            action(name)
        except ValueError as exc:
            return str(exc)
        return ""

    check("an unknown profile is refused", "unknown profile" in refused(cfg.apply_profile, "Nope"))
    check("a built-in name cannot be overwritten", "built-in" in refused(cfg.save_profile, "vivid"))
    check("odd names are refused", refused(cfg.save_profile, "../../etc") != ""
          and refused(cfg.save_profile, "") != "" and refused(cfg.save_profile, "x" * 40) != "")
    cfg.delete_profile("Everyday")
    check("a profile can be deleted", set(cfg.get("user_profiles")) == {"Immersive"})
    check("deleting one that does not exist is refused", "unknown profile" in refused(cfg.delete_profile, "Everyday"))
    with open(path) as fh:
        stored = json.load(fh)
    stored["user_profiles"]["Bad"] = {"saturation": "lots", "evil": 1}
    stored["user_profiles"]["Fine"] = {"saturation": 9}
    with open(path, "w") as fh:
        json.dump(stored, fh)
    reloaded = Config(path).get("user_profiles")
    check("a damaged profile on disk is dropped, a good one is clamped and kept",
          "Bad" not in reloaded and reloaded["Fine"]["saturation"] == 3.0, reloaded)

    cfg = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-looks-"), "config.json"))
    cfg.update({"saturation": 1.6})
    for name in ("Day", "Late night", "Film"):
        cfg.save_profile(name)
    cfg.apply_profile("Late night")
    cfg.rename_profile("Late night", "Night")
    check("a look can be renamed, keeping its place in the list",
          list(cfg.get("user_profiles")) == ["Day", "Night", "Film"])
    check("...and the look in use follows the new name", cfg.get("profile") == "Night")
    check("renaming onto another of your looks is refused", "already" in refused(lambda n: cfg.rename_profile("Day", n), "Film"))
    check("...onto a built-in too", "built-in" in refused(lambda n: cfg.rename_profile("Day", n), "vivid"))
    check("...and a look that does not exist cannot be renamed", "unknown" in refused(lambda n: cfg.rename_profile(n, "X"), "Gone"))
    check("renaming to the same name changes nothing", cfg.rename_profile("Day", "Day") == [])
    cfg.duplicate_profile("immersive", "My immersive")
    copy = cfg.get("user_profiles")["My immersive"]
    check("duplicating a built-in makes a whole look of your own with its settings",
          set(copy) == set(PROFILE_KEYS) and all(copy[k] == v for k, v in PROFILES["immersive"].items()))
    check("...and leaves the built-in itself as it was", "immersive" not in cfg.get("user_profiles"))
    cfg.duplicate_profile("Night", "Night 2")
    check("duplicating one of yours copies it exactly",
          cfg.get("user_profiles")["Night 2"] == cfg.get("user_profiles")["Night"])
    check("a copy needs a name of its own", "already" in refused(lambda n: cfg.duplicate_profile("Day", n), "Film"))
    for i in range(MAX_PROFILES - len(cfg.get("user_profiles"))):
        cfg.duplicate_profile("Day", f"Day {i}")
    check("copies stop at the limit of 20 looks", "at most" in refused(lambda n: cfg.duplicate_profile("Day", n), "One more"))


def test_dark_scenes():
    print("\n40c. Dark scenes: shadows stay visible and responsive")
    from ambilight import color
    from ambilight.config import DEFAULTS, PROFILE_KEYS

    with tempfile.TemporaryDirectory() as folder:
        cfg = {**Config(os.path.join(folder, "config.json")).snapshot(), "led_gamma": 1.5, "contrast": 1.8}
    off, on = {**cfg, "dark_boost": 0.0}, {**cfg, "dark_boost": 1.0}
    dark = np.full((100, 3), 0.05, np.float32)
    check("in a dark scene, dim light drives the LEDs several times harder",
          color.to_led(dark, on).mean() > 3 * color.to_led(dark, off).mean(),
          (round(float(color.to_led(dark, off).mean()), 2), round(float(color.to_led(dark, on).mean()), 2)))
    bright = np.full((100, 3), 0.7, np.float32)
    bright[0] = 0.12
    check("in a bright scene nothing changes, so it keeps its punch",
          np.allclose(color.to_led(bright, on), color.to_led(bright, off)))
    desktop = np.full((100, 3), 0.118, np.float32)     # a dark-mode editor's background
    desktop[::10] = 0.8                                # ...and its text
    check("a dark-mode desktop is barely lifted, so everyday use is not brighter",
          color.to_led(desktop, {**cfg, "dark_boost": DEFAULTS["dark_boost"]}).sum() < 1.15 * color.to_led(desktop, off).sum())
    ramp = np.linspace(0.0, 0.2, 100, dtype=np.float32)[:, None].repeat(3, axis=1)
    lifted = color.to_led(ramp, on).sum(axis=1)
    check("a darker pixel never comes out brighter than a lighter one", bool(np.all(np.diff(lifted) >= -1e-6)))
    ember = np.tile(np.array([[0.09, 0.035, 0.01]], np.float32), (100, 1))
    a, b = color.to_led(ember, off)[0], color.to_led(ember, on)[0]
    check("a dark colour gets brighter without changing colour", b.sum() > a.sum() and np.allclose(a / a.sum(), b / b.sum(), atol=1e-3),
          (a.round(2), b.round(2)))
    check("at zero the curve is exactly as before", np.array_equal(color.to_led(dark, off), color.to_led(dark, {**cfg, "dark_boost": 0})))
    check("it is on out of the box", DEFAULTS["dark_boost"] > 0, DEFAULTS["dark_boost"])
    check("a profile of your own keeps it", "dark_boost" in PROFILE_KEYS)


def test_adaptive_brightness():
    print("\n40d. Adaptive brightness: each LED's level from what it faces")
    from ambilight import color
    from ambilight.config import DEFAULTS, PROFILE_KEYS

    with tempfile.TemporaryDirectory() as folder:
        cfg = {**Config(os.path.join(folder, "config.json")).snapshot(), "dark_boost": 0.0}
    off, on = {**cfg, "adaptive": 0.0}, {**cfg, "adaptive": 1.0}
    white = np.full((100, 3), 0.95, np.float32)
    check("a bright white screen no longer drives every LED near full",
          color.to_led(white, on).mean() < 0.75 * color.to_led(white, off).mean(),
          (round(float(color.to_led(white, off).mean())), round(float(color.to_led(white, on).mean()))))
    mixed = np.full((100, 3), 0.45, np.float32)
    mixed[40:50] = 0.9
    plain, shaped = color.to_led(mixed, off).sum(axis=1), color.to_led(mixed, on).sum(axis=1)
    check("LEDs facing a highlight stand out more against the rest",
          shaped[45] / shaped[0] > 1.3 * plain[45] / plain[0], (round(plain[45] / plain[0], 2), round(shaped[45] / shaped[0], 2)))
    check("...and keep most of their brightness", shaped[45] > 0.8 * plain[45], (plain[45], shaped[45]))
    check("dull parts dim but never switch off", shaped[0] > 0.2 * plain[0], (plain[0], shaped[0]))
    dark = np.full((100, 3), 0.08, np.float32)
    check("a dark scene is not dimmed further", np.allclose(color.to_led(dark, on), color.to_led(dark, off), rtol=0.02))
    ramp = np.linspace(0.0, 1.0, 100, dtype=np.float32)[:, None].repeat(3, axis=1)
    check("a brighter target never shows dimmer than a darker one",
          bool(np.all(np.diff(color.to_led(ramp, on).sum(axis=1)) >= -1e-4)))
    tint = np.tile(np.array([[0.9, 0.5, 0.1]], np.float32), (100, 1))
    tint[:50] *= 0.5
    a, b = color.to_led(tint, off)[10], color.to_led(tint, on)[10]
    check("only the level changes, never the colour", np.allclose(a / a.sum(), b / b.sum(), atol=1e-3))
    check("at zero nothing changes", np.array_equal(color.to_led(mixed, off), color.to_led(mixed, {**cfg, "adaptive": 0})))
    check("it is on out of the box, and profiles keep it", DEFAULTS["adaptive"] > 0 and "adaptive" in PROFILE_KEYS)

    # Saturated blue and red carry little luminance but are as vivid as yellow: they must not fade beside it.
    bars = np.repeat(np.array([[0.92, 0.86, 0.16], [0.12, 0.24, 0.90], [0.90, 0.12, 0.12],
                               [0.12, 0.78, 0.24], [0.86, 0.16, 0.75]], np.float32), 20, axis=0)
    levels = color.to_led(bars, {**cfg, "adaptive": DEFAULTS["adaptive"]}).max(axis=1)
    check("a saturated blue or red beside bright yellow keeps most of its level at the default setting",
          levels.min() > 0.6 * levels.max(), [round(float(levels[i * 20] / levels.max()), 2) for i in range(5)])


def test_hue_balance():
    print("\n40e. Hue balance: dim hues (blue, red) lifted within the headroom, nothing dimmed")
    from ambilight import color
    from ambilight.config import DEFAULTS, PROFILE_KEYS

    def level(rgb, amount, brightness=1.0):
        return color.hue_balance(np.array([rgb], np.float32), amount, brightness)[0]

    blue, red, green, yellow = (0, 0, 150), (150, 0, 0), (0, 150, 0), (150, 150, 0)
    check("at zero nothing changes", np.array_equal(level(blue, 0.0, 0.5), np.array(blue, np.float32)))
    check("blue, which gives the least light, is lifted", level(blue, 0.5, 0.5)[2] > 1.3 * 150, level(blue, 0.5, 0.5))
    check("red is lifted too, by less", 150 < level(red, 0.5, 0.5)[0] < level(blue, 0.5, 0.5)[2], level(red, 0.5, 0.5))
    check("green and yellow, which give the most light, are left alone: nothing is ever dimmed",
          np.array_equal(level(green, 1.0, 0.5), np.array(green, np.float32))
          and np.array_equal(level(yellow, 1.0, 0.5), np.array(yellow, np.float32)))
    check("white and greys keep their level: only colour is rebalanced",
          np.allclose(level((150, 150, 150), 1.0, 0.5), 150) and np.allclose(level((40, 40, 40), 1.0, 0.5), 40))
    mixed = level((0, 90, 150), 0.7, 0.5)
    check("the colour never changes, only how bright it is", np.allclose(mixed / mixed.max(), np.array([0, 90, 150]) / 150, atol=1e-4))
    check("nothing is pushed past what the strip can show at the current brightness",
          level(blue, 1.0, 0.8).max() * 0.8 <= 255.001 and level((0, 0, 250), 1.0, 1.0).max() <= 255.001,
          (level(blue, 1.0, 0.8), level((0, 0, 250), 1.0, 1.0)))
    check("at full brightness with the strip already at full, there is no headroom and nothing changes",
          np.allclose(level((0, 0, 255), 1.0, 1.0), (0, 0, 255)))
    check("black stays black", np.array_equal(level((0, 0, 0), 1.0, 0.5), np.zeros(3, np.float32)))

    with tempfile.TemporaryDirectory() as folder:
        cfg = {**Config(os.path.join(folder, "config.json")).snapshot(), "dark_boost": 0.0, "brightness": 0.55}
    bars = np.repeat(np.array([[0.92, 0.86, 0.16], [0.12, 0.24, 0.90], [0.90, 0.12, 0.12],
                               [0.12, 0.78, 0.24], [0.86, 0.16, 0.75]], np.float32), 20, axis=0)
    light = lambda drive: (drive @ color.CHANNEL_LIGHT)[[0, 20, 40, 60, 80]]      # what the eye gets from each bar
    flat = light(color.to_led(bars, {**cfg, "hue_balance": 0.0}))
    balanced = light(color.to_led(bars, cfg))
    check("on colour bars the dimmest hue's light moves closer to the brightest hue's",
          balanced.min() / balanced.max() > flat.min() / flat.max() + 0.05,
          (round(float(flat.min() / flat.max()), 2), round(float(balanced.min() / balanced.max()), 2)))
    check("and no bar shows less light than before", bool(np.all(balanced >= flat - 1e-3)), np.round(balanced / flat, 2))
    check("it is on by default, between none and full, and a profile of your own keeps it",
          0.0 < DEFAULTS["hue_balance"] < 1.0 and "hue_balance" in PROFILE_KEYS)
    check("it is a checked setting", Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-hb-"), "c.json")).validate("hue_balance", "0.4") == 0.4)


def test_enriched():
    print("\n40b. The Enriched tone: deep blacks, bright highlights, true colour")
    from ambilight import color, enrich
    from ambilight.config import DEFAULTS, PROFILE_KEYS, PROFILES

    check("Vivid uses the tone in full, Immersive in part so its flood still reaches the shadows",
          PROFILES["vivid"]["enrich"] == 1.0 and 0.0 < PROFILES["immersive"]["enrich"] < 1.0)
    check("every mode sets the tone, so switching modes always sets it",
          all("enrich" in mode for mode in PROFILES.values()) and "enrich" in PROFILE_KEYS)

    # Answers recorded from colour-science 0.4.7 (sRGB_to_XYZ + XYZ_to_Oklab and
    # back), including out-of-gamut inputs the gamut search produces.
    ref = np.load(os.path.join(ROOT, "tests", "oklab_reference.npz"))
    check("sRGB to Oklab matches colour-science",
          np.allclose(enrich.to_oklab(ref["rgb"]), ref["rgb_to_lab"], rtol=0, atol=1e-12))
    check("Oklab to sRGB matches colour-science",
          np.allclose(enrich.from_oklab(ref["lab"]), ref["lab_to_rgb"], rtol=0, atol=1e-12))

    lightness = enrich.to_oklab

    greys = np.array([[0.12] * 3, [0.5] * 3, [0.9] * 3], np.float32)
    toned = enrich.apply(greys, 1.0)
    before, after = lightness(greys)[:, 0], lightness(toned)[:, 0]
    check("dark stays dark: a dark grey gets darker", after[0] < before[0] - 0.02, (before[0], after[0]))
    check("bright stays bright: a light grey gets brighter", after[2] > before[2] + 0.01, (before[2], after[2]))
    check("greys stay neutral", float(np.ptp(toned, axis=1).max()) < 2e-3, toned)

    rng = np.random.default_rng(7)
    colours = rng.random((1000, 3)).astype(np.float32)
    out = enrich.apply(colours, 1.0)
    check("every colour stays something the LEDs can show", float(out.min()) >= 0.0 and float(out.max()) <= 1.0)
    lab_in, lab_out = lightness(colours), lightness(out)
    chroma_in = np.hypot(lab_in[:, 1], lab_in[:, 2])
    vivid = chroma_in > 0.05
    hue_in = np.degrees(np.arctan2(lab_in[vivid, 2], lab_in[vivid, 1]))
    hue_out = np.degrees(np.arctan2(lab_out[vivid, 2], lab_out[vivid, 1]))
    drift = np.abs((hue_out - hue_in + 180.0) % 360.0 - 180.0)
    check("true colour: hues hold within 2 degrees", float(np.percentile(drift, 99)) < 2.0, float(drift.max()))
    in_gamut = vivid & (np.abs(out - colours).max(axis=1) < 0.5)
    ratio_in = chroma_in[in_gamut] / np.maximum(lab_in[in_gamut, 0], 1e-6)
    ratio_out = np.hypot(lab_out[in_gamut, 1], lab_out[in_gamut, 2]) / np.maximum(lab_out[in_gamut, 0], 1e-6)
    check("...and saturation is kept, not boosted (median within 5%)",
          abs(float(np.median(ratio_out / ratio_in)) - 1.0) < 0.05, float(np.median(ratio_out / ratio_in)))
    check("at zero it leaves colours untouched", np.array_equal(enrich.apply(colours, 0.0), colours))

    def film(low, high, saturation=0.5):
        hsv = np.stack([rng.random(120), np.full(120, saturation), rng.uniform(low, high, 120)], 1).astype(np.float32)
        return color.srgb_to_linear(color.hsv_to_rgb(hsv))

    def drive(linear, tone):
        # The tone stage alone: true colour, no flood, no hue balance, the old default contrast.
        look = {**DEFAULTS, "saturation": 1.0, "immersion": 0.0, "contrast": 1.5, "enrich": tone, "hue_balance": 0.0}
        return color.to_led(color.grade(linear, look), look).max(axis=1)

    for name, scene in (("a dark film scene", film(0.05, 0.35)), ("a mid-lit one", film(0.2, 0.6)),
                        ("a bright one", film(0.5, 1.0, 0.4)), ("a vivid one", film(0.6, 1.0, 0.95))):
        flat, toned = drive(scene, 0.0), drive(scene, 1.0)
        check(f"the tone gives {name} contrast without dimming it overall (within 3%)",
              toned.mean() >= 0.97 * flat.mean(), (round(float(flat.mean()), 1), round(float(toned.mean()), 1)))
        order = np.argsort(flat)
        check(f"...its dimmest LEDs still sink and its brightest are not dimmed by more than 3% (they sit at the ceiling)",
              toned[order[:12]].mean() < flat[order[:12]].mean() and toned[order[-12:]].mean() >= 0.97 * flat[order[-12:]].mean(),
              (round(float(flat[order[:12]].mean()), 1), round(float(toned[order[:12]].mean()), 1),
               round(float(flat[order[-12:]].mean()), 1), round(float(toned[order[-12:]].mean()), 1)))

    frame = rng.random((400, 3)).astype(np.float32)
    enrich.apply(frame, 1.0)
    timings = []
    for _ in range(30):
        started = time.perf_counter()
        enrich.apply(frame, 1.0)
        timings.append(time.perf_counter() - started)
    check("fast enough for 60 fps: under 4 ms for 400 LEDs", float(np.median(timings)) < 0.004,
          f"{np.median(timings) * 1000:.2f} ms")
    base = {"saturation": 1.0, "immersion": 0.0}
    linear = color.srgb_to_linear(colours[:20])
    check("the colour grade applies it when the mode asks",
          not np.allclose(color.grade(linear, {**base, "enrich": 1.0}), color.grade(linear, {**base, "enrich": 0.0})))


def test_sync_modes():
    print("\n40. Sync modes: Vivid and Immersive, each a whole tuned look")
    import json as json_module
    from ambilight import color
    from ambilight.config import DEFAULTS, PROFILE_KEYS, PROFILES
    from ambilight.smoothing import settle_seconds

    vivid, immersive = PROFILES.get("vivid", {}), PROFILES.get("immersive", {})
    check("the sync modes are Vivid and Immersive", list(PROFILES) == ["vivid", "immersive"], list(PROFILES))
    recipe = {"blend", "fps", "saturation", "immersion", "spread", "colour_weight", "enrich", "contrast", "dark_boost",
              "white_kelvin", "bass_bounce", "bass_shake"}
    check("Vivid and Immersive switch the beat off",
          all(PROFILES[m]["bass_bounce"] == PROFILES[m]["bass_shake"] == 0 for m in ("vivid", "immersive")))
    check("each sets motion, colour, warmth, beat and shadow depth together",
          all(set(look) == recipe for look in PROFILES.values()),
          (sorted(vivid), sorted(immersive)))
    check("and only what a profile of your own also keeps", recipe <= set(PROFILE_KEYS))
    check("a fresh install starts in Vivid, exactly",
          DEFAULTS["profile"] == "vivid" and all(DEFAULTS[k] == v for k, v in vivid.items()))

    check("Immersive glides: a change settles in about a third of a second, slower than Vivid",
          0.28 < settle_seconds(immersive["blend"]) < 0.45 and settle_seconds(immersive["blend"]) > settle_seconds(vivid["blend"]),
          round(settle_seconds(immersive["blend"]), 3))
    check("...and adaptive pace, on by default, speeds it up when the picture moves fast", DEFAULTS["adaptive_pace"] > 0)

    def scene(background, spots=()):
        frame = np.tile(np.asarray(background, np.float32), (97, 1))
        for first, last, colour in spots:
            frame[first:last] = colour
        return color.srgb_to_linear(frame)

    def levels(mode, frame):
        look = {**DEFAULTS, **mode, "brightness": 1.0}
        return (color.to_led(color.grade(frame, look), look) / 255.0).max(axis=1)

    # A dark film scene: a dim blue room with one warm lamp. At contrast 2.0 every
    # old mode left nine tenths of the strip dark here.
    dark_film = scene([0.04, 0.05, 0.09], [(40, 50, [0.9, 0.7, 0.4])])
    lit = {name: float(np.mean(levels(mode, dark_film) > 0.05)) for name, mode in PROFILES.items()}
    check("Immersive lights the whole room in a dark film scene; Vivid keeps it to the lamp",
          lit["immersive"] > 0.9 and lit["vivid"] < 0.3, lit)
    black = scene([0.003, 0.003, 0.003])
    check("while a black frame stays dark in both", all(levels(mode, black).max() < 0.02 for mode in PROFILES.values()))
    check("Immersive's highlights still reach near full", levels(immersive, dark_film).max() > 0.95,
          round(float(levels(immersive, dark_film).max()), 3))

    muted = color.srgb_to_linear(np.array([(0.6, 0.35, 0.3)], np.float32))
    punch = [color.rgb_to_hsv(color.grade(muted, mode))[0, 1] for mode in (vivid, immersive)]
    check("Immersive makes colour punchier than Vivid", punch[1] > punch[0] + 0.03, np.round(punch, 3))

    band = np.full((40, 100, 3), 128, np.uint8)
    band[:, 45:55] = (230, 30, 30)
    mapping = {"starts": np.array([0]), "stops": np.array([100])}
    red_share = [float(c[0, 0] / c[0, 1]) for c in
                 (processing.zone_colors(band, mapping, mode["colour_weight"]) for mode in (vivid, immersive))]
    check("Immersive picks a small vivid object out of a grey edge more strongly", red_share[1] > red_share[0],
          np.round(red_share, 2))

    one = np.zeros((9, 3), np.float32)
    one[4] = 1.0
    shared = [processing.spread(one, mode["spread"])[3, 0] for mode in (vivid, immersive)]
    check("Immersive washes light wider than Vivid", shared[1] > shared[0], np.round(shared, 3))

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "config.json")
        for old in ("movie", "game", "enriched"):
            with open(path, "w") as f:
                json_module.dump({"profile": old, "blend": 0.8, "saturation": 1.35}, f)
            cfg = Config(path)
            check(f"a config left in the retired {old!r} mode keeps its look but drops the name",
                  cfg.get("profile") == "" and cfg.get("blend") == 0.8 and cfg.get("saturation") == 1.35)
        with open(path, "w") as f:
            json_module.dump({"profile": "game", "user_profiles": {"game": {"blend": 0.7}}}, f)
        check("...unless it is a saved look of your own by that name", Config(path).get("profile") == "game")


def test_colour_locks():
    print("\n37. Locked colours: kept ones stay, temporary ones end with the hub")
    from ambilight.zones import normalize

    zones = normalize([{"start": 0, "end": 9, "mode": "solid", "color": "#ff0000"},
                       {"start": 20, "end": 29, "mode": "solid", "color": "#0000ff", "keep": False}], 97)
    check("a lock is kept unless asked otherwise", zones[0]["keep"] is True and zones[1]["keep"] is False)
    check("odd keep values are read safely", normalize([{"start": 0, "end": 1, "keep": "no"}], 97)[0]["keep"] is False)
    path = os.path.join(tempfile.mkdtemp(prefix="ambilight-locks-"), "config.json")
    cfg = Config(path)
    cfg.update({"led_count": 97, "zones": zones})
    check("while the hub runs, both locks are live", len(cfg.get("zones")) == 2)
    after = Config(path).get("zones")
    check("after a restart only the kept lock remains", [z["color"] for z in after] == ["#ff0000"], after)


class FakeBedBoard:
    """The parts of WLED the bed strip uses: live state, presets, config, reboot."""

    def __init__(self, ps=1):
        self.live = {"ps": ps, "mainseg": 0, "seg": [{"id": 0, "start": 0, "stop": 180, "on": True}]}
        self.sent, self.configs, self.reboots = [], [], 0
        self.hw = {"hw": {"led": {"total": 180, "maxpwr": 4000, "ins": [
            {"start": 0, "len": 180, "pin": [2], "order": 0, "type": 22, "maxpwr": 4000, "ledma": 55}]}}}
        self.paused_while_writing = []

    def state(self):
        return self.live

    def apply(self, payload):
        self.sent.append(payload)
        return True

    def set_preset(self, pid):
        self.sent.append({"ps": pid})
        return True

    def config(self):
        return self.hw

    def set_config(self, payload):
        self.configs.append(payload)
        return True

    def reboot(self):
        self.reboots += 1
        return True


def test_vivid_mode():
    print("\n40c. Vivid: bright highlights and deep shadows, close to the screen")
    from ambilight import color
    from ambilight.config import DEFAULTS, PROFILES

    rng = np.random.default_rng(3)

    def scene(background, spots=()):
        frame = np.tile(np.asarray(background, np.float32), (97, 1))
        for first, last, colour in spots:
            frame[first:last] = colour
        return frame + rng.normal(0, 0.004, frame.shape).astype(np.float32).clip(-0.01, 0.01)

    dark_film = scene([0.04, 0.05, 0.09], [(40, 50, [0.9, 0.7, 0.4])])
    night_city = scene([0.02, 0.02, 0.04], [(10, 20, [0.9, 0.2, 0.1]), (60, 70, [0.2, 0.5, 0.95])])
    mid_grade = scene([0.2, 0.18, 0.15], [(30, 45, [0.7, 0.55, 0.35])])
    black = scene([0.0, 0.0, 0.0])

    def look(name, frame):
        """How the LEDs would show `frame` in a mode: (average, brightest, dimmest tenth) of each LED's level."""
        cfg = {**DEFAULTS, **PROFILES[name], "brightness": 1.0}
        level = (color.to_led(color.grade(frame, cfg), cfg) / 255.0).max(axis=1)
        return float(level.mean()), float(level.max()), float(np.percentile(level, 10))

    shadows = {name: np.mean([look(name, f)[2] for f in (dark_film, night_city, mid_grade)]) for name in PROFILES}
    peak = {name: np.mean([look(name, f)[1] for f in (dark_film, night_city, mid_grade)]) for name in PROFILES}
    check("Vivid highlights reach full", peak["vivid"] > 0.98, {k: round(v, 3) for k, v in peak.items()})
    check("its shadows are deeper than Immersive's", shadows["vivid"] <= shadows["immersive"],
          {k: round(v, 3) for k, v in shadows.items()})
    check("while a black frame stays dark", look("vivid", black)[0] < 0.015, round(look("vivid", black)[0], 4))
    edges = [look("vivid", scene([0.1, 0.4, 0.15], [(a, b, c)]))[1] for a, b, c in
             ((0, 30, [0.9, 0.1, 0.1]), (0, 30, [0.1, 0.2, 0.9]), (0, 30, [0.9, 0.8, 0.1]))]
    check("saturated red, blue and yellow each keep over 80% of full in Vivid, so they read as colours",
          min(edges) > 0.8, [round(e, 2) for e in edges])


def test_bed_strip():
    print("\n39. A bed strip on the board's second output: screen, effect, colour or off")
    from urllib.parse import parse_qs
    from ambilight import bed, board, routes
    from ambilight.engine import OutputStage
    from ambilight.intent import SyncIntent
    from ambilight.safety import estimate_ma
    from ambilight.smoothing import settle_seconds

    path = os.path.join(tempfile.mkdtemp(prefix="ambilight-bed-"), "config.json")
    cfg = Config(path)
    cfg.update({"led_count": 180, "sync_preset": 1})

    def refused(changes):
        try:
            cfg.update(changes)
        except ValueError as exc:
            return str(exc)
        return ""

    check("bed settings are checked like any other",
          refused({"bed_mode": "disco"}) and refused({"bed_colour": "red"}) and refused({"bed_side": "under"}))
    check("bed settings start off: no bed strip until it is set up", cfg.get("bed_leds") == 0)

    # --- the segment layout the board should have --------------------------
    snap = cfg.snapshot()
    check("without a bed strip the hub never touches the board's segments", bed.layout(snap) is None)
    cfg.update({"bed_leds": 60, "bed_mode": "screen"})
    check("following the screen, one segment spans both strips so the stream reaches the bed",
          bed.layout(cfg.snapshot()) == [{"id": 0, "start": 0, "stop": 240}, {"id": 1, "stop": 0}])
    cfg.update({"bed_mode": "effect", "bed_effect": 9, "bed_speed": 200, "bed_brightness": 0.5})
    seg0, seg1 = bed.layout(cfg.snapshot())
    check("an effect gets a segment of its own, after the monitor strip",
          seg0 == {"id": 0, "start": 0, "stop": 180} and (seg1["start"], seg1["stop"]) == (180, 240))
    check("running the chosen effect at the chosen speed and brightness",
          (seg1["fx"], seg1["sx"], seg1["bri"], seg1["on"]) == (9, 200, 128, True), seg1)
    check("and is never selected, so the board's own page only changes the monitor strip", seg1["sel"] is False)
    cfg.update({"bed_mode": "colour", "bed_colour": "ff8000"})
    seg1 = bed.layout(cfg.snapshot())[1]
    check("a picked colour is a solid segment in that colour", seg1["fx"] == 0 and seg1["col"] == [[255, 128, 0]])
    cfg.update({"bed_mode": "off"})
    check("off turns just the bed segment off", bed.layout(cfg.snapshot())[1]["on"] is False)

    cfg.update({"bed_mode": "colour"})
    want = bed.layout(cfg.snapshot())
    right = {"ps": 1, "mainseg": 0, "seg": [
        {"id": 0, "start": 0, "stop": 180, "on": True, "fx": 0},
        {"id": 1, "start": 180, "stop": 240, "on": True, "fx": 0, "sx": 128, "bri": 128,
         "col": [[255, 128, 0], [0, 0, 0], [0, 0, 0]]}]}
    check("a board already in the wanted layout matches", bed.matches(right, want))
    wrong = json.loads(json.dumps(right))
    wrong["seg"][1]["col"][0] = [0, 0, 255]
    check("a different bed colour does not", not bed.matches(wrong, want))
    check("nor does a missing bed segment (a preset wiped it)", not bed.matches({**right, "seg": right["seg"][:1]}, want))
    check("nor a moved main segment, which the stream would follow",
          not bed.matches({**right, "mainseg": 1}, want))
    spanning = bed.layout({**cfg.snapshot(), "bed_mode": "screen"})
    check("a left-over bed segment does not match the spanning layout",
          not bed.matches({**right, "seg": [{"id": 0, "start": 0, "stop": 240}, right["seg"][1]]}, spanning))
    check("and the spanning layout matches once it is gone",
          bed.matches({"mainseg": 0, "seg": [{"id": 0, "start": 0, "stop": 240}]}, spanning))

    # --- keeping the board in that layout ------------------------------------
    now = [100.0]
    boardfake = FakeBedBoard(ps=-1)

    class Engine:
        running = False

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    intent = SyncIntent(cfg, boardfake, Engine(), clock=lambda: now[0])
    keeper = bed.Keeper(cfg, boardfake, intent, clock=lambda: now[0])
    original_apply = boardfake.apply

    def watching_apply(payload):
        boardfake.paused_while_writing.append(intent.paused_until > now[0])
        return original_apply(payload)

    boardfake.apply = watching_apply
    check("outside sync a wrong layout is fixed live, without saving anything",
          keeper.tick(boardfake.live) == "applied" and "psave" not in boardfake.sent[-1]
          and boardfake.sent[-1]["seg"] == want and boardfake.sent[-1]["mainseg"] == 0, boardfake.sent)

    boardfake.sent.clear()
    boardfake.live = {**boardfake.live, "ps": 1}
    check("during sync the sync preset is rewritten with the bed in it", keeper.tick(boardfake.live) == "saved")
    saved, reapplied = boardfake.sent
    check("as a stored command (the board cannot snapshot its state while streaming)",
          saved["psave"] == 1 and saved["o"] is True and saved["n"] == board.SYNC_NAME, saved)
    check("keeping the board's sync look on the monitor segment",
          {k: saved["seg"][0][k] for k in ("fx", "col")} == {k: board.SYNC_LOOK[k] for k in ("fx", "col")}
          and (saved["seg"][0]["start"], saved["seg"][0]["stop"]) == (0, 180))
    check("with the bed segment as wanted", saved["seg"][1] == want[1] and saved["mainseg"] == 0)
    check("then sync is marked active again", reapplied == {"ps": 1})
    check("and the watcher is held off meanwhile, so it cannot read a gap as 'sync stopped'",
          boardfake.paused_while_writing[-1] is True and intent.paused_until == 0.0)
    boardfake.sent.clear()
    check("a second change right away waits (dragging a colour must not wear the flash)",
          keeper.tick(boardfake.live) is None and not boardfake.sent)
    now[0] += bed.SAVE_GAP_S + 0.1
    check("and goes through a moment later", keeper.tick(boardfake.live) == "saved")
    boardfake.sent.clear()
    check("a board in the wanted layout is left alone", keeper.tick(right) is None and not boardfake.sent)
    check("an unreachable board is left alone", keeper.tick(None) is None)

    intent.watchers.append(keeper.tick)
    now[0] += bed.SAVE_GAP_S + 0.1
    boardfake.live = {**right, "seg": right["seg"][:1]}
    intent.tick()
    check("the sync watcher hands every look at the board to the keeper", boardfake.sent, boardfake.sent)

    # --- the board's second output --------------------------------------------
    hw_led = boardfake.hw["hw"]["led"]
    payload = bed.bus_config(hw_led, 180, 2800, 60, 2500, 5)
    monitor, second = payload["hw"]["led"]["ins"]
    check("the monitor output keeps its pin and LEDs", {k: v for k, v in monitor.items() if k != "maxpwr"}
          == {k: v for k, v in hw_led["ins"][0].items() if k != "maxpwr"})
    check("the bed is a second output on the chosen pin (D1 = GPIO5), right after the monitor strip",
          (second["pin"], second["start"], second["len"], second["type"]) == ([5], 180, 60, 22), second)
    check("and the pin can be another, such as RX",
          bed.bus_config(hw_led, 180, 2800, 60, 2500, 3)["hw"]["led"]["ins"][1]["pin"] == [3])
    import re
    picker = re.search(r'<select id="bed-pin">(.*?)</select>', open(PAGE).read(), re.S).group(1)
    offered = re.findall(r'<option value="(\d+)">(\w+)</option>', picker)
    check("the page offers TX (GPIO1) first, the free hardware pin with memory for a long bed strip",
          offered[:1] == [("1", "TX")] and ("3", "RX") in offered, offered)
    swift = open("macapp/Sources/SettingsPage.swift").read()
    app_pins = [(pin, name) for name, pin in re.findall(r'\("(\w+)", (\d+)\)', swift.split("static let pins", 1)[1].split("\n", 1)[0])]
    check("the Mac app offers the same pins in the same order", app_pins == offered, app_pins)
    check("each strip is limited to its own adapter (WLED's per-output limiter)",
          payload["hw"]["led"]["maxpwr"] == 0 and second["maxpwr"] == 2500 and monitor["maxpwr"] == 2800)
    check("realtime data drives only the main segment, so a bed effect keeps running",
          payload["if"]["live"]["mso"] is True and payload["hw"]["led"]["total"] == 240)
    single = bed.bus_config(hw_led, 180, 3800, 0, 2500, 5)["hw"]["led"]
    check("no bed: back to one output under one limit", len(single["ins"]) == 1 and single["maxpwr"] == 3800)
    try:
        bed.bus_config(hw_led, 200, 2800, 60, 2500, 5)
        mismatch = ""
    except ValueError as exc:
        mismatch = str(exc)
    check("a board whose first output is shorter than the monitor strip is refused", "monitor" in mismatch, mismatch)
    longer = bed.bus_config(hw_led, 97, 2800, 60, 2500, 5)["hw"]["led"]
    check("a monitor output longer than the LEDs round the screen is kept, and the bed starts after it",
          longer["ins"][0]["len"] == 180 and longer["ins"][1]["start"] == 180 and longer["total"] == 240)

    class Hub:
        wled = boardfake

        def __init__(self):
            self.cfg = cfg

        def status(self):
            return {}

    hub = Hub()
    hub.intent = intent
    body, code = routes.handle(hub, "/api/bed/setup", parse_qs("leds=72&amps=3"))
    check("setting up the bed strip writes the board's outputs and restarts it",
          code == 200 and boardfake.configs[-1]["hw"]["led"]["ins"][1]["len"] == 72 and boardfake.reboots == 1,
          (code, body))
    check("and remembers the strip and its adapter", cfg.get("bed_leds") == 72 and cfg.get("bed_power_ma") == 3000)
    from ambilight.safety import shares
    check("the board holds the monitor strip to the hub's own share of the adapter",
          boardfake.configs[-1]["hw"]["led"]["ins"][0]["maxpwr"] == round(shares(cfg.snapshot())[0]["power_limit_ma"]))
    check("and remembers where the bed starts on the board", cfg.get("bed_start") == 180)
    body, code = routes.handle(hub, "/api/bed/setup", parse_qs("leds=5000&amps=3"))
    check("an impossible LED count is refused", code == 400 or "error" in body, (code, body))

    class Wizard:
        def count_start(self, reported):
            return {"reported": reported}

    class CountHub(Hub):
        wizard = Wizard()

        class wled:
            @staticmethod
            def led_count():
                return 252

    count_hub = CountHub()
    count_hub.intent = intent
    count_hub.engine = Engine()
    body, _ = routes.handle(count_hub, "/api/wizard/count/start", parse_qs(""))
    check("the LED-count wizard counts the monitor strip, not the bed", body["reported"] == 180, body)

    # --- what the bed shows while following the screen --------------------
    cfg.update({"bed_leds": 30, "bed_mode": "screen", "bed_look": "mood", "bed_brightness": 1.0,
                "bed_power_ma": 20000, "black_level": 0.0})
    red_scene = np.array([[0.8, 0.05, 0.05]] * 120 + [[0.1, 0.1, 0.6]] * 40 + [[0.5, 0.5, 0.5]] * 20, np.float32)
    mood = bed.mood(red_scene)
    check("the mood is the scene's strongest colour, not a muddy average", mood[0] > 3 * mood[2], mood)
    grey = bed.mood(np.full((180, 3), 0.4, np.float32))
    check("a grey scene gives a grey mood", np.ptp(grey) < 0.02, grey)

    frame_cfg = {**cfg.snapshot(), "layout": "frame", "edge_bottom": 60, "edge_right": 30,
                 "edge_top": 60, "edge_left": 30, "bed_look": "side"}
    ring = np.zeros((180, 3), np.float32)
    ring[:60] = [1, 0, 0]
    ring[60:90] = [0, 1, 0]
    ring[90:150] = [0, 0, 1]
    ring[150:] = [1, 1, 0]
    for side, colour in (("bottom", [1, 0, 0]), ("right", [0, 1, 0]), ("top", [0, 0, 1]), ("left", [1, 1, 0])):
        got = bed.side_colours(ring, {**frame_cfg, "bed_side": side}, 30)
        check(f"'{side}' spreads that edge of the screen along the bed", np.allclose(got, colour), got[:2])
    whole = bed.side_colours(ring, {**frame_cfg, "bed_side": "whole"}, 30)
    check("'whole' stretches the full ring along the bed", len(whole) == 30 and np.allclose(whole[0], [1, 0, 0])
          and np.allclose(whole[-1], [1, 1, 0]))
    strip = bed.side_colours(ring, {**frame_cfg, "layout": "strip", "bed_side": "left"}, 30)
    check("a strip under the screen has only one side, so any side means all of it", np.allclose(strip, whole))

    low = ring.copy()
    low[:30] = [1, 0, 0]        # bottom edge, left half red
    low[30:60] = [0, 0, 1]      # bottom edge, right half blue
    blend_cfg = {**frame_cfg, "bed_look": "blend", "bed_reverse": False}
    blended = bed.blend_colours(low, blend_cfg, 30)
    check("the bed sits low, so it starts as the screen's bottom-right corner beside the monitor",
          np.allclose(blended[0], [0, 0, 1]), blended[0])
    check("and eases into the scene's mood colour at the far end",
          np.allclose(blended[-1], bed.mood(low), atol=1e-4), (blended[-1], bed.mood(low)))
    check("it never takes its colours from the top of the screen",
          all(not np.allclose(px, [0, 1, 0]) for px in blended))
    even_edge = low.copy()
    even_edge[:60] = [0, 0, 1]
    ramp_steps = np.abs(np.diff(bed.blend_colours(even_edge, blend_cfg, 30), axis=0)).max()
    check("the hand-over from the screen's edge to the mood has no jump", ramp_steps < 0.25, ramp_steps)
    flipped = bed.blend_colours(low, {**blend_cfg, "bed_reverse": True}, 30)
    check("a strip laid the other way round starts from its other end",
          np.allclose(flipped[-1], [0, 0, 1]) and np.allclose(flipped[0], bed.mood(low), atol=1e-4))
    check("the look is a valid setting", cfg.validate("bed_look", "blend") == "blend"
          and cfg.validate("bed_reverse", "1") is True)

    stage = OutputStage()
    stage.set_target(red_scene, 0.0)
    live = cfg.snapshot()
    out = stage.frame(0.0, live)
    check("following the screen, each frame carries the monitor strip and then the bed",
          out.shape == (210, 3) and out.dtype == np.uint8)
    check("the whole bed glows one mood colour", len({tuple(p) for p in out[180:]}) == 1 and out[180][0] > out[180][2])
    blue_edge_scene = red_scene.copy()
    blue_edge_scene[:3] = [0.05, 0.05, 0.9]     # the first LED is blue; the scene is red
    blend_stage = OutputStage()
    blend_stage.set_target(blue_edge_scene, 0.0)
    blend_out = blend_stage.frame(0.0, {**live, "bed_look": "blend"})
    check("the blend look fills the bed after the monitor strip, and the bed's two ends differ",
          blend_out.shape == (210, 3) and not np.array_equal(blend_out[180], blend_out[-1]), blend_out[[180, -1]])
    check("an effect or colour on the bed is the board's job: frames stay monitor-only",
          stage.frame(0.1, {**live, "bed_mode": "effect"}).shape == (180, 3))
    dim = OutputStage()
    dim.set_target(red_scene, 0.0)
    slow, fast = OutputStage(), OutputStage()
    for stage_under_test, response in ((slow, 0.1), (fast, 0.9)):
        stage_under_test.set_target(np.full((180, 3), 0.2, np.float32), 0.0)
        stage_under_test.frame(0.0, {**live, "bed_look": "side", "blend": response})
        stage_under_test.set_target(np.full((180, 3), 0.35, np.float32), 0.05)     # a move too small to count as a cut
    quick_bed = fast.frame(0.1, {**live, "bed_look": "side", "blend": 0.9})[180:].astype(int).sum()
    gentle_bed = slow.frame(0.1, {**live, "bed_look": "side", "blend": 0.1})[180:].astype(int).sum()
    check("the bed moves at the monitor's Response, not at a pace of its own", quick_bed > gentle_bed, (quick_bed, gentle_bed))
    mood_bed, side_bed = bed.BedStage(), bed.BedStage()
    movie = {**live, "blend": 0.3}
    for bed_stage, look in ((mood_bed, "mood"), (side_bed, "side")):
        bed_stage.frame(np.full((180, 3), 0.2, np.float32), 0.0, {**movie, "bed_look": look})
        bed_stage.frame(np.full((180, 3), 0.35, np.float32), 0.05, {**movie, "bed_look": look})
    halfway = 0.05 + settle_seconds(0.3) / 2
    mood_now = mood_bed.frame(np.full((180, 3), 0.35, np.float32), halfway, {**movie, "bed_look": "mood"}).astype(int).sum()
    side_now = side_bed.frame(np.full((180, 3), 0.35, np.float32), halfway, {**movie, "bed_look": "side"}).astype(int).sum()
    check("the mood look keeps pace with the monitor strip instead of trailing it",
          abs(mood_now - side_now) <= 0.02 * max(side_now, 1), (mood_now, side_now))
    check("bed brightness scales only the bed",
          np.array_equal(dim.frame(0.0, {**live, "bed_brightness": 0.0})[180:], np.zeros((30, 3), np.uint8)))
    bright = OutputStage()
    bright.set_target(np.ones((180, 3), np.float32), 0.0)
    limited = bright.frame(0.0, {**live, "bed_power_ma": 400})[180:]
    check("the bed keeps to its own adapter's current", estimate_ma(limited) <= 400 * 1.02, estimate_ma(limited))
    short = OutputStage()
    short.set_target(red_scene[:97], 0.0)
    padded = short.frame(0.0, {**live, "led_count": 97, "bed_start": 180})
    check("with 97 LEDs round the screen on a 180-LED output, the rest stay dark and the bed follows at 180",
          padded.shape == (210, 3) and not padded[97:180].any() and padded[180:].any())
    check("and the bed's segments start there too",
          bed.layout({**live, "led_count": 97, "bed_start": 180, "bed_mode": "colour"})[1]["start"] == 180)


def test_shared_supply():
    print("\n39b. Both strips on one adapter: their currents together stay inside it")
    from urllib.parse import parse_qs
    from ambilight import routes
    from ambilight.engine import OutputStage
    from ambilight.safety import estimate_ma

    path = os.path.join(tempfile.mkdtemp(prefix="ambilight-supply-"), "config.json")
    cfg = Config(path)
    # The defaults, plus a 200-LED bed set up with the form's suggested 2 A.
    # Neutral white draws the most current, so the limits are checked against the worst case.
    cfg.update({"led_count": 180, "bed_leds": 200, "bed_start": 180, "bed_mode": "screen",
                "bed_brightness": 1.0, "black_level": 0.0, "white_kelvin": 6500})
    budget = 5000 * 0.8
    check("a fresh install assumes the 5 A adapter its limits were sized for",
          cfg.snapshot().get("supply_ma") == 5000)

    stage = OutputStage()
    stage.set_target(np.ones((180, 3), np.float32), 0.0)
    out = stage.frame(0.0, cfg.snapshot())
    total = estimate_ma(out[:180]) + estimate_ma(out[180:])
    check("full white on both strips stays under 80% of the adapter's rating",
          total <= budget * 1.02, round(total))
    check("and both strips still light", out[:180].any() and out[180:].any())

    alone = OutputStage()
    alone.set_target(np.ones((180, 3), np.float32), 0.0)
    own = alone.frame(0.0, {**cfg.snapshot(), "supply_ma": 0})
    check("0 means each strip has an adapter of its own: each keeps its own limit",
          estimate_ma(own[180:]) > 1900 and estimate_ma(own[:180]) > 3700,
          (round(estimate_ma(own[:180])), round(estimate_ma(own[180:]))))

    board = FakeBedBoard(ps=-1)

    class Hub:
        wled = board

        def __init__(self):
            self.cfg = cfg

        def status(self):
            return {}

    hub = Hub()
    body, code = routes.handle(hub, "/api/bed/setup", parse_qs("leds=200&amps=2"))
    monitor, second = board.configs[-1]["hw"]["led"]["ins"]
    check("the board's own limits for the two outputs add up to no more than that share too",
          code == 200 and monitor["maxpwr"] + second["maxpwr"] <= budget, (code, monitor["maxpwr"], second["maxpwr"]))
    check("the bed's share keeps the proportion that was asked for",
          abs(second["maxpwr"] / monitor["maxpwr"] - 2000 / 3800) < 0.01, (monitor["maxpwr"], second["maxpwr"]))
    check("what was typed in is kept, so a bigger adapter later gives it back", cfg.get("bed_power_ma") == 2000)
    check("the adapter's rating is a checked setting", cfg.validate("supply_ma", "8000") == 8000)


class FakeProcess:
    """A finished child process: its output lines, then an exit code."""

    def __init__(self, args, lines, code, gate):
        self.args, self.code, self.gate = args, code, gate
        self.stdout = iter(lines)
        self.returncode = None

    def wait(self):
        self.gate.wait(5)
        self.returncode = self.code
        return self.code


def test_webcam_job():
    print("\n38. The web page runs the webcam calibration, never the built-in camera")
    import threading
    from ambilight import camrun

    cameras = [("Lenovo FHD Webcam", False), ("FaceTime HD Camera", True)]
    started, gate = [], threading.Event()

    def popen(args, **kwargs):
        started.append(args)
        return FakeProcess(args, ["Using Lenovo FHD Webcam\n", "  matched white: mismatch 0.019\n"], 0, gate)

    job = camrun.CameraJob(8080, popen=popen, cameras=lambda: cameras)
    check("nothing runs at first", job.status()["state"] == "idle")
    job.start("calibrate", "lenovo")
    check("the calibration runs as its own process, on the named webcam",
          "--camera-calibrate" in started[0] and "Lenovo FHD Webcam" in started[0] and "8080" in started[0], started[0])
    try:
        job.start("check", "lenovo")
        refused = ""
    except RuntimeError as exc:
        refused = str(exc)
    check("a second one is refused while the first runs", "already running" in refused, refused)
    gate.set()
    for _ in range(50):
        if job.status()["state"] != "working":
            break
        time.sleep(0.02)
    st = job.status()
    check("its progress lines are relayed, and it ends done",
          st["state"] == "done" and st["log"][-1] == "matched white: mismatch 0.019", st)
    try:
        job.start("calibrate", "facetime")
        refused = ""
    except RuntimeError as exc:
        refused = str(exc)
    check("the built-in camera is refused even when asked for", "built-in" in refused, refused)
    check("and nothing was started for it", len(started) == 1)
    gate.clear()
    job.start("check", None, seconds=30)
    check("a check scores for the asked number of seconds",
          started[-1][-2:] == ["--camera-check", "30"], started[-1])
    gate.set()


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


class FakeClock:
    """Time that moves only when told to, so timing checks are exact. It also
    stands in for a stop event: waiting on it moves time on instead of sleeping."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds

    def is_set(self):
        return False


def test_telemetry():
    print("\n40. Telemetry: stage timing, delivered frames, drops and picture age")
    from ambilight.telemetry import STAGES, WINDOW, Telemetry, percentile

    check("percentiles use the nearest rank",
          percentile([4, 1, 3, 2], 0.5) == 2 and percentile([4, 1, 3, 2], 0.95) == 4)
    check("no samples, no percentile", percentile([], 0.5) is None)

    clock = FakeClock()
    t = Telemetry(clock)
    empty = t.snapshot()
    check("a fresh collector lists every stage, all empty",
          set(empty["stages_ms"]) == set(STAGES)
          and all(v == {"p50": None, "p95": None} for v in empty["stages_ms"].values()), empty["stages_ms"])
    check("and nothing delivered or dropped", (empty["delivered_fps"], empty["dropped"]) == (0, 0))

    for ms in (10, 20, 30, 40):
        with t.stage("capture"):
            clock.wait(ms / 1000)
    capture = t.snapshot()["stages_ms"]["capture"]
    check("a stage reports its median and p95 in ms", capture == {"p50": 20.0, "p95": 40.0}, capture)

    try:
        with t.stage("process"):
            raise OSError("display gone")
    except OSError:
        pass
    check("a stage that fails is not counted", t.snapshot()["stages_ms"]["process"]["p50"] is None)

    for i in range(WINDOW + 60):
        t.record("send", i / 1000)
    check("only the most recent samples count",
          t.snapshot()["stages_ms"]["send"]["p50"] == 60 + WINDOW // 2 - 1, t.snapshot()["stages_ms"]["send"])

    for _ in range(120):
        clock.wait(1 / 60)
        t.sent(True, clock())
    fps = t.snapshot()["delivered_fps"]
    check("delivered fps counts the last second of sends", 59 <= fps <= 61, fps)
    clock.wait(2.0)
    check("and falls to zero when sends stop", t.snapshot()["delivered_fps"] == 0)

    t = Telemetry(clock)
    t.captured(clock())
    clock.wait(0.030)
    t.sent(True, clock())
    clock.wait(1 / 60)
    t.sent(True, clock())
    check("a picture's age is taken at its first send only",
          t.snapshot()["latency_ms"] == {"p50": 30.0, "p95": 30.0}, t.snapshot()["latency_ms"])

    t.captured(clock())
    clock.wait(0.010)
    t.sent(False, clock())
    clock.wait(0.010)
    t.sent(True, clock())
    snap = t.snapshot()
    check("a failed send is a drop and an error", (snap["dropped"], snap["send_errors"]) == (1, 1), snap)
    check("and the picture's age runs to the first send that got through",
          snap["latency_ms"] == {"p50": 20.0, "p95": 30.0}, snap["latency_ms"])
    t.missed(5)
    check("frames the pacer skipped are drops too", t.snapshot()["dropped"] == 6)
    check("delivered counts only good sends", t.snapshot()["delivered"] == 3)

    t.reset()
    snap = t.snapshot()
    check("reset starts from nothing",
          (snap["delivered"], snap["dropped"], snap["latency_ms"]["p50"]) == (0, 0, None), snap)


def test_engine_telemetry():
    print("\n41. The engine times every stage and counts what it drops")
    import ambilight.engine as eng
    from ambilight.telemetry import STAGES

    clock = FakeClock()
    pacer = eng._Pacer(clock)
    for _ in range(10):
        pacer.tick(60, clock)
    clock.wait(0.1)
    for _ in range(8):
        pacer.tick(60, clock)
    check("a short hiccup is caught up, not dropped", pacer.missed == 0, pacer.missed)
    clock.wait(0.41)
    before = clock()
    pacer.tick(60, clock)
    check("a stall that forces a resync counts the frames it skipped", pacer.missed == 23, pacer.missed)
    pacer.tick(60, clock)
    check("and the pacer still resyncs instead of sprinting", abs(clock() - before - 1 / 60) < 1e-9)

    class Steady:
        name = "steady"

        @staticmethod
        def available():
            return True

        @staticmethod
        def display_count():
            return 1

        def open(self, monitor_index, band_height):
            return 600, 150

        def grab(self):
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

    real = (eng.CAPTURES, eng.screen_locked)
    eng.CAPTURES, eng.screen_locked = {"steady": Steady}, lambda: False
    try:
        cfg = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-telemetry-"), "config.json"))
        cfg.update({"wled_ip": "127.0.0.1", "wled_port": FAKE_WLED_UDP, "led_count": 60,
                    "layout": "strip", "fps": 60})
        engine = eng.Engine(cfg)
        engine.start()
        time.sleep(1.2)
        t = engine.status()["telemetry"]
        engine.stop()
        engine.start()
        restarted = engine.status()["telemetry"]["delivered"]
        engine.stop()
    finally:
        eng.CAPTURES, eng.screen_locked = real
    check("every stage has timings", all(t["stages_ms"][name]["p50"] is not None for name in STAGES),
          t["stages_ms"])
    check("p95 is never below the median",
          all(v["p95"] >= v["p50"] for v in [*t["stages_ms"].values(), t["latency_ms"]]))
    check("frames reach the output at the set rate", t["delivered_fps"] > 40, t["delivered_fps"])
    check("a picture's age when it is sent is measured", (t["latency_ms"]["p50"] or 0) > 0, t["latency_ms"])
    check("each run starts counting afresh", restarted < t["delivered"], (restarted, t["delivered"]))


def test_protected_glow():
    print("\n41b. Protected video: the strip glows warm instead of going dark")
    import ambilight.engine as eng

    spot = eng._ProtectedVideo(hold_s=2.5)
    check("a moment of exact black is a dark scene, not protected video",
          not spot.update(True, 0.0) and not spot.update(True, 2.0))
    check("exact black that lasts is taken for protected video", spot.update(True, 2.6))
    check("any light ends it at once", not spot.update(False, 2.7) and not spot.update(True, 3.0))

    class Screen:
        """A capture showing whatever `frame` holds."""
        name = "screen"
        frame = np.zeros((150, 600, 3), np.uint8)

        @staticmethod
        def available():
            return True

        @staticmethod
        def display_count():
            return 1

        def open(self, monitor_index, band_height):
            return 600, 150

        def grab(self):
            return Screen.frame.copy()

        def grab_full(self):
            return self.grab()

        def grab_search(self, rows):
            return self.grab()

        def set_inset(self, rows):
            pass

        def close(self):
            pass

    blue = np.zeros((150, 600, 3), np.uint8)
    blue[:] = (20, 40, 200)
    dim = np.full((150, 600, 3), 3, np.uint8)     # a dark scene: almost, but not exactly, black
    real = (eng.CAPTURES, eng.screen_locked, eng.PROTECTED_HOLD_S)
    eng.CAPTURES, eng.screen_locked, eng.PROTECTED_HOLD_S = {"screen": Screen}, (lambda: False), 0.5
    try:
        cfg = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-protected-"), "config.json"))
        cfg.update({"wled_ip": "127.0.0.1", "wled_port": FAKE_WLED_UDP, "led_count": 60, "layout": "strip",
                    "fps": 60, "letterbox": False})
        engine = eng.Engine(cfg)
        Screen.frame = blue
        engine.start()
        time.sleep(0.4)
        Screen.frame = np.zeros((150, 600, 3), np.uint8)
        time.sleep(0.25)
        early = engine.status()["protected_video"]
        time.sleep(0.6)
        glowing, target = engine.status()["protected_video"], engine.stage.smoother.target.copy()
        Screen.frame = blue
        time.sleep(0.3)
        back, after = engine.status()["protected_video"], engine.stage.smoother.target.copy()
        Screen.frame = dim
        time.sleep(1.0)
        dark = engine.status()["protected_video"]
        engine.stop()
    finally:
        eng.CAPTURES, eng.screen_locked, eng.PROTECTED_HOLD_S = real
    check("a capture gone black does not glow at once", early is False)
    warm = target.mean(axis=0)
    check("black that lasts makes every LED a soft warm white",
          glowing is True and warm[0] > warm[1] > warm[2] > 0 and warm.max() < 0.8
          and np.allclose(target, target[0]), warm.round(3))
    check("the picture coming back takes over at once", back is False and after.mean(axis=0)[2] > after.mean(axis=0)[0],
          after.mean(axis=0).round(3))
    check("a dark scene that is not exactly black never glows", dark is False)
    check("the black-frame warning names protected video, not only permission",
          "protected" in eng._BlackFrameWatchdog.MESSAGE)


def test_telemetry_api(base):
    print("\n42. Timing in the status API")
    from ambilight.telemetry import STAGES

    def get(path):
        with urllib.request.urlopen(base + path, timeout=3) as r:
            return json.loads(r.read())

    get("/api/sync/on")
    time.sleep(2.0)
    s = get("/api/status")
    get("/api/sync/off")
    t = s.get("telemetry", {})
    check("status carries the stage timings", set(t.get("stages_ms", {})) == set(STAGES), t)
    check("the screen capture is timed", (t["stages_ms"]["capture"]["p50"] or 0) > 0, t["stages_ms"]["capture"])
    check("frames are delivered to the board", t["delivered_fps"] > 20, t["delivered_fps"])
    check("diagnostics carries the same block", set(get("/api/diagnostics")["telemetry"]) == set(t))

    import re
    html = page_text()
    script = re.search(r"function renderTiming\(s\) \{.*?\n\}", html, re.S).group(0)
    stages = re.search(r"const TIMING_STAGES = (\[.*?\]);", html, re.S).group(1)
    check("the page has a Timing panel", 'id="timing"' in html and "renderTiming(s);" in html)
    check("every timing figure the page reads is in the status",
          set(re.findall(r"\bt\.(\w+)", script)) <= set(t) and "measured_fps" in s,
          set(re.findall(r"\bt\.(\w+)", script)) - set(t))
    check("the page shows every stage the hub times", set(re.findall(r'\["(\w+)", "', stages)) == set(STAGES))


def test_one_interface():
    print("\n44. PudiSync: one Home screen that runs every light")
    import gzip, re, shutil
    from ambilight.config import DEFAULTS
    from ambilight.routes import POST_ROUTES, ROUTES
    from ambilight.server import PAGES
    html = page_text()
    markup, script = open(PAGE).read(), page_script()
    tabs = re.findall(r'data-action="tab" data-value="(\w+)"', markup)
    check("four tabs: Home, Tune, Room, Settings", tabs == ["home", "tune", "room", "settings"], tabs)
    home = re.search(r'<section class="screen" id="tab-home".*?\n  </section>', markup, re.S).group(0)
    scenes = re.findall(r'data-action="scene" data-value="(\w+)"', home)
    bed_modes = re.findall(r'data-action="bed-mode" data-value="(\w+)"', home)
    check("Home offers one scene for every light: screen, colour, effect or a room scene",
          scenes == ["screen", "colour", "effect", "room"], scenes)
    check("...one power button and one brightness for all of them",
          home.count('data-action="power"') == 1 and home.count('data-fader="master"') == 1)
    check("the bed can match the scene, or have its own colour, effect or off",
          bed_modes == ["screen", "colour", "effect", "off"], bed_modes)
    check("the mouse is on Home too", 'data-switch="mouse_sync"' in home)
    check("old links and bookmarks open the screen that now holds them",
          all(re.search(rf"\b{old}: \"{new}\"", script) for old, new in
              (("lights", "overview"), ("effects", "overview"), ("picture", "monitor"),
               ("override", "monitor"), ("advanced", "setup"))))
    sections = re.findall(r'data-action="section" data-value="(\w+)"', markup)
    check("wide screens offer eight sections, Room Studio its own", sections ==
          ["overview", "monitor", "bed", "mouse", "looks", "room", "calibration", "setup"], sections)
    screens = markup[markup.index('<div id="screens"'):]
    cards = re.findall(r'<(?:div|details) class="card[^"]*"([^>]*)>', screens)
    check("every card belongs to a section", all("data-section=" in attrs for attrs in cards),
          [a for a in cards if "data-section=" not in a])
    used = {s for value in re.findall(r'data-section="([\w ]+)"', screens) for s in value.split()}
    check("every section has something in it, and no card names a section that is not offered",
          used == set(sections), used ^ set(sections))
    ids = set(re.findall(r'id="([\w-]+)"', markup))
    wanted = set(re.findall(r'\$\("([\w-]+)"\)', script))
    check("every element the script reaches for is on the page", wanted <= ids, sorted(wanted - ids))
    check("no id is used twice", len(re.findall(r' id="', markup)) == len(ids))
    handled = set(re.findall(r'^\s*"?([\w-]+)"?\s*:', js_block(script, "const ACTIONS"), re.M))
    actions = set(re.findall(r'data-action="([\w-]+)"', markup)) | set(re.findall(r'dataset\.action = "([\w-]+)"', script))
    check("every button on the page has a handler", actions <= handled, sorted(actions - handled))
    called = set(re.findall(r'"(/api/[\w/-]+)"', script))
    served = set(ROUTES) | set(POST_ROUTES)
    check("every hub address the page calls exists", called <= served, sorted(called - served))
    switches = set(re.findall(r'data-switch="(\w+)"', markup))
    check("every switch is a real setting", switches <= set(DEFAULTS), sorted(switches - set(DEFAULTS)))
    check("it is called PudiSync", "<title>PudiSync</title>" in html and 'content="PudiSync"' in html
          and "Ambilight Hub" not in html)
    sent = sum(len(gzip.compress(open(path, "rb").read(), mtime=0)) for path in PAGE_FILES)
    # 40 kB until 2026-11-20, when the owner chose to add the appearance switch at a slightly higher budget;
    # 43 kB from the experience engine (2026-11-20): the sound controls on Home and the Room tab. Room
    # Studio's own controls load only when it opens (room.js), as the expert settings do. 44 kB from the
    # owner's sci-fi look (2026-11-20): glass panels, glowing controls and a starfield drawn in CSS; 46 kB
    # for the starship-console look the owner asked for next (cut corners, brackets, scanlines, reticle).
    # 47 kB for the soft modules on top of it, and the grid of modules on wide screens.
    # 48 kB (2026-11-21) for looks in sections, each with a description the owner writes, and Home's blocks.
    check("it stays light: no outside resources, under 48 kB in all as a phone downloads it",
          sent < 48_000 and not re.search(r'(src|href)="https?://|url\(https?://|@import', html), sent)
    from ambilight.server import packed
    first = packed("index.html", b"page")
    packed("app.js", b"script")
    check("each page file is gzipped once, not again whenever another file is asked for",
          packed("index.html", b"page") is first and packed("index.html", b"new page") is not first)
    check("the page loads only its own two files, both served by the hub",
          re.findall(r'(?:src|href)="(/app\.\w+)"', markup) == ["/app.css", "/app.js"]
          and {"/app.css", "/app.js"} <= set(PAGES))
    expert = open("ambilight/web/expert.js").read()
    check("expert settings load only when opened, and stay small",
          "/expert.js" in PAGES and "expert.js" not in markup and '"/expert.js"' in script
          and len(gzip.compress(expert.encode(), mtime=0)) < 8_000)
    room = open("ambilight/web/room.js").read()
    check("Room Studio's controls load only when it opens, and stay small",
          "/room.js" in PAGES and "room.js" not in markup and '"/room.js"' in script
          and len(gzip.compress(room.encode(), mtime=0)) < 4_000)
    reachable = "\n".join([html, expert, room, open("ambilight/web/calibrate.html").read()])
    check("every setting the hub has can be changed from a page",
          not [k for k in DEFAULTS if k not in reachable], [k for k in DEFAULTS if k not in reachable])
    check("it never builds markup from strings",
          all("innerHTML" not in s and "eval(" not in s for s in (script, expert, room)))
    head = markup[:markup.index("</head>")]
    check("on Auto it follows the device's light or dark appearance, decided before the page draws",
          "prefers-color-scheme: light" in head and "pudisync-theme" in head and "dataset.theme" in head
          and "DEVICE_LIGHT" in script)
    check("each device can pick Auto, Light or Dark for itself",
          re.findall(r'data-action="theme" data-value="(\w*)"', markup) == ["", "light", "dark"])
    css = open("ambilight/web/app.css").read()
    dark = dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", css[css.index(":root{"):css.index("}")]))
    light_block = css[css.index(":root[data-theme=light]{"):]
    light = {**dark, **dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", light_block[:light_block.index("}")]))}

    def contrast(a, b):
        def lum(hex_colour):
            c = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    pairs = [(fg, bg, 4.5) for fg in ("ink", "dim", "accent", "ok", "bad") for bg in ("bg", "surface", "raised")]
    pairs += [("accent-ink", "accent", 4.5), ("line-hi", "surface", 3.0), ("line-hi", "bg", 3.0)]
    pairs += [(fg, bg, 4.5) for fg in ("rail-ink", "rail-dim", "rail-accent") for bg in ("rail-bg", "rail-on")]
    weak = [(theme, fg, bg, round(contrast(tokens[fg], tokens[bg]), 2)) for theme, tokens in (("dark", dark), ("light", light))
            for fg, bg, need in pairs if contrast(tokens[fg], tokens[bg]) < need]
    check("in both themes text reads at 4.5:1 and control edges show at 3:1 (WCAG AA)", not weak, weak)
    swift = dict((re.sub(r"(?<!^)([A-Z])", r"-\1", name).lower(), (d, l)) for name, d, l in
                 re.findall(r'static let (\w+) = adaptive\("(#\w+)", "(#\w+)"\)', open("macapp/Sources/Theme.swift").read()))
    drift = {k: (v, (dark.get(k), light.get(k))) for k, v in swift.items() if v != (dark.get(k), light.get(k))}
    check("the Mac app wears exactly the phone page's colours, light and dark", len(swift) == 14 and not drift, drift)
    wheels = re.findall(r'<div class="wheel" id="([\w-]+)"', markup)
    check("every colour is picked on a round wheel, never the browser's square box",
          wheels == ["colour", "bed-colour", "part-colour"] and 'type="color"' not in markup, wheels)

    if not shutil.which("node"):
        print("    (skipped: node not installed, so the page's logic was not run)")
        return
    consts = "\n".join(re.search(rf"^const {name} .*?;$", script, re.M | re.S).group(0)
                       for name in ("settle", "pct", "two", "offOr", "modeOf", "SWATCHES", "HUE_NAMES", "WARMEST_K"))
    faders = run_node(consts + "\n" + js_block(script, "const FADERS") + r"""
console.log(JSON.stringify(Object.entries(FADERS).map(([k, f]) => [k, !!f.local, !!(f.apply || f.read)])));
""")
    if isinstance(faders, list):
        plain = {k for k, local, custom in faders if not local and not custom}
        check("every slider writes a real setting", plain <= set(DEFAULTS), sorted(plain - set(DEFAULTS)))
        hosts = set(re.findall(r'data-fader="(\w+)"', markup))
        check("every slider on the page is defined, and every one defined is used",
              hosts == {k for k, *_ in faders}, sorted(hosts ^ {k for k, *_ in faders}))
    else:
        check("the sliders' table runs", False, faders)

    logic = run_node(consts + "\n" + "\n".join(js_block(script, start) for start in (
        "function sceneOf", "function heroView", "const BED_LOOKS", "function bedView", "function cutOut",
        "function withPart", "function newerGate", "function hexToHsv", "function hsvToHex",
        "function kelvinToHex", "function kelvinOf", "function colourName")) + r"""
const effectName = fx => `fx${fx}`;
const name = s => [sceneOf(s), heroView(s, {offline: false, scene: sceneOf(s), effectName}).state];
const out = {
  scenes: [name({mode: "sync", running: true}), name({mode: "effect", wled_fx: 0, wled_col: "ff2010"}),
           name({mode: "effect", wled_fx: 63}), name({mode: "off"}), name({mode: "effect", wled_fx: null})],
  offline: heroView({mode: "sync"}, {offline: true, scene: "screen", effectName}).state,
  noBoard: heroView({mode: "off", wled_reachable: false}, {offline: false, scene: "off", effectName}),
  syncNoBoard: heroView({mode: "sync", wled_reachable: false}, {offline: false, scene: "screen", effectName}).lamp,
  bed: [["screen", "screen"], ["screen", "colour"], ["screen", "off"], ["colour", "screen"], ["off", "screen"]]
    .map(([mode, scene]) => bedView({bed_leds: 120, bed_mode: mode, bed_look: "mood", bed_effect: 9}, scene))
    .map(v => [v.state, v.brightness]),
  noBed: bedView({bed_leds: 0, bed_mode: "screen"}, "screen").state,
  cut: cutOut([{start: 0, end: 9, mode: "warm"}, {start: 20, end: 29, mode: "off"}], 5, 24)
    .map(z => [z.start, z.end]),
  split: cutOut([{start: 0, end: 30, mode: "warm"}], 10, 19).map(z => [z.start, z.end]),
  put: withPart([{start: 0, end: 30, mode: "warm"}], 10, 19, {mode: "solid", color: "#ff0000"})
    .map(z => [z.start, z.end, z.mode]),
  back: withPart([{start: 0, end: 30, mode: "warm"}], 10, 19, {mode: "sync"}).map(z => [z.start, z.end]),
  wheel: ["#80ff00", "#00ffff", "#2040ff", "#ffb46b", "#ffffff", "#000000"]
    .map(h => { const c = hexToHsv(h); return hsvToHex(c.h, c.s, c.v); }),
  rim: [hsvToHex(0, 1), hsvToHex(90, 1), hsvToHex(180, 1), hsvToHex(270, 0.5), hsvToHex(45, 0)],
  names: ["#ff2010", "#80ff00", "#00ffff", "#ff80ff", "#f2f2f2", "#ff6a00", "#c0ffc0", "#ff9227", "#fffefa"]
    .map(colourName),
  whites: [kelvinToHex(2200), kelvinToHex(6500), kelvinOf(kelvinToHex(4000)), kelvinOf("#00ffff")],
};
const gate = newerGate();
out.gate = [gate(2), gate(1), gate(3), gate(3)];
console.log(JSON.stringify(out));
""")
    if not isinstance(logic, dict) or "error" in logic:
        check("the page's logic runs under node", False, logic)
        return
    check("the scene is read from the board: sync, colour, effect, off",
          [s for s, _ in logic["scenes"]] == ["screen", "colour", "effect", "off", "colour"], logic["scenes"])
    check("...and the hero names what is showing",
          [t for _, t in logic["scenes"][:4]] == ["Following your screen", "Red", "fx63", "Off"], logic["scenes"])
    check("an unreachable hub is never shown as lights on", logic["offline"] == "Hub offline")
    check("an unreachable board says so instead of 'Off'",
          logic["noBoard"]["state"] == "Can't reach the board" and logic["noBoard"]["lamp"] == "fault", logic["noBoard"])
    check("...and while syncing it shows as a fault", logic["syncNoBoard"] == "fault")
    check("the bed says when it matches the scene, and hides a brightness that would do nothing",
          logic["bed"] == [["Matches · follows the screen", True], ["Matches · same colour", False],
                           ["Matches · off with everything", False], ["Its own colour", True], ["Off", False]], logic["bed"])
    check("a missing bed strip says it is not set up", logic["noBed"] == "Not set up")
    check("a selection takes its LEDs out of the parts it crosses", logic["cut"] == [[0, 4], [25, 29]], logic["cut"])
    check("...splitting a part it falls inside", logic["split"] == [[0, 9], [20, 30]], logic["split"])
    check("giving a stretch a colour leaves the lamp on both sides",
          logic["put"] == [[0, 9, "warm"], [10, 19, "solid"], [20, 30, "warm"]], logic["put"])
    check("handing a stretch back to the scene leaves no part there", logic["back"] == [[0, 9], [20, 30]], logic["back"])
    check("a reply that arrives after a newer one is not drawn", logic["gate"] == [True, False, True, False], logic["gate"])
    check("the wheel reads any colour back exactly as it was given",
          logic["wheel"] == ["#80ff00", "#00ffff", "#2040ff", "#ffb46b", "#ffffff", "#000000"], logic["wheel"])
    check("...red at the top, then round clockwise, white at the centre",
          logic["rim"] == ["#ff0000", "#80ff00", "#00ffff", "#bf80ff", "#ffffff"], logic["rim"])
    check("a picked shade gets a plain name for the hero",
          logic["names"] == ["Red", "Lime", "Cyan", "Magenta", "White", "Orange", "Pale green", "Warm white", "Daylight"],
          logic["names"])
    check("the White wheel runs from 2200 K candlelight to 6500 K daylight, and knows a white when it sees one",
          logic["whites"] == ["#ff9227", "#fffefa", 4000, None], logic["whites"])


def test_page_in_browser(base):
    print("\n45. The phone page in a real browser, against the sandbox hub")
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        print("    (skipped: playwright not installed, so the page was not driven)")
        return

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return json.loads(r.read())

    def until(cond, seconds=6.0):
        """Wait for the hub to agree; returns the last status seen."""
        deadline = time.time() + seconds
        while True:
            s = get("/api/status")
            if cond(s) or time.time() > deadline:
                return s
            time.sleep(0.2)

    get("/api/sync/off")
    get("/api/set?bed_leds=60&bed_mode=screen&zones=[]")
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch(channel="chrome", headless=True)
        except PlaywrightError:
            try:
                browser = play.chromium.launch(headless=True)
            except PlaywrightError:
                print("    (skipped: no Chrome or Chromium for playwright, so the page was not driven)")
                return
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        problems = []
        page.on("pageerror", lambda exc: problems.append(str(exc)))
        page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" else None)
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base + "/")
        page.wait_for_selector("#screens:not([inert])", timeout=8000)
        check("the page opens on Home and comes alive once the hub answers",
              page.is_visible("#tab-home") and not page.is_visible("#tab-tune"))

        def pressed(selector):
            return page.get_attribute(selector, "aria-pressed") == "true"

        page.click('[data-action=scene][data-value=colour]')
        s = until(lambda s: s["mode"] == "effect" and s.get("wled_fx") == 0)
        check("Colour lights every light at once, no second tap needed",
              s["mode"] == "effect" and s.get("wled_fx") == 0 and pressed('[data-action=scene][data-value=colour]'), s["mode"])
        page.click('#swatches .swatch[data-hex="#2040ff"]')
        s = until(lambda s: s.get("wled_col") == "2040ff")
        page.wait_for_function("document.getElementById('power-state').textContent === 'Blue'", timeout=4000)
        check("a swatch sets the colour and the hero names it", s.get("wled_col") == "2040ff")

        def wheel(host):
            """Where the wheel is, once it is scrolled into the middle of the screen as a person would."""
            disc = page.locator(f"#{host} .wheel-disc")
            disc.evaluate("el => el.scrollIntoView({block: 'center'})")
            box = disc.bounding_box()
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, box["width"] / 2

        mid_x, mid_y, rim = wheel("colour")
        page.mouse.click(mid_x + rim - 2, mid_y)
        s = until(lambda s: s.get("wled_col") == "80ff00")
        check("a tap on the colour wheel's rim lights that full shade", s.get("wled_col") == "80ff00", s.get("wled_col"))
        page.mouse.move(mid_x, mid_y)
        page.mouse.down()
        page.mouse.move(mid_x, mid_y + rim - 2, steps=8)
        page.mouse.up()
        s = until(lambda s: s.get("wled_col") == "00ffff")
        page.wait_for_function("document.getElementById('power-state').textContent === 'Cyan'", timeout=4000)
        check("dragging round the wheel ends on the shade where it is let go, and the hero names it",
              s.get("wled_col") == "00ffff", s.get("wled_col"))
        page.focus("#colour .wheel-disc")
        page.keyboard.press("ArrowLeft")
        s = until(lambda s: s.get("wled_col") == "00ffea")
        check("...and the arrow keys turn it a little", s.get("wled_col") == "00ffea", s.get("wled_col"))
        page.click("#colour [data-wheel-mode=white]")
        mid_x, mid_y, rim = wheel("colour")
        page.mouse.click(mid_x, mid_y + rim - 2)
        s = until(lambda s: s.get("wled_col") == "ff9227")
        page.wait_for_function("document.getElementById('power-state').textContent === 'Warm white'", timeout=4000)
        check("White gives candle-warm white at the bottom of its wheel", s.get("wled_col") == "ff9227", s.get("wled_col"))
        page.mouse.click(mid_x, mid_y - rim + 2)
        s = until(lambda s: s.get("wled_col") == "fffefa")
        check("...and daylight at the top", s.get("wled_col") == "fffefa", s.get("wled_col"))
        recent = page.eval_on_selector_all("#colour .recent .swatch", "els => els.map(e => e.dataset.hex)")
        check("the shades just picked come back under Recent, newest first",
              recent[:3] == ["#fffefa", "#ff9227", "#00ffea"], recent)
        page.click('#colour .recent .swatch[data-hex="#00ffea"]')
        s = until(lambda s: s.get("wled_col") == "00ffea")
        check("...one tap on a recent shade lights it, back on the colour wheel",
              s.get("wled_col") == "00ffea" and pressed("#colour [data-wheel-mode=colour]"), s.get("wled_col"))

        page.click('[data-action=scene][data-value=effect]')
        s = until(lambda s: s.get("wled_fx") not in (None, 0))
        page.wait_for_selector("#fx-tiles .tile[aria-pressed=true]", timeout=4000)
        check("Effect starts one straight away and marks its tile", s.get("wled_fx") not in (None, 0), s.get("wled_fx"))

        page.click("#power")
        s = until(lambda s: s["mode"] == "off")
        page.wait_for_function("document.getElementById('power').getAttribute('aria-checked') === 'false'", timeout=4000)
        check("the power button turns everything off", s["mode"] == "off" and s["wled_on"] is False and not s["running"])
        page.click("#power")
        s = until(lambda s: s["running"])
        page.wait_for_selector('[data-action=scene][data-value=screen][aria-pressed=true]', timeout=4000)
        check("...and on again into screen sync", s["running"] and page.text_content("#power-state") == "Following your screen")

        page.eval_on_selector("#f-master", "el => { el.value = 77; el.dispatchEvent(new Event('input', {bubbles: true})); }")
        s = until(lambda s: s.get("wled_bri") == 77)
        check("the master brightness reaches the board, even while syncing", s.get("wled_bri") == 77, s.get("wled_bri"))
        time.sleep(3.0)
        check("...and the slider stays where it was let go", page.input_value("#f-master") == "77")
        check("...and sync carries on", get("/api/status")["running"])

        page.click('[data-action=profile][data-value=immersive]')
        s = until(lambda s: s["profile"] == "immersive")
        check("a style applies its look", s["profile"] == "immersive" and pressed('[data-action=profile][data-value=immersive]'))

        page.click('[data-action=bed-mode][data-value=colour]')
        s = until(lambda s: s["bed_mode"] == "colour")
        page.wait_for_selector("#bed-colour-box:not([hidden])", timeout=4000)
        check("the bed can take its own colour while the rest syncs", s["bed_mode"] == "colour" and s["running"])
        mid_x, mid_y, rim = wheel("bed-colour")
        page.mouse.click(mid_x, mid_y - rim + 2)
        s = until(lambda s: s["bed_colour"] == "ff0000")
        check("...picked on its own wheel", s["bed_colour"] == "ff0000" and s["running"], s["bed_colour"])
        page.click('[data-action=bed-mode][data-value=screen]')
        s = until(lambda s: s["bed_mode"] == "screen")
        page.wait_for_selector("#bed-screen:not([hidden])", timeout=4000)
        check("...and match the scene again", s["bed_mode"] == "screen")

        page.click('[data-action=tab][data-value=tune]')
        page.click("#parts > summary")
        page.click('[data-action=part-mode][data-value=warm]')
        page.click('[data-action=part-all]')
        page.click("#part-go")
        s = until(lambda s: len(s["zones"]) == 1)
        check("a part of the strip can be set to a lamp from the Tune tab",
              [z["mode"] for z in s["zones"]] == ["warm"], s["zones"])
        page.click("#parts-clear")
        s = until(lambda s: not s["zones"])
        check("...and cleared", s["zones"] == [])

        page.click('[data-action=tab][data-value=settings]')
        for group in ("set-controller", "set-strip", "set-bed", "set-calibrate", "set-capture", "set-access", "set-diag"):
            page.click(f"#{group} > summary")
        page.wait_for_function("document.querySelectorAll('#timing dt').length > 0", timeout=4000)
        check("every settings group opens, and diagnostics fill in", page.is_visible("#timing"))

        spilled = []
        for width in (320, 390):
            page.set_viewport_size({"width": width, "height": 800})
            for tab in ("home", "tune", "settings"):
                page.evaluate(f"location.hash = '{tab}'")
                page.wait_for_selector(f"#tab-{tab}:not([hidden])")
                if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                    spilled.append(f"{tab} at {width}px")
        check("nothing runs off the side of a small phone", not spilled, spilled)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        bar = page.evaluate("Math.round(document.querySelector('.tabs').getBoundingClientRect().bottom) - innerHeight")
        check("on a phone the tab bar stays at the bottom of the screen, scrolled or not", bar == 0, bar)
        for width in (768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            page.evaluate("location.hash = 'overview'")
            page.wait_for_timeout(200)
            edges = page.evaluate("""[document.querySelector('.top').getBoundingClientRect().right,
                document.querySelector('#power').getBoundingClientRect().left,
                document.documentElement.scrollWidth - innerWidth]""")
            check(f"at {width}px the sections sit in a rail beside the page, nothing under it or off the side",
                  edges[0] > 0 and edges[1] > edges[0] and edges[2] <= 1, edges)
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("location.hash = 'home'")
        page.wait_for_selector("#tab-home:not([hidden])")

        page.route("**/api/status", lambda route: route.abort())
        page.wait_for_selector("#offline-banner:not([hidden])", timeout=12000)
        check("a hub that drops out is said so, and the controls step back",
              page.get_attribute("#screens", "inert") is not None and page.text_content("#link-word") == "Offline")
        page.unroute("**/api/status")
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_selector("#offline-banner[hidden]", state="attached", timeout=8000)
        check("...and come back by themselves", page.get_attribute("#screens", "inert") is None)

        # Backup: a file opened on the page is reported before Apply, and Undo puts it back.
        before = get("/api/config")
        backup_file = {"format": "pudisync-backup", "schema_version": 1, "kind": "full",
                       "settings": {"saturation": 2.5, "power_limit_ma": 20000, "led_count": 7}}
        page.goto(base + "/#settings")
        page.click("#set-backup summary")
        page.set_input_files("#backup-file", files=[{"name": "backup.json", "mimeType": "application/json",
                                                     "buffer": json.dumps(backup_file).encode()}])
        page.wait_for_selector("#backup-plan:not([hidden])", timeout=5000)
        report = page.text_content("#backup-rows")
        check("opening a backup on the page reports it before anything changes",
              "kept from this hub" in report and "2.5" in report
              and get("/api/config")["saturation"] == before["saturation"], report)
        groups = page.eval_on_selector_all("#backup-groups input", "els => els.map(e => [e.value, e.checked])")
        check("...with the looks ticked and the strip's layout left for you to choose",
              groups == [["looks", True], ["layout", False]], groups)
        page.click("#backup-apply")
        s = until(lambda s: s["saturation"] == 2.5)
        check("Apply stores it; the power limit and the strip length stay this hub's",
              s["saturation"] == 2.5 and s["power_limit_ma"] == before["power_limit_ma"]
              and s["led_count"] == before["led_count"], (s["saturation"], s["power_limit_ma"], s["led_count"]))
        page.click(".toast button:has-text('Undo')")
        s = until(lambda s: s["saturation"] == before["saturation"])
        check("...and Undo puts it back", s["saturation"] == before["saturation"], s["saturation"])
        try:
            page.wait_for_function("document.querySelectorAll('#backup-points li button').length >= 2", timeout=4000)
            listed = True
        except PlaywrightError:
            listed = False
        check("the restore points made by Apply and Undo are listed", listed)

        # Looks: pick one, compare it, copy it, rename the copy, try it and undo.
        get("/api/profile/vivid")
        page.goto(base + "/#tune")
        # Only the hash changes, so the page learns of the change made behind its back at its next poll.
        page.wait_for_function("state && state.profile === 'vivid'", timeout=5000)
        page.click("#looks summary")
        page.click("#look-list button:has-text('Immersive')")
        page.click("[data-action=look-compare]")
        diff = page.text_content("#look-diff")
        check("Compare lists what a look would change from the lights now",
              "Immersion" in diff and "0.15 → 0.7" in diff, diff)
        check("a built-in look cannot be renamed or deleted",
              page.is_disabled("#look-rename") and page.is_disabled("#look-delete"))
        page.click("[data-action=look-form][data-value=duplicate]")
        page.fill("#look-name", "Fireside")
        page.click("#form-look button[type=submit]")
        s = until(lambda s: "Fireside" in s["user_profiles"])
        check("Duplicate saves a built-in as a look of your own",
              s["user_profiles"].get("Fireside", {}).get("immersion") == 0.7, list(s["user_profiles"]))
        page.click("[data-action=look-form][data-value=rename]")
        page.fill("#look-name", "Campfire")
        page.click("#form-look button[type=submit]")
        s = until(lambda s: "Campfire" in s["user_profiles"])
        check("Rename gives your look a new name", "Campfire" in s["user_profiles"] and "Fireside" not in s["user_profiles"])
        page.click("[data-action=look-describe]")
        page.fill("#look-note", "Warm evenings by the fire")
        page.click("#form-note button[type=submit]")
        s = until(lambda s: s["user_profiles"].get("Campfire", {}).get("note") == "Warm evenings by the fire")
        check("Describe writes what a look is for", s["user_profiles"]["Campfire"]["note"] == "Warm evenings by the fire")
        page.wait_for_function("document.querySelector('#look-list').textContent.includes('Warm evenings by the fire')", timeout=5000)
        rows = page.eval_on_selector_all("#look-list .look", "els => els.map(e => e.textContent)")
        check("the looks list has built-in and your own in sections, each with its description",
              any("Warm evenings by the fire" in r for r in rows) and any("Deep shadows" in r for r in rows)
              and page.eval_on_selector_all("#look-list > .label", "els => els.map(e => e.textContent)") == ["Built-in", "Yours"], rows)
        check("a built-in look cannot be described",
              (page.click("#look-list button:has-text('Vivid')"), page.is_disabled("#look-describe"))[1])
        page.click("#look-list button:has-text('Campfire')")
        page.click("[data-action=look-use]")
        s = until(lambda s: s["profile"] == "Campfire")
        check("Use switches to the picked look", s["profile"] == "Campfire" and s["immersion"] == 0.7)
        page.click(".toast button:has-text('Undo')")
        s = until(lambda s: s["profile"] == "vivid")
        check("...and Undo goes back to the look before it, setting by setting",
              s["profile"] == "vivid" and s["immersion"] == 0.15, (s["profile"], s["immersion"]))
        get("/api/profiles/delete?name=Campfire")

        # Expert settings: fetched on first open, every value checked by the hub, power behind a confirm.
        before = get("/api/config")
        page.goto(base + "/#settings")
        page.click("#set-expert summary")
        page.wait_for_selector("#x-band_height", timeout=5000)
        check("opening Expert settings fetches its controls", page.is_visible("#f-enrich")
              and page.input_value("#x-sustained_ma") == str(before["sustained_ma"]))
        page.fill("#x-band_height", "180")
        page.click("form:has(#x-band_height) button[type=submit]")
        s = until(lambda s: s["band_height"] == 180)
        check("a mapping value saves to the hub", s["band_height"] == 180, s["band_height"])
        page.fill("#x-edge_skip", "900")
        page.click("form:has(#x-edge_skip) button[type=submit]")
        page.wait_for_timeout(500)
        check("a value past its range is refused by the page before it is sent",
              page.evaluate("!document.getElementById('x-edge_skip').validity.valid")
              and get("/api/config")["edge_skip"] == before["edge_skip"])
        page.fill("#x-edge_skip", str(before["edge_skip"]))
        dialogs = []
        page.on("dialog", lambda d: dialogs.append(d.message))
        page.fill("#x-sustained_ma", "2800")
        page.click("form:has(#x-sustained_ma) button[type=submit]")
        s = until(lambda s: s["sustained_ma"] == 2800)
        check("power limits save only after a warning about the adapter",
              s["sustained_ma"] == 2800 and any("adapter" in m for m in dialogs), dialogs)
        page.click("#expert [role=switch]")
        page.wait_for_selector(".toast.error", timeout=4000)
        check("protected mode is refused without a PIN, and the hub stays open", get("/api/config")["unprotected"] is True)
        get(f"/api/set?band_height={before['band_height']}&edge_skip={before['edge_skip']}"
            f"&sustained_ma={before['sustained_ma']}")

        wide = browser.new_page(viewport={"width": 1280, "height": 900})
        wide.goto(base + "/")
        wide.wait_for_selector("#screens:not([inert])", timeout=8000)
        check("a wide screen shows the rail of sections, not the phone's tabs",
              wide.is_visible(".rail") and not wide.is_visible(".tabs") and wide.is_visible("#power"))
        wide.click("[data-action=section][data-value=bed]")
        check("Bed strip shows the bed's controls and wiring, and nothing else",
              wide.is_visible("#bed-state") and wide.is_visible("#set-bed") and not wide.is_visible("#power")
              and not wide.is_visible("#monitor-state") and wide.text_content("#page-title") == "Bed strip"
              and wide.url.endswith("#bed"))
        wide.click("[data-action=section][data-value=calibration]")
        check("Calibration gathers the calibration page and the fine colour settings",
              wide.is_visible("#set-calibrate") and wide.is_visible("#fine-colour") and not wide.is_visible("#set-bed"))
        wide.goto(base + "/#settings")
        wide.wait_for_timeout(300)
        check("an old phone link opens its section on a wide screen", wide.url.endswith("#setup")
              and wide.is_visible("#set-controller") and wide.text_content("#page-title") == "Setup")
        wide.set_viewport_size({"width": 390, "height": 844})
        wide.wait_for_timeout(300)
        check("narrowing the window turns the rail back into the tabs, on the tab that holds the section",
              wide.is_visible(".tabs") and not wide.is_visible(".rail") and wide.url.endswith("#settings")
              and wide.is_visible("#set-controller") and wide.is_visible("#set-calibrate"))
        wide.close()

        light = browser.new_page(color_scheme="light")
        light.goto(base + "/")
        colours = light.evaluate("getComputedStyle(document.body).backgroundColor")
        check("a phone in light mode gets the light look", colours == "rgb(255, 243, 238)", colours)
        light.goto(base + "/#settings")
        light.wait_for_selector("#screens:not([inert])", timeout=8000)
        light.click("#set-access summary")
        light.click("[data-action=theme][data-value=dark]")
        dark_now = light.evaluate("getComputedStyle(document.body).backgroundColor")
        light.reload()
        light.wait_for_selector("#screens:not([inert])", timeout=8000)
        dark_kept = light.evaluate("getComputedStyle(document.body).backgroundColor")
        check("Dark turns a light-mode device dark, and it stays dark after a reload",
              dark_now == dark_kept == "rgb(13, 7, 22)", (dark_now, dark_kept))
        light.click("#set-access summary")
        light.click("[data-action=theme][data-value='']")
        check("Auto goes back to following the device",
              light.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 243, 238)"
              and light.get_attribute("[data-action=theme][data-value='']", "aria-pressed") == "true")
        light.close()
        browser.close()
    # Aborted requests log a network error to the console; anything else is the page's own fault.
    own = [p for p in problems if "Failed to load resource" not in p]
    check("the page logged no errors along the way", not own, own[:5])
    get("/api/sync/off")
    get("/api/set?bed_leds=0&bed_mode=screen")
    get("/api/profile/vivid")


def test_page_in_webkit(base):
    print("\n48. The phone page in WebKit, Safari's engine, as an iPhone and an iPad")
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        print("    (skipped: playwright not installed)")
        return
    with sync_playwright() as play:
        try:
            browser = play.webkit.launch()
        except PlaywrightError:
            print("    (skipped: no WebKit for playwright; `python -m playwright install webkit` adds it)")
            return
        for device, places in (("iPhone SE", ("home", "tune", "room", "settings")),
                               ("iPad Pro 11 landscape", ("overview", "monitor", "bed", "mouse", "looks", "room",
                                                          "calibration", "setup"))):
            context = browser.new_context(**play.devices[device])
            page = context.new_page()
            problems = []
            page.on("pageerror", lambda exc: problems.append(str(exc)))
            page.goto(base + "/")
            page.wait_for_selector("#screens:not([inert])", timeout=10000)
            rail = len(places) == 8
            check(f"{device}: {'the rail of sections' if rail else 'the tabs'} and nothing else",
                  page.is_visible(".rail") == rail and page.is_visible(".tabs") == (not rail))
            spilled = []
            for place in places:
                page.evaluate(f"location.hash = '{place}'")
                page.wait_for_timeout(200)
                if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                    spilled.append(place)
            check(f"{device}: nothing runs off the side of any screen", not spilled, spilled)
            page.evaluate(f"location.hash = '{'setup' if rail else 'settings'}'")
            page.wait_for_timeout(200)
            page.tap("#set-expert summary")
            try:
                page.wait_for_selector("#x-band_height", timeout=5000)
                loaded = True
            except PlaywrightError:
                loaded = False
            check(f"{device}: expert settings load when opened", loaded)
            check(f"{device}: the page's script ran without an error", not problems, problems[:3])
            context.close()
        browser.close()


def test_soak_verdict():
    print("\n43. The soak script's verdict on memory and frame rate")
    import math
    import soak

    def run_of(minutes=120, rss=lambda t: 50.0, fresh=lambda i: 33.0, drops=0, paused=()):
        """A sample every 10 s, shaped like soak.sample()'s."""
        return [{"t": i * 10.0, "rss_mb": rss(i * 10.0), "paused": i in paused,
                 "fresh_fps": 0.0 if i in paused else fresh(i), "delivered_fps": 60.0,
                 "delivered": i * 600, "dropped": i * drops} for i in range(minutes * 6)]

    def verdict(samples):
        return {name: ok for name, ok, _ in soak.judge(samples, 60)}

    steady = verdict(run_of(rss=lambda t: 50 + 0.3 * math.sin(t / 60)))
    check("a steady two-hour run passes every check, the leak check included",
          all(steady.values()) and "memory stays flat" in steady, steady)
    check("a 10 MB an hour leak fails", verdict(run_of(rss=lambda t: 50 + 10 * t / 3600))["memory stays flat"] is False)
    check("memory that jumps 30 MB fails, however short the run",
          verdict(run_of(minutes=2, rss=lambda t: 80.0 if t > 60 else 50.0))["memory does not grow"] is False)
    check("single readings swinging by tens of MB are not a leak",
          verdict(run_of(rss=lambda t: 50.0 + (35.0 if int(t) % 70 == 0 else 0.0)))["memory does not grow"])
    check("fresh frames collapsing now and then fails",
          verdict(run_of(fresh=lambda i: 5.0 if i % 10 == 0 else 33.0))["fresh frames never collapse"] is False)
    check("2% dropped frames fails", verdict(run_of(drops=12))["dropped frames stay rare"] is False)
    check("time with the screen locked or asleep is left out",
          all(verdict(run_of(paused=set(range(300, 360)))).values()))
    short = verdict(run_of(minutes=1))
    check("a one-minute run is judged, but not for a slow leak",
          all(short.values()) and "memory stays flat" not in short, short)
    check("no samples is a failure, not a pass", verdict([]) == {"enough samples to judge": False})

    check("ps CPU times are read in every form",
          (soak.cpu_seconds("0:01.23"), soak.cpu_seconds("12:34.50"), soak.cpu_seconds("1-02:03:04.00"))
          == (1.23, 754.5, 93784.0))
    try:
        soak.ports(8079)
        refused = False
    except SystemExit:
        refused = True
    check("the soak refuses a port base that lands on the live hub", refused)


def test_soak_live():
    print("\n44. A short soak against its own sandbox hub")
    import soak
    from ambilight.telemetry import STAGES

    out = os.path.join(tempfile.mkdtemp(prefix="ambilight-soak-test-"), "samples.jsonl")
    samples = soak.run(PORT_BASE + 2, seconds=6, every=1.0, layout="frame", fps=60, out=out, log=lambda line: None)
    check("it samples for the whole run", len(samples) >= 6 and samples[-1]["t"] >= 6, len(samples))
    check("every sample has memory, CPU and every stage",
          all(s["rss_mb"] > 0 and s["cpu_s"] >= 0 and set(s["stages_ms"]) == set(STAGES) for s in samples))
    check("the fake board counts the packets", samples[-1]["packets"] > samples[0]["packets"])
    check("the hub delivers frames throughout", min(s["delivered_fps"] for s in samples) > 20,
          [s["delivered_fps"] for s in samples])
    with open(out) as fh:
        check("every sample is saved as a JSON line", len(fh.read().splitlines()) == len(samples))
    check("the summary covers every stage", all(any(name in line for line in soak.summary(samples)) for name in STAGES))


def test_sync_worktrees():
    print("\n46. Keeping worktrees in step with the shared branch")
    import sync_worktrees as sw

    def tree(branch, dirty=False, ahead=0, behind=0, up_ahead=0):
        return {"branch": branch, "dirty": dirty, "ahead_base": ahead, "behind_base": behind,
                "ahead_upstream": up_ahead, "behind_upstream": 0}

    base = sw.BASE
    check("a clean checkout of the shared branch that is behind is fast-forwarded",
          sw.decide(tree(base, behind=2, ahead=0))[0] == "fast-forward")
    check("one with uncommitted work is left alone, however far behind",
          sw.decide(tree(base, dirty=True, behind=2))[0] == "skip")
    check("a shared branch that has diverged is reported, never merged",
          sw.decide(tree(base, behind=1, ahead=1))[0] == "report")
    check("unpushed commits on the shared branch are reported",
          sw.decide(tree(base, ahead=1, up_ahead=1))[0] == "report")
    check("a shared branch already level needs nothing", sw.decide(tree(base))[0] == "ok")
    check("a workstream branch that is behind is reported for its own session, not changed",
          sw.decide(tree("ws/capture", behind=3, ahead=2))[0] == "report")
    check("a workstream branch with unpushed commits is reported",
          sw.decide(tree("ws/capture", up_ahead=2))[0] == "report")
    check("a level workstream branch needs nothing", sw.decide(tree("ws/capture"))[0] == "ok")
    check("a detached checkout is skipped", sw.decide(tree(None))[0] == "skip")
    check("main is never touched, even when behind", sw.decide(tree("main", behind=5))[0] == "skip")
    check("only a fast-forward is ever applied",
          {sw.decide(tree(b, d, a, bh, u))[0] for b in (base, "ws/x", "main", None)
           for d in (False, True) for a in (0, 1) for bh in (0, 1) for u in (0, 1)}
          <= {"fast-forward", "report", "skip", "ok"})


def test_latency_script():
    print("\n45. The camera latency script, in a simulated room")
    import latency
    from ambilight import camrun

    times = [i / 30 for i in range(30)]
    hit = latency.crossing(times, [0.0] * 10 + [0.25, 0.75] + [1.0] * 18, times[5])
    check("a crossing is interpolated between camera frames", hit is not None and abs(hit - 10.5 / 30) < 1e-9, hit)
    hit = latency.crossing(times, [0.0] * 10 + [0.9] + [0.0] * 4 + [1.0] * 15, times[5])
    check("a one-frame blip is not taken for the change", hit is not None and abs(hit - 14.5 / 30) < 1e-9, hit)
    check("a light that does not change gives no reading", latency.crossing(times, [0.3] * 30, times[5]) is None)
    screen = [(t, 0.0 if i < 10 else 1.0, 0.0 if i < 13 else 1.0) for i, t in enumerate(times)]
    check("a flash's latency is the wall's crossing minus the screen's",
          abs(latency.trial_latency(screen, times[5]) - 0.1) < 1e-9)

    for delay in (80, 150):
        result, truth = latency.dry_run(delay)
        check(f"the simulated camera recovers a {delay} ms delay to within 10 ms",
              result["usable"] == result["trials"] and abs(result["median_ms"] - truth) <= 10, (result, truth))
        check(f"and reports a p95 no lower than the median ({delay} ms)", result["p95_ms"] >= result["median_ms"])

    def opened(*args, **kwargs):
        raise AssertionError("a camera was opened")

    saved = (camrun.list_cameras, camrun.Camera.__init__, camrun.open_camera, camrun.PatchWindow)
    camrun.Camera.__init__ = camrun.open_camera = camrun.PatchWindow = opened
    try:
        camrun.list_cameras = opened
        check("the dry run passes without touching a camera or a hub", latency.main(["--dry-run"]) == 0)
        try:
            latency.main([])
            refused = False
        except SystemExit as exc:
            refused = exc.code == 2
        check("a real run without --camera is refused", refused)
        camrun.list_cameras = lambda: [("FaceTime HD Camera", True), ("Lenovo FHD Webcam", False)]
        check("naming the built-in camera stops before anything is opened",
              latency.main(["--camera", "FaceTime", "--port", "9"]) == 1)
    finally:
        camrun.list_cameras, camrun.Camera.__init__, camrun.open_camera, camrun.PatchWindow = saved


def test_look_notes():
    print("\n36b. Looks with a description: saved, kept, carried and bounded")
    from ambilight import backup
    from ambilight.config import NOTE_MAX, PROFILES, look_note

    path = os.path.join(tempfile.mkdtemp(prefix="ambilight-notes-"), "config.json")
    cfg = Config(path)

    def refused(action, *args):
        try:
            action(*args)
        except ValueError as exc:
            return str(exc)
        return ""

    cfg.save_profile("Day", "Bright and plain for daytime work")
    check("a look can be saved with a description, kept beside its settings",
          cfg.get("user_profiles")["Day"]["note"] == "Bright and plain for daytime work")
    cfg.update({"saturation": 1.1})
    cfg.save_profile("Day")
    check("saving it again without one keeps the description, and takes the new settings",
          cfg.get("user_profiles")["Day"]["note"] == "Bright and plain for daytime work"
          and cfg.get("user_profiles")["Day"]["saturation"] == 1.1)
    cfg.save_profile("Day", "")
    check("saving it with an empty one clears it", "note" not in cfg.get("user_profiles")["Day"])
    cfg.describe_profile("Day", "  Warm\n white,\t4500 K  ")
    check("a description is one tidy line: spaces and line breaks collapse",
          cfg.get("user_profiles")["Day"]["note"] == "Warm white, 4500 K", cfg.get("user_profiles")["Day"].get("note"))
    check("control characters never reach it", look_note("a\x00b\x1bc") == "a b c")
    check("a description is not a setting: using the look sets the settings and leaves it out",
          cfg.apply_profile("Day") is not None and "note" not in cfg.snapshot() and cfg.get("profile") == "Day")
    check("a description of exactly the limit is kept; one more is refused, not cut",
          len(look_note("x" * NOTE_MAX)) == NOTE_MAX and "at most" in refused(look_note, "x" * (NOTE_MAX + 1)))
    check("only text is a description", refused(look_note, 5) != "" and refused(look_note, ["a"]) != "")
    check("a built-in look's description is fixed", "built-in" in refused(cfg.describe_profile, "vivid", "mine")
          and "built-in" in refused(cfg.describe_profile, "immersive", ""))
    check("a look that does not exist cannot be described", "unknown" in refused(cfg.describe_profile, "Gone", "x"))

    cfg.rename_profile("Day", "Daytime")
    check("a renamed look keeps its description", cfg.get("user_profiles")["Daytime"]["note"] == "Warm white, 4500 K")
    cfg.duplicate_profile("Daytime", "Daytime 2")
    check("a copy of your look takes the description along", cfg.get("user_profiles")["Daytime 2"]["note"] == "Warm white, 4500 K")
    cfg.duplicate_profile("immersive", "Mine")
    check("a copy of a built-in starts with none, for you to write", "note" not in cfg.get("user_profiles")["Mine"])
    cfg.delete_profile("Daytime 2")
    check("deleting a look deletes its description with it", "Daytime 2" not in cfg.get("user_profiles"))
    check("descriptions survive a restart", Config(path).get("user_profiles")["Daytime"]["note"] == "Warm white, 4500 K")

    with open(path) as fh:
        stored = json.load(fh)
    stored["user_profiles"]["Long"] = {"saturation": 1.2, "note": "y" * 400}
    stored["user_profiles"]["Odd"] = {"saturation": 1.2, "note": ["not", "text"]}
    with open(path, "w") as fh:
        json.dump(stored, fh)
    reloaded = Config(path).get("user_profiles")
    check("an over-long description on disk is cut, and the look is kept",
          len(reloaded["Long"]["note"]) == NOTE_MAX and reloaded["Long"]["saturation"] == 1.2)
    check("a description that is not text costs the look, like any other damaged value", "Odd" not in reloaded)

    # Backups carry descriptions, bounded like everything else read from a file.
    snap = cfg.snapshot()
    exported = backup.export(snap, "looks", "test", "now")
    check("a looks backup carries the descriptions", exported["settings"]["user_profiles"]["Daytime"]["note"] == "Warm white, 4500 K")
    other = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-notes2-"), "config.json"))
    data = backup.parse(json.dumps({"format": "pudisync-backup", "schema_version": 1, "settings": {
        "user_profiles": {"Fresh": {"saturation": 1.1, "note": "From a file  ", "evil": 1},
                          "Long": {"saturation": 1.1, "note": "z" * 500}}}}).encode())
    report = backup.plan(other, data)
    entries = {e["name"]: e for e in report["looks"]}
    check("an imported look keeps its description, tidied", entries["Fresh"]["look"]["note"] == "From a file")
    check("...and the description is not listed as an ignored setting", entries["Fresh"]["ignored"] == ["evil"])
    check("...an over-long one is cut to the limit", len(entries["Long"]["look"]["note"]) == NOTE_MAX)
    backup.apply(other, report, ["looks"], other.restore_folder(), "test", 1_000_000.0)
    check("applying it stores the description", other.get("user_profiles")["Fresh"]["note"] == "From a file")
    check("a built-in's settings still carry no description", all("note" not in v for v in PROFILES.values()))


def test_look_notes_api(base):
    print("\n36c. Describing a look over HTTP")

    def call(path):
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    code, st = call("/api/profiles/save?name=Film&note=Dark%20room%2C%20low%20warmth")
    check("Save takes a description", code == 200 and st["user_profiles"]["Film"]["note"] == "Dark room, low warmth", st.get("user_profiles"))
    code, st = call("/api/profiles/describe?name=Film&note=Cinema%20nights")
    check("Describe changes it", code == 200 and st["user_profiles"]["Film"]["note"] == "Cinema nights")
    code, st = call("/api/profiles/save?name=Film")
    check("saving again without one keeps it", st["user_profiles"]["Film"]["note"] == "Cinema nights")
    code, st = call("/api/profiles/describe?name=Film&note=")
    check("an empty one clears it", code == 200 and "note" not in st["user_profiles"]["Film"])
    check("a built-in cannot be described", call("/api/profiles/describe?name=vivid&note=x")[0] == 400)
    check("an unknown look is refused", call("/api/profiles/describe?name=Nope&note=x")[0] == 400)
    check("a description over the limit is refused with the reason",
          call("/api/profiles/describe?name=Film&note=" + "x" * 121)[0] == 400)
    code, listing = call("/api/profiles")
    check("the look list says which are built in and which are yours", code == 200 and "Film" in listing["user"]
          and list(listing["builtin"]) == ["vivid", "immersive"], listing.get("builtin"))
    call("/api/profiles/delete?name=Film")


def test_backup():
    print("\n46. Backups: a file of your settings, read back only after a report")
    from ambilight import backup
    from ambilight.config import DEFAULTS, MAX_PROFILES, PROFILE_KEYS

    folder = tempfile.mkdtemp(prefix="ambilight-backup-")
    cfg = Config(os.path.join(folder, "config.json"))
    cfg.update({"pin": "4321", "saturation": 1.5, "zones": [{"start": 0, "end": 9, "mode": "off"}]})
    cfg.save_profile("Late night")
    grouped = [k for keys in backup.GROUPS.values() for k in keys]
    check("every setting has one place: a group to restore, or this hub only",
          sorted(grouped + list(backup.LOCAL_ONLY)) == sorted(DEFAULTS) and len(set(grouped)) == len(grouped))

    full = backup.export(cfg.snapshot(), "full", "test", "now")
    check("a full backup carries every portable setting", set(full["settings"]) == set(grouped))
    check("...and never the PIN, protected mode or a power limit",
          not set(full["settings"]) & set(backup.LOCAL_ONLY) and "4321" not in json.dumps(full))
    looks = backup.export(cfg.snapshot(), "looks", "test", "now")
    check("a looks file holds your looks and nothing else",
          list(looks["settings"]) == ["user_profiles"] and "Late night" in looks["settings"]["user_profiles"])

    def refusal(raw):
        try:
            backup.parse(raw)
        except ValueError as exc:
            return str(exc)
        return ""

    good = {"format": "pudisync-backup", "schema_version": 1, "settings": {}}
    check("an oversized file is refused before it is read", "kB" in refusal(b" " * (backup.MAX_BYTES + 1)))
    check("so is one that is not JSON", "not valid JSON" in refusal(b"{nope"))
    check("so is one nested far too deeply, without crashing", refusal(b"[" * 100_000) != ""
          and "deeply" in refusal(json.dumps({**good, "settings": {"zones": [[[[[[1]]]]]]}}).encode()))
    check("a file from something else is refused", "not a PudiSync" in refusal(json.dumps({"seg": []}).encode()))
    check("a newer format is refused with the reason",
          "newer" in refusal(json.dumps({**good, "schema_version": 2}).encode()))
    check("a missing or odd version is refused", refusal(json.dumps({**good, "schema_version": True}).encode()) != ""
          and refusal(json.dumps({**good, "schema_version": "1"}).encode()) != "")

    settings = {"saturation": 2.0, "blend": 0.5, "fps": 500, "layout": "circle", "mouse_sync": {"on": 1},
                "pin": "0000", "power_limit_ma": 20000, "unprotected": False, "rgb_mode": "disco",
                "led_count": 120, "zones": [{"start": 0, "end": 300, "mode": "off"}],
                "user_profiles": {"Late night": {"saturation": 0.5}, "Fresh": {"saturation": 1.1, "evil": 1},
                                  "vivid": {"saturation": 3}, "../x": {}, "Odd": {"saturation": [1]}}}
    data = backup.parse(json.dumps({**good, "settings": settings}).encode())
    before = cfg.snapshot()
    report = backup.plan(cfg, data)
    check("planning an import changes nothing", cfg.snapshot() == before)
    rows = {r["key"]: r for r in report["rows"]}
    check("a new value is a change", rows["saturation"]["status"] == "change" and rows["saturation"]["stored"] == 2.0)
    check("a value already in place is the same", rows["blend"]["status"] == "same")
    check("a value the hub would clamp says so, with what was asked",
          rows["fps"]["status"] == "adjusted" and rows["fps"]["stored"] == 120 and rows["fps"]["asked"] == 500)
    check("an invalid value is refused with the reason",
          rows["layout"]["status"] == "rejected" and "strip" in rows["layout"]["reason"])
    check("an object where a switch belongs is refused, not read as on", rows["mouse_sync"]["status"] == "rejected")
    check("the PIN, protected mode and the power limit are kept from this hub, and not echoed",
          all(rows[k]["status"] == "local" and "stored" not in rows[k] for k in ("pin", "unprotected", "power_limit_ma")))
    check("a setting this hub does not have is listed as unknown", rows["rgb_mode"]["status"] == "unknown")
    check("zones are checked against the strip length they would live with",
          rows["zones"]["stored"][0]["end"] == 119, rows["zones"].get("stored"))
    states = {e["name"]: e["status"] for e in report["looks"]}
    check("each look says whether it is new, replaces yours, or is refused",
          states == {"Late night": "replaces", "Fresh": "new", "vivid": "rejected", "../x": "rejected",
                     "Odd": "rejected"}, states)
    check("a look's unknown settings are listed and left out",
          next(e for e in report["looks"] if e["name"] == "Fresh")["ignored"] == ["evil"])

    result = backup.apply(cfg, report, ["looks"], cfg.restore_folder(), "test", 1_000_000.0)
    after = cfg.snapshot()
    check("applying the looks group changes only looks and picture settings",
          after["saturation"] == 2.0 and after["fps"] == 120 and after["led_count"] == before["led_count"]
          and after["zones"] == before["zones"], result["changed"])
    check("...never the PIN or the power limit",
          after["pin"] == "4321" and after["power_limit_ma"] == before["power_limit_ma"] and after["unprotected"])
    check("...merges the looks into yours",
          set(after["user_profiles"]) == {"Late night", "Fresh"} and after["user_profiles"]["Late night"]["saturation"] == 0.5)
    check("...reads every value back as planned", result["mismatched"] == [])
    point = cfg.restore_folder() / result["restore_point"]
    saved = json.loads(point.read_text())
    check("a restore point was saved first, without the PIN",
          saved["settings"]["saturation"] == 1.5 and "pin" not in saved["settings"] and "4321" not in point.read_text())
    restored = backup.restore(cfg, cfg.restore_folder(), result["restore_point"], "test", 1_000_001.0)
    check("restoring it puts every setting back exactly", cfg.snapshot() == before, restored["changed"])
    check("...after saving a restore point of its own, so a restore can be undone too",
          restored["restore_point"] and restored["restore_point"] != result["restore_point"])

    def refused(action):
        try:
            action()
        except (ValueError, FileNotFoundError) as exc:
            return str(exc)
        return ""

    check("a restore point is named, never a path",
          refused(lambda: backup.restore(cfg, cfg.restore_folder(), "../config.json", "test", 0)) != ""
          and refused(lambda: backup.restore(cfg, cfg.restore_folder(), "config-x.json", "test", 0)) != "")
    check("an unknown group is refused", "unknown group" in refused(lambda: backup.changes_for(report, ["power"], {})))
    crowded = {f"Look {i}": {"saturation": 1.0} for i in range(MAX_PROFILES)}
    check("looks that would pass the limit are refused, not silently cut",
          "more than" in refused(lambda: backup.changes_for(report, ["looks"], crowded)))
    full_plan = backup.plan_looks(cfg, crowded, {"One more": {"saturation": 1.0}})
    check("a look past the limit of 20 is marked as skipped", full_plan[0]["status"] == "full")
    check("nothing to change makes no restore point",
          backup.apply(cfg, backup.plan(cfg, backup.parse(json.dumps(good).encode())), ["looks"],
                       cfg.restore_folder(), "test", 0)["restore_point"] is None)

    for i in range(backup.KEEP_RESTORE_POINTS + 3):
        backup.save_restore_point(cfg.snapshot(), cfg.restore_folder(), "test", 2_000_000.0 + i)
    kept = backup.restore_points(cfg.restore_folder())
    check(f"only the newest {backup.KEEP_RESTORE_POINTS} restore points are kept",
          len(kept) == backup.KEEP_RESTORE_POINTS, len(kept))
    check("...newest first, with a time a browser can read",
          kept[0]["saved_at"] > kept[-1]["saved_at"] and kept[0]["saved_at"][-6] in "+-" and kept[0]["saved_at"][-3] == ":",
          kept[0]["saved_at"])

    held = backup.Pending(hold_s=10)
    token = held.hold({"rows": []}, now=100.0)
    check("a wrong id does not cancel the import that is held", refused(lambda: held.take("nope", 101.0)) != ""
          and held.take(token, 101.0) == {"rows": []})
    check("an import applies once", refused(lambda: held.take(token, 102.0)) != "")
    token = held.hold({"rows": []}, now=100.0)
    check("an import left too long expires", "expired" in refused(lambda: held.take(token, 111.0)))
    check("a look keeps the settings a look can hold", set(PROFILE_KEYS) >= set(after["user_profiles"]["Fresh"]))


def test_privacy():
    print("\n49. Privacy: who may redirect or expose the screen, what is kept, what may reach the network")
    import ast
    import pathlib
    import stat
    from urllib.parse import parse_qs
    from ambilight import access, backup, privacy, routes
    from ambilight.discovery import lan_ip

    check("this Mac is recognised by loopback, IPv4 or IPv6",
          all(access.is_this_mac(a) for a in ("127.0.0.1", "::1", "::ffff:127.0.0.1")))
    check("...and by its own network address, as the Mac app reaches it", access.is_this_mac(lan_ip()), lan_ip())
    check("another device, or nonsense, is not this Mac",
          not access.is_this_mac("203.0.113.9") and not access.is_this_mac("fe80::1%en0") and not access.is_this_mac("x"))

    class Untouchable:
        """Fails the test if an owner-only action reached the hardware or the disk."""

        def __getattr__(self, name):
            raise AssertionError(f"{name} ran for someone who is not the owner")

    class Stub:
        def __init__(self, cfg):
            self.cfg, self.intent = cfg, type("Intent", (), {"nudge": lambda self: None})()
            self.camera = self.provisioner = self.engine = self.pending_import = Untouchable()

        def status(self):
            return {}

    folder = tempfile.mkdtemp(prefix="ambilight-privacy-")
    cfg = Config(os.path.join(folder, "config.json"))
    hub = Stub(cfg)

    def ask(url, owner=False):
        path, _, query = url.partition("?")
        return routes.handle(hub, path, parse_qs(query, keep_blank_values=True), owner=owner)

    for url in ("/api/set?wled_ip=203.0.113.9", "/api/set?wled_port=9", "/api/set?output=serial",
                "/api/set?new_pin=1234", "/api/set?unprotected=0", "/api/set?http_port=9000",
                "/api/camera/start", "/api/record/start", "/api/flash/networks",
                "/api/flash/start?port=/dev/x&ssid=y", "/api/flash/adopt", "/api/backup/apply?id=x&groups=looks,connection"):
        payload, code = ask(url)
        check(f"another device without the PIN is refused: {url}", code == 409 and "PIN" in payload["error"], (code, payload))
    check("...and nothing changed", cfg.get("wled_ip") == "4.3.2.1" and cfg.get("pin") == "" and cfg.get("output") == "udp")
    check("lights and looks stay open to every device on the Wi-Fi",
          ask("/api/set?saturation=1.5&bed_mode=off")[1] == 200 and cfg.get("saturation") == 1.5)
    check("this Mac, or a device with the PIN, may redirect the strip",
          ask("/api/set?wled_ip=192.168.1.40", owner=True)[1] == 200 and cfg.get("wled_ip") == "192.168.1.40")

    point = backup.save_restore_point({**cfg.snapshot(), "wled_ip": "203.0.113.9"}, cfg.restore_folder(), "t", 1_000_000.0)
    hub.intent = type("Intent", (), {"nudge": lambda self: None})()
    payload, code = ask(f"/api/backup/restore?file={point}")
    check("a restore that would redirect the strip needs the owner", code == 409 and cfg.get("wled_ip") == "192.168.1.40",
          (code, payload))
    point = backup.save_restore_point({**cfg.snapshot(), "saturation": 1.1}, cfg.restore_folder(), "t", 1_000_001.0)
    check("...one that only changes the look does not", ask(f"/api/backup/restore?file={point}")[1] == 200
          and cfg.get("saturation") == 1.1)

    home = pathlib.Path(tempfile.mkdtemp(prefix="ambilight-files-"))
    (home / "backups").mkdir()
    (home / "recordings").mkdir()
    exposed = [home / "config.json", home / "config.journal.jsonl", home / "backups" / "config-1.json",
               home / "recordings" / "calibration-view.jpg"]
    for path in exposed:
        path.write_text("x")
        os.chmod(path, 0o644)
    log_file = open(home / "hub.log", "w")
    os.chmod(home / "hub.log", 0o644)
    before = os.umask(0o022)
    try:
        privacy.lock_down(home / "config.json", home / "recordings", (log_file,))
        (home / "new.json").write_text("x")
        new_mode = stat.S_IMODE((home / "new.json").stat().st_mode)
    finally:
        os.umask(before)
        log_file.close()
    modes = {p.name: oct(stat.S_IMODE(p.stat().st_mode)) for p in exposed + [home / "hub.log"]}
    check("files the hub wrote before, and the log, become readable by this account alone",
          set(modes.values()) == {"0o600"}, modes)
    check("...folders too", oct(stat.S_IMODE((home / "recordings").stat().st_mode)) == "0o700")
    check("every file the hub writes from then on is private", new_mode == 0o600, oct(new_mode))
    for i in range(privacy.KEEP_RECORDINGS + 3):
        (home / "recordings" / f"rec-202611{i + 10:02d}-000000.npz").write_bytes(b"x")
    gone = privacy.prune_recordings(home / "recordings")
    kept = sorted(p.name for p in (home / "recordings").glob("rec-*.npz"))
    check(f"only the newest {privacy.KEEP_RECORDINGS} colour recordings are kept",
          len(kept) == privacy.KEEP_RECORDINGS and len(gone) == 3 and kept[0] == "rec-20261113-000000.npz", gone)
    check("...and the calibration files beside them are left alone", (home / "recordings" / "calibration-view.jpg").exists())

    # Every module that can open a network connection, and why. A new one fails here until it is reviewed.
    allowed = {"access.py": "binds a probe socket to tell this Mac's addresses; sends nothing",
               "discovery.py": "finds this Mac's LAN address without sending a packet; Bonjour name",
               "hostcache.py": "looks up the board's .local name",
               "server.py": "the hub's own web server",
               "wled.py": "the board's JSON API", "board.py": "puts the phone page on the board",
               "output_wled_udp.py": "LED colours to the board", "output_wled_ddp.py": "LED colours to the board",
               "camrun.py": "the webcam calibration talks to this hub on 127.0.0.1",
               "provision.py": "WLED firmware from GitHub, only when setting up a new board"}
    net = {"socket", "urllib.request", "http.client", "http.server", "ssl", "requests", "urllib3", "websocket",
           "asyncio", "smtplib", "ftplib", "aiohttp", "httpx"}
    found = set()
    for path in pathlib.Path("ambilight").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
                     if isinstance(node, ast.ImportFrom) else [])
            if any(n in net or n.split(".")[0] in net - {"urllib"} for n in names):
                found.add(path.name)
    check("only the reviewed modules can reach the network: nothing phones home", found == set(allowed),
          sorted(found ^ set(allowed)))
    from ambilight.provision import wled_release_link
    check("firmware comes only from WLED's releases on GitHub, over HTTPS",
          wled_release_link("https://github.com/wled/WLED/releases/download/v16.0.1/WLED_16.0.1_ESP8266.bin")
          and not wled_release_link("http://github.com/wled/WLED/releases/download/v16/x.bin")
          and not wled_release_link("https://evil.example/wled/WLED/releases/download/x.bin")
          and not wled_release_link("https://github.com/someone/WLED/releases/download/x.bin"))


def test_backup_api(base):
    print("\n47. Backups over HTTP: export, preview, apply, undo")

    def call(path, body=None, headers=None):
        req = urllib.request.Request(base + path, data=body, headers=headers or {},
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    as_json = {"Content-Type": "application/json"}
    code, full = call("/api/backup/export")
    check("a backup downloads as JSON", code == 200 and full["format"] == "pudisync-backup" and "fps" in full["settings"])
    check("...and never holds the PIN", "pin" not in full["settings"])
    check("a bad kind is a 400", call("/api/backup/export?kind=everything")[0] == 400)
    start = call("/api/config")[1]
    upload = json.dumps({**full, "settings": {**full["settings"], "saturation": 0.4, "power_limit_ma": 20000}}).encode()
    check("a preview needs JSON", call("/api/backup/preview", upload, {"Content-Type": "text/plain"})[0] == 415)
    check("a file from another website is blocked",
          call("/api/backup/preview", upload, {**as_json, "Sec-Fetch-Site": "cross-site"})[0] == 403)
    check("an oversized file is refused", call("/api/backup/preview", b" " * 300_000, as_json)[0] == 413)
    check("only the preview takes a body", call("/api/set", upload, as_json)[0] == 404)
    check("a file that is not a backup is a 400", call("/api/backup/preview", b"{}", as_json)[0] == 400)
    code, plan = call("/api/backup/preview", upload, as_json)
    rows = {r["key"]: r["status"] for r in plan.get("rows", [])}
    check("a preview reports each setting and holds the import",
          code == 200 and plan["import_id"] and rows.get("saturation") == "change"
          and rows.get("power_limit_ma") == "local", rows.get("saturation"))
    check("...and changes nothing yet", call("/api/config")[1]["saturation"] == start["saturation"])
    code, done = call(f"/api/backup/apply?id={plan['import_id']}&groups=looks")
    check("apply stores it and names the restore point",
          code == 200 and done["saturation"] == 0.4 and done["restore_point"] and done["mismatched"] == [], done.get("changed"))
    check("the power limit stayed this hub's", done["power_limit_ma"] == start["power_limit_ma"])
    check("the same import cannot be applied twice",
          call(f"/api/backup/apply?id={plan['import_id']}&groups=looks")[0] == 400)
    check("the restore points are listed", any(p["file"] == done["restore_point"]
                                               for p in call("/api/backup/list")[1]["restore_points"]))
    code, undone = call(f"/api/backup/restore?file={done['restore_point']}")
    check("undo puts it back", code == 200 and undone["saturation"] == start["saturation"])
    check("a restore point outside the folder is not found",
          call("/api/backup/restore?file=../config.json")[0] == 404)
    try:
        check("a PIN guards the preview like everything else", call("/api/set?new_pin=4321")[0] == 200
              and call("/api/backup/preview", upload, as_json)[0] == 403
              and call("/api/backup/preview", upload, {**as_json, "X-Ambilight-Pin": "4321"})[0] == 200)
    finally:
        call("/api/set?new_pin=", None, {"X-Ambilight-Pin": "4321"})
    check("...and is cleared again for the tests after", call("/api/status")[0] == 200)


@contextmanager
def sandbox_hub():
    """A fake WLED plus a hub on its own ports and config, torn down afterwards."""
    folder = tempfile.mkdtemp(prefix="ambilight-hub-")
    config = os.path.join(folder, "config.json")
    with open(config, "w") as fh:
        json.dump({"wled_ip": "127.0.0.1", "wled_http_port": FAKE_WLED_HTTP,
                   "wled_port": FAKE_WLED_UDP, "ddp_port": FAKE_WLED_DDP, "monitor_index": 1}, fh)
    log = open(os.path.join(folder, "hub.log"), "w")
    procs = [
        subprocess.Popen([sys.executable, "tools/fake_wled.py", "--udp-port", str(FAKE_WLED_UDP),
                          "--ddp-port", str(FAKE_WLED_DDP), "--http-port", str(FAKE_WLED_HTTP), "--quiet"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([sys.executable, "hub.py", "--config", config,
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
    import gzip
    for url, path in (("/", PAGE), ("/app.css", "ambilight/web/app.css"), ("/app.js", "ambilight/web/app.js")):
        page = open(path, "rb").read()
        packed = urllib.request.Request(base + url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(packed, timeout=3) as r:
            encoding, vary, body = r.headers.get("Content-Encoding"), r.headers.get("Vary"), r.read()
        check(f"a browser that takes gzip gets {url} at under a third of its size",
              encoding == "gzip" and gzip.decompress(body) == page and len(body) * 3 < len(page)
              and "Accept-Encoding" in (vary or ""), (encoding, len(body), len(page)))
        with urllib.request.urlopen(base + url, timeout=3) as r:
            plain_encoding, plain = r.headers.get("Content-Encoding"), r.read()
        check(f"...and one that does not gets {url} plain", plain_encoding is None and plain == page)
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

    check("the hub reports that it is unprotected for now", call("/api/config")[1].get("unprotected") is True)
    check("protected mode is refused while no PIN is set", call("/api/set?unprotected=0")[0] == 400
          and call("/api/status")[0] == 200)
    pinned = {"X-Ambilight-Pin": "4321"}
    check("a PIN and protected mode go on together",
          call("/api/set?new_pin=4321&unprotected=0")[0] == 200)
    try:
        check("protected: no PIN is refused", call("/api/status")[0] == 403)
        check("protected: the PIN header unlocks it", call("/api/status", pinned)[0] == 200)
        check("protected: the config says so", call("/api/config", pinned)[1].get("unprotected") is False)
        check("protected: the PIN cannot be cleared", call("/api/set?new_pin=", pinned)[0] == 400)
    finally:
        call("/api/set?unprotected=1&new_pin=", pinned)
    check("unprotected again, and the PIN is cleared", call("/api/status")[0] == 200
          and call("/api/config")[1].get("unprotected") is True)


def test_connect_api(base):
    print("\n43. Connecting phones, tablets and the Mac")

    def get(path, headers=None):
        req = urllib.request.Request(base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, None, b""

    status = json.loads(get("/api/status")[2])
    check("status says where the board's own page is",
          status.get("board_urls") == [f"http://127.0.0.1:{FAKE_WLED_HTTP}"], status.get("board_urls"))

    code, _, body = get("/api/pair")
    targets = json.loads(body)["targets"] if code == 200 else []
    urls = [t["url"] for t in targets]
    check("pairing offers the hub by name, by address, and the board's page",
          [t["label"] for t in targets] == ["Hub", "Hub by IP address", "Strip's own page"], urls)
    check("the hub's .local name comes first, because it survives an address change",
          bool(urls) and urls[0].endswith(f".local:{SANDBOX_PORT}"), urls[:1])
    check("the board's page is where the hub reaches the board",
          urls[2:] == [f"http://127.0.0.1:{FAKE_WLED_HTTP}"], urls[2:])
    check("every code is square", all(len(t["modules"]) == len(t["modules"][0]) for t in targets))
    check("a phone camera reads the first code as the hub's address",
          bool(targets) and decode_qr(targets[0]["modules"]) == [urls[0]])

    a = {"X-Ambilight-Pin": "2468"}
    get("/api/set?new_pin=2468")
    try:
        check("a hub with a PIN shows its pairing codes only to a device that knows it",
              get("/api/pair")[0] == 403 and get("/api/pair", a)[0] == 200)
        get("/api/set?brightness=0.42", a)
        seen_by_b = json.loads(get("/api/status", a)[2])["brightness"]
        check("a change from one device shows on another's next poll", seen_by_b == 0.42, seen_by_b)
    finally:
        get("/api/set?new_pin=", a)
    check("the PIN is cleared again", get("/api/status")[0] == 200)

    import struct
    code, ctype, png = get("/apple-touch-icon.png")
    width, height = struct.unpack(">II", png[16:24]) if png[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)
    check("the Home Screen gets a 180 px PNG icon, which iOS needs (it ignores SVG)",
          code == 200 and ctype == "image/png" and (width, height) == (180, 180), (code, ctype, width, height))
    page = get("/")[2].decode()
    manifest = json.loads(get("/manifest.webmanifest")[2])
    check("the page and the manifest both point to it",
          'rel="apple-touch-icon" href="/apple-touch-icon.png"' in page
          and any(i["src"] == "/apple-touch-icon.png" and i["type"] == "image/png" for i in manifest["icons"]))
    code, ctype, svg = get("/icon.svg")
    check("the header, the tab icon and the manifest all show the one logo, /icon.svg",
          code == 200 and ctype.startswith("image/svg+xml") and b'aria-label="PudiSync"' in svg
          and '<img class="logo" src="/icon.svg"' in page and 'rel="icon" href="/icon.svg"' in page
          and any(i["src"] == "/icon.svg" for i in manifest["icons"]), (code, ctype))


def test_experience():
    print("\n35d. Experience engine: styles, audio as an add-on, Room Studio, safety")
    import math as _math
    import ambilight.engine as eng
    from ambilight import beat, experience as ex
    from ambilight.config import DEFAULTS, PROFILES
    from ambilight.safety import STROBE_MAX, Guard
    cfg = {**DEFAULTS, "led_count": 240, "left_led": 0, "right_led": 239, "fade_leds": 0}

    # Primitives are deterministic and bounded.
    x = np.linspace(0, 1, 240)
    check("noise and waves repeat exactly for the same seed and time",
          np.array_equal(ex.noise(x * 5, 3), ex.noise(x * 5, 3)) and np.array_equal(ex.waves(x, 1.3, 3, 2), ex.waves(x, 1.3, 3, 2)))
    check("...stay within -1..1", np.abs(ex.noise(x * 50, 1)).max() <= 1 and np.abs(ex.waves(x, 7.0, 5)).max() <= 1)
    check("...and are smooth: neighbouring LEDs never jump", np.abs(np.diff(ex.noise(x * 3, 9))).max() < 0.1)
    stops = [(0.0, ex.hex_rgb("#000000")), (0.5, ex.hex_rgb("#FF0000")), (1.0, ex.hex_rgb("#000000"))]
    check("a palette wraps without a seam", np.allclose(ex.palette(stops, [0.0]), ex.palette(stops, [1.0])))

    # Audio as an add-on: bounded, gated on confidence, and easing back when the sound goes.
    meter = beat.BassMeter()
    check("no sound yet: the feature frame is neutral and invalid", meter.features(0.0) == beat.neutral_features())
    rate = beat.RATE
    t = np.arange(int(rate * 2)) / rate
    music = (0.6 * np.sin(2 * np.pi * 60 * t) * (0.55 + 0.45 * (np.sin(2 * np.pi * 2 * t) > 0))).astype(np.float32)
    for start in range(0, len(music), 480):
        meter.feed(music[start:start + 480], (start + 480) / rate)
    live = meter.features(2.0)
    check("music gives a valid feature frame with every value in 0..1",
          live["valid"] and all(0.0 <= live[k] <= 1.0 for k in ex.FEATURES) and live["confidence"] > 0.5, live)
    check("...which goes stale and neutral once the sound stops", not meter.features(2.0 + beat.FRESH_S + 0.1)["valid"])
    mod = ex.AudioMod()
    loud = {**live, "valid": True, "confidence": 1.0, "bass_envelope": 1.0, "onset_strength": 1.0,
            "mid_energy": 1.0, "high_energy": 1.0}
    maps = ex.SCENES["neon_coast"]["audio_reactivity"]["mappings"]
    for i in range(120):
        got = mod.update(loud, maps, "expressive", i / 60)
    check("Expressive never goes past each mapping's depth", all(got[m["target"]] <= m["depth"] + 1e-6 for m in maps), got)
    off = ex.AudioMod().update(loud, maps, "off", 0.0)
    check("Off adds nothing at all", not any(off.values()), off)
    weak = ex.AudioMod()
    for i in range(60):
        gated = weak.update({**loud, "confidence": 0.1}, maps, "expressive", i / 60)
    check("sound below a mapping's confidence moves nothing", gated["impact_amount"] == 0 and gated["warp_strength"] == 0, gated)
    for i in range(120, 330):
        after = mod.update(beat.neutral_features(), maps, "expressive", i / 60)
    check("when the sound stops, every offset eases back to neutral", max(after.values()) < 0.01, after)

    # Monitor Sync styles keep the screen as the source.
    target = np.random.default_rng(3).random((240, 3)).astype(np.float32)

    def run(settings, frames=300, target=target, beat_at=None, plain=False):
        stage = eng.OutputStage()
        if plain:
            stage.sound.apply = lambda levels, *rest: levels
        stage.meter = beat.BassMeter()
        stage.set_target(target, 0.0)
        out = []
        for i in range(frames):
            now = 1.0 + i / 60
            if beat_at is not None and i == beat_at:
                stage.meter.fire(now)
            stage.set_target(target, now)       # as the capture thread does, so the strip never goes stale
            out.append(stage.frame(now, {**cfg, **settings}).astype(np.float32))
        return out, stage

    natural, _ = run(PROFILES["vivid"])
    check("Vivid with the sound off is exactly as before the experience engine",
          all(np.array_equal(a, b) for a, b in zip(natural, run(PROFILES["vivid"], plain=True)[0])))
    immersive, _ = run(PROFILES["immersive"])
    check("...and so is Immersive", all(np.array_equal(a, b) for a, b in zip(immersive, run(PROFILES["immersive"], plain=True)[0])))
    check("the sound is wanted only when a beat effect or Follow the sound asks for it, never under Calm",
          not eng.wants_sound(cfg) and eng.wants_sound({**cfg, "audio_react": "subtle"})
          and not eng.wants_sound({**cfg, "audio_react": "subtle", "calm": True})
          and not eng.wants_sound({**cfg, "bass_bounce": 0.8, "calm": True}))
    pulse_calm, _ = run({**{**PROFILES["vivid"], "bass_bounce": 0.8, "bass_shake": 0.5}, "calm": True}, beat_at=30)
    pulse_still, _ = run({**PROFILES["vivid"], "bass_bounce": 0.8, "bass_shake": 0.5})
    check("Calm stops the beat effects even when a beat arrives", np.array_equal(pulse_calm[40], pulse_still[40]))

    # Every effect on at once, beats at three a second: the one guard keeps the flashes within WCAG.
    every = {**PROFILES["vivid"], "bass_bounce": 1.0, "bass_shake": 1.0, "audio_react": "expressive"}
    stage = eng.OutputStage()
    stage.meter = beat.BassMeter()
    stage.set_target(np.tile(np.float32([[0.8, 0.7, 0.6]]), (240, 1)), 0.0)
    flashes = []
    for i in range(600):
        now = 1.0 + i / 60
        if i % 21 == 0:
            stage.meter.fire(now)
        stage.set_target(np.tile(np.float32([[0.8, 0.7, 0.6]]), (240, 1)), now)
        stage.frame(now, {**cfg, **every})
        flashes.append(len(stage.guard.flashes))
    check("every effect on, beats three a second: never more than three flashes a second reach the strip",
          max(flashes) <= STROBE_MAX + 1, max(flashes))
    check("...and every frame went through the guard", stage.guard.last_time is not None)

    # Room Studio: from a palette, no screen; continuous; through the same guard.
    room = eng.OutputStage()
    room.meter = beat.BassMeter()
    room.enter_room(0.0)
    for scene in ex.SCENES:
        frames = np.array([room.frame(i / 60, {**cfg, "room_scene": scene}) for i in range(60, 420)], np.float32)
        steps = np.abs(np.diff(frames.mean(axis=(1, 2))))
        check(f"{ex.SCENES[scene]['name']}: lit, moving and smooth, with the sound off",
              frames.mean() > 5 and np.abs(frames[-1] - frames[0]).mean() > 0.5 and steps.max() < 0.05 * 255,
              (round(float(frames.mean()), 1), round(float(steps.max()), 2)))
    check("a scene's frames go through the guard as screen frames do", room.guard.last_time is not None)
    check("a scene name that no longer exists falls back to Neon Coast",
          ex.scene_for({**cfg, "room_scene": "gone_scene"})["id"] == "neon_coast")
    long_run = [room.frame(1000.0 + i / 60, {**cfg, "room_scene": "neon_coast"}).astype(np.float32) for i in range(3)]
    check("hours in, there is no loop seam: frames still glide", np.abs(long_run[1] - long_run[0]).max() < 20)

    # Scene files: strict, versioned, never code or addresses.
    good = ex.SCENES["aurora"]
    check("every built-in scene validates against the schema", all(ex.validate_scene(s) == s for s in ex.SCENES.values()))
    for label, bad in (("an unknown field", {**good, "script": "rm"}),
                       ("an unknown renderer", {**good, "renderer": "fractal_2d"}),
                       ("another schema version", {**good, "schema_version": 2}),
                       ("a monitor style", {**good, "experience_type": "monitor_style"}),
                       ("an unknown audio feature", {**good, "audio_reactivity": {"mappings": [
                           {"feature": "microphone", "target": "motion_speed", "depth": 0.1, "attack_ms": 10, "release_ms": 50}]}}),
                       ("a safety setting", {**good, "parameters": {**good["parameters"], "power_limit_ma": 9999}}),
                       ("a bad colour", {**good, "palette": [{"position": 0, "hex": "red"}, {"position": 1, "hex": "#000000"}]}),
                       ("a 2D layout", {**good, "compatibility": {"topology": "matrix_2d"}})):
        try:
            ex.validate_scene(bad)
            refused = False
        except ValueError:
            refused = True
        check(f"a scene file with {label} is refused", refused)
    clamped = ex.validate_scene({**good, "parameters": {**good["parameters"], "motion_speed": 99, "brightness": -3}})
    check("numbers out of range are clamped, as everywhere in the hub",
          clamped["parameters"]["motion_speed"] == 2.0 and clamped["parameters"]["brightness"] == 0.0)
    check("a scene file cannot be NaN", _refuses(lambda: ex.validate_scene(
        {**good, "parameters": {**good["parameters"], "warp_strength": float("nan")}})))
    copy_ = ex.copy_scene(good, "My Aurora", set(ex.SCENES))
    check("a copy gets its own id and remembers the built-in it came from",
          copy_["id"] == "my_aurora" and copy_["based_on"] == "aurora")

    # Settings: migration and validation.
    folder = tempfile.mkdtemp(prefix="ambilight-exp-")
    path = os.path.join(folder, "config.json")
    old = {"profile": "pulse", "bass_bounce": 0.8, "bass_shake": 0.5, "saturation": 1.4,
           "user_profiles": {"Late night": {"saturation": 1.1, "blend": 0.3}}}
    with open(path, "w") as fh:
        json.dump(old, fh)
    config = Config(path)
    snap = config.snapshot()
    check("a config left on a removed look (Pulse) moves to Vivid whole, beat effects off",
          snap["profile"] == "vivid" and snap["bass_bounce"] == 0 and snap["bass_shake"] == 0)
    check("...with Follow the sound off, Calm off, and Room Studio idle",
          snap["audio_react"] == "off" and not snap["calm"]
          and snap["room_scene"] == "neon_coast" and snap["room_audio"] == "off")
    config.apply_profile("Late night")
    check("a look of your own still applies", config.get("profile") == "Late night")
    for raw in ("loud", "trippy:balanced", "[1,2]"):
        check(f"audio_react refuses {raw!r}", _refuses(lambda raw=raw: config.update({"audio_react": raw})))
    config.update({"room_custom": {"mine_a": {**good, "id": "mine_a", "name": "Mine"}, "aurora": good,
                                   "bad_one": {**good, "id": "bad_one", "renderer": "x"}}})
    check("stored scenes of your own validate; one claiming a built-in's id or broken is dropped",
          list(config.get("room_custom")) == ["mine_a"], list(config.get("room_custom")))
    reloaded = Config(path)
    check("...and they survive a restart", list(reloaded.get("room_custom")) == ["mine_a"])

    # Room mode never opens a screen capture; with Room audio on it opens only the sound.
    class Ears:
        name, hears, opened, sound = "ears", True, [], []

        @staticmethod
        def available():
            return True

        @staticmethod
        def display_count():
            return 1

        def open(self, monitor_index, band_height):
            Ears.opened.append("screen")
            return 600, 150

        def open_sound(self, monitor_index):
            Ears.sound.append(self.listener is not None)

        def grab(self):
            return np.full((150, 600, 3), 90, np.uint8)

        grab_full = grab

        def grab_search(self, rows):
            return self.grab()

        def set_inset(self, rows):
            pass

        def close(self):
            pass

    real = (eng.CAPTURES, eng.screen_locked)
    eng.CAPTURES, eng.screen_locked = {"ears": Ears}, (lambda: False)
    try:
        config = Config(os.path.join(tempfile.mkdtemp(prefix="ambilight-room-"), "config.json"))
        config.update({"wled_ip": "127.0.0.1", "wled_port": FAKE_WLED_UDP, "led_count": 60})
        engine = eng.Engine(config)
        engine.start("room")
        time.sleep(0.5)
        quiet = (list(Ears.opened), list(Ears.sound))
        status = engine.status()
        config.update({"room_audio": "balanced"})
        time.sleep(0.8)
        hearing = list(Ears.sound)
        config.update({"calm": True})
        time.sleep(0.8)
        calm_name = engine.capture_name
        frames = engine.output_fps
        engine.start("screen")
        time.sleep(0.5)
        switched = list(Ears.opened)
        engine.stop()
    finally:
        eng.CAPTURES, eng.screen_locked = real
    check("Room Studio runs with the screen never captured and, with its sound off, nothing heard",
          quiet == ([], []) and status["source"] == "room" and status["capture_plugin"] == "none", quiet)
    check("...its Audio Reactivity opens the sound alone, never the screen", hearing == [True], hearing)
    check("...Calm closes the sound again", calm_name == "none", calm_name)
    check("...it streams frames meanwhile", frames > 20, frames)
    check("switching to screen sync opens the screen capture then, and only then", switched == ["screen"], switched)


def test_trippy():
    print("\n35e. Trippy: a treatment on the screen's own colours, the sound optional")
    import ambilight.engine as eng
    from ambilight import beat, experience as ex
    from ambilight.config import DEFAULTS, PROFILES, _coerce
    from ambilight.plugins.capture_sck import downmix
    from ambilight.safety import STROBE_MAX
    cfg = {**DEFAULTS, **PROFILES["vivid"], "led_count": 240, "left_led": 0, "right_led": 239, "fade_leds": 0}
    target = np.random.default_rng(5).random((240, 3)).astype(np.float32)
    loud = {**beat.neutral_features(), "valid": True, "confidence": 1.0, "low_energy": 1.0, "mid_energy": 1.0,
            "high_energy": 1.0, "bass_envelope": 1.0, "onset_strength": 1.0}

    def run(settings, frames=240, target=target, beat_every=None, features=None):
        stage = eng.OutputStage()
        stage.meter = beat.BassMeter()
        if features is not None:
            stage.features = features
        out = []
        for i in range(frames):
            now = 1.0 + i / 60
            if beat_every and i % beat_every == 0:
                stage.meter.fire(now)
            stage.set_target(target, now)       # as the capture thread does, so the strip never goes stale
            out.append(stage.frame(now, {**cfg, **settings}).astype(np.float32))
        return out, stage

    def same(a, b):
        return all(np.array_equal(x, y) for x, y in zip(a, b))

    plain, _ = run({})
    check("Trippy off: its own settings change nothing, so Vivid stays exactly as it was",
          same(plain, run({"trippy_warp": 1.0, "trippy_speed": 2.0, "trippy_texture": 1.0})[0]))
    trippy, _ = run({"trippy": True})
    check("Trippy with the sound off moves on its own", np.abs(trippy[200] - trippy[60]).mean() > 2,
          round(float(np.abs(trippy[200] - trippy[60]).mean()), 2))
    check("...and looks unlike the plain look", np.abs(trippy[200] - plain[200]).mean() > 4,
          round(float(np.abs(trippy[200] - plain[200]).mean()), 2))
    check("...the same moment always gives the same frame: no random colours", same(trippy, run({"trippy": True})[0]))
    black = np.zeros((240, 3), np.float32)
    check("a black screen stays exactly as dark as without Trippy",
          same(run({}, frames=90, target=black)[0], run({"trippy": True}, frames=90, target=black)[0]))
    red = np.tile(np.float32([[0.9, 0.1, 0.05]]), (240, 1))
    reds = run({"trippy": True, "trippy_colour": 1.0}, target=red)[0][-1]
    lit = reds.sum(axis=1) > 0
    check("a red screen stays red on every LED", lit.any() and (reds[lit, 0] > reds[lit, 1:].max(axis=1)).all())
    mostly_blue = np.tile(np.float32([[0.05, 0.15, 0.9]]), (240, 1))
    mostly_blue[160:] = [0.9, 0.1, 0.05]
    out = run({"trippy": True, "trippy_warp": 1.0}, target=mostly_blue)[0][-1]
    blue = int((out[:, 2] > out[:, 0]).sum())
    check("a screen two thirds blue stays about two thirds blue: its dominant colour leads", abs(blue - 160) <= 24, blue)
    spot = np.zeros((240, 3), np.float32)
    spot[118:123] = 1.0
    reach = ex.TRIPPY_REACH * 239 + 3
    spread_ = [np.nonzero(f.sum(axis=1))[0] for f in run({"trippy": True, "trippy_warp": 1.0, "audio_react": "expressive"},
                                                         target=spot, features=lambda now: loud)[0][30:]]
    check("colours move at most the warp's bounded reach, even with the sound pushing it",
          all(len(i) and i.min() >= 120 - reach and i.max() <= 120 + reach for i in spread_),
          (min(i.min() for i in spread_), max(i.max() for i in spread_)))

    # The sound: an add-on through the shared feature frame, eased, gated and bounded.
    heard, _ = run({"trippy": True, "audio_react": "expressive"}, features=lambda now: loud)
    check("with the sound on, Trippy changes", np.abs(heard[200] - trippy[200]).mean() > 1)
    calm, _ = run({"trippy": True, "audio_react": "expressive", "calm": True}, features=lambda now: loud, beat_every=20)
    check("Calm: exactly Trippy without the sound, kicks and all", same(calm, trippy))
    beats_off, _ = run({"trippy": True}, beat_every=20)
    check("with Follow the sound off, a beat adds nothing", same(beats_off, trippy))
    # A dim screen, so the power limit does not scale the kick's light away.
    kicked, _ = run({"trippy": True, "audio_react": "expressive"}, target=target * 0.3, features=lambda now: loud,
                    beat_every=60)
    steady, _ = run({"trippy": True, "audio_react": "expressive"}, target=target * 0.3, features=lambda now: loud)
    check("a kick sends light through it", kicked[66].sum() > steady[66].sum() * 1.01,
          (float(kicked[66].sum()), float(steady[66].sum())))
    x = np.linspace(0, 1, 240)
    early, later = ex.impulse(x, 0.5, 0.1, 1.0), ex.impulse(x, 0.5, 0.3, 1.0)
    check("...that travels out from the middle and fades",
          np.argmax(later[120:]) > np.argmax(early[120:]) and later.max() < early.max()
          and not ex.impulse(x, 0.5, ex.IMPULSE_LIFE_S, 1.0).any())
    stage = ex.TrippyStage()
    trippy_cfg = {**cfg, "trippy": True, "audio_react": "expressive"}
    for i in range(60):
        stage.apply(target, i / 60, trippy_cfg, loud)
    pushed = max(stage.audio._values.values())
    for i in range(60, 360):
        stage.apply(target, i / 60, trippy_cfg, beat.neutral_features())
    check("when the sound goes stale, Trippy eases back to no sound at all",
          pushed > 0.1 and max(stage.audio._values.values()) < 0.01, (pushed, max(stage.audio._values.values())))
    sure, unsure = ex.AudioMod(), ex.AudioMod()
    for i in range(120):
        full = sure.update(loud, ex.TRIPPY_MAPPINGS, "expressive", i / 60, by_confidence=True)
        half = unsure.update({**loud, "confidence": 0.6}, ex.TRIPPY_MAPPINGS, "expressive", i / 60, by_confidence=True)
    check("confidence sets how strongly the sound applies",
          0 < half["warp_strength"] < full["warp_strength"] <= ex.TRIPPY_MAPPINGS[0]["depth"] + 1e-6, (half, full))
    check("Trippy alone captures no sound; Follow the sound does; Calm never",
          not eng.wants_sound({**cfg, "trippy": True}) and eng.wants_sound({**cfg, "trippy": True, "audio_react": "subtle"})
          and not eng.wants_sound({**cfg, "trippy": True, "audio_react": "subtle", "calm": True}))

    # One guard for everything: Trippy, the sound, the beat effects, beats three a second.
    every = {"trippy": True, "trippy_warp": 1.0, "trippy_texture": 1.0, "audio_react": "expressive",
             "bass_bounce": 1.0, "bass_shake": 1.0}
    flashes = []
    bright = np.tile(np.float32([[0.8, 0.7, 0.6]]), (240, 1))
    stage = eng.OutputStage()
    stage.meter = beat.BassMeter()
    stage.features = lambda now: loud
    for i in range(600):
        now = 1.0 + i / 60
        if i % 21 == 0:
            stage.meter.fire(now)
        stage.set_target(bright, now)
        stage.frame(now, {**cfg, **every})
        flashes.append(len(stage.guard.flashes))
    check("Trippy with every effect and the sound on: never more than three flashes a second reach the strip",
          max(flashes) <= STROBE_MAX + 1 and stage.guard.last_time is not None, max(flashes))
    room = eng.OutputStage()
    room.enter_room(0.0)
    plain_room = eng.OutputStage()
    plain_room.enter_room(0.0)
    check("Room Studio ignores Trippy: scenes stay their own",
          all(np.array_equal(room.frame(i / 60, {**cfg, "trippy": True}), plain_room.frame(i / 60, cfg)) for i in range(60)))

    # Settings, and the sound reader's mono fallback.
    check("Trippy is off unless asked for, and its settings are bounded",
          DEFAULTS["trippy"] is False and _coerce("trippy", "on") is True and _coerce("trippy_warp", 7) == 1.0
          and _coerce("trippy_speed", -1) == 0.0)
    left, right = np.float32([1, 2, 3]), np.float32([3, 4, 5])
    check("two channels one after another (ScreenCaptureKit's layout) mix down to mono",
          np.array_equal(downmix(np.concatenate([left, right]), 2, interleaved=False), np.float32([2, 3, 4])))
    check("...and so do interleaved ones; mono passes untouched",
          np.array_equal(downmix(np.float32([1, 3, 2, 4, 3, 5]), 2, interleaved=True), np.float32([2, 3, 4]))
          and downmix(left, 1, interleaved=False) is left)
    coast = [stop["hex"] for stop in ex.SCENES["neon_coast"]["palette"]]
    check("Neon Coast runs midnight indigo, electric cyan, neon pink and coral sunset, closing its own loop",
          coast[0] == coast[-1] and {"#140C3E", "#16DFF2", "#FF2E9E", "#FF7A52"} <= set(coast), coast)


def _refuses(run):
    try:
        run()
    except ValueError:
        return True
    return False


def test_room_api(base):
    print("\n48. Room Studio and the test beat over HTTP")

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=body, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    code, listing = call("/api/room/scenes")
    ids = [s["id"] for s in listing.get("scenes", [])]
    check("five scenes ship: Neon Coast, Aurora, Deep Ocean, Golden Hour, Hearth",
          code == 200 and ids == ["neon_coast", "aurora", "deep_ocean", "golden_hour", "hearth"], ids)
    check("an unknown scene is a 404", call("/api/room/start?scene=nope")[0] == 404)
    code, st = call("/api/room/start?scene=aurora")
    time.sleep(1.0)
    st = call("/api/status")[1]
    check("starting a scene streams it with no screen capture",
          code == 200 and st["running"] and st["source"] == "room" and st["room_scene"] == "aurora"
          and st["capture_plugin"] == "none" and st["output_fps"] > 20, (st["source"], st["capture_plugin"], st["output_fps"]))
    code, st = call("/api/beat/test")
    check("Test beat answers without opening any sound", code == 200 and not st["audio"]["wanted"], st["audio"])
    code, made = call("/api/room/duplicate?id=aurora&name=Night%20sky")
    check("a built-in scene can be copied as your own, and becomes active",
          code == 200 and made["scene"] == "night_sky" and made["room_scene"] == "night_sky")
    check("your scene's colours and motion can be edited",
          call("/api/room/edit?id=night_sky&palette=102040,40c0a0,8040c0&motion_speed=0.5")[0] == 200)
    st = call("/api/status")[1]
    edited = st["room_custom"]["night_sky"]
    check("...the palette closes its own loop, so the drift has no seam",
          [p["hex"] for p in edited["palette"]] == ["#102040", "#40C0A0", "#8040C0", "#102040"]
          and edited["parameters"]["motion_speed"] == 0.5, edited["palette"])
    check("built-in scenes cannot be edited", call("/api/room/edit?id=aurora&motion_speed=1")[0] == 400)
    check("an unknown setting is refused", call("/api/room/edit?id=night_sky&wled_ip=1.2.3.4")[0] == 400)
    call("/api/room/reset?id=night_sky")
    reset = call("/api/status")[1]["room_custom"]["night_sky"]
    check("Reset puts your copy back to the built-in it came from",
          reset["palette"] == listing["scenes"][1]["palette"] and reset["name"] == "Night sky")
    code, exported = call("/api/room/export?id=night_sky")
    check("a scene exports as its JSON", code == 200 and exported["id"] == "night_sky" and exported["schema_version"] == 1)
    code, imported = call("/api/room/import", json.dumps(exported).encode())
    check("an exported scene imports under a free id", code == 200 and imported["scene"] == "night_sky_2", imported.get("scene"))
    hostile = json.dumps({**exported, "wled_ip": "10.0.0.9"}).encode()
    check("a scene file carrying anything else is refused", call("/api/room/import", hostile)[0] == 400)
    check("deleting your scene works", call("/api/room/delete?id=night_sky_2")[0] == 200
          and "night_sky_2" not in call("/api/status")[1]["room_custom"])
    code, st = call("/api/sync/on")
    time.sleep(0.5)
    st = call("/api/status")[1]
    check("screen sync takes over from the room scene", st["source"] == "screen", st["source"])
    call("/api/sync/off")
    code, st = call("/api/room/stop")
    check("Stop in Room Studio leaves screen sync alone when the room is not running", code == 200)
    call("/api/room/delete?id=night_sky")


def main():
    print("=" * 62)
    print("PUDISYNC -- ACCEPTANCE TESTS")
    print("=" * 62)
    # First: its live child can only start a ScreenCaptureKit stream while
    # this process has not captured the screen yet (see test_sck_capture).
    test_sck_capture()
    test_packets()
    test_ddp_packets()
    test_mapping()
    test_fade()
    test_zones()
    test_serial()
    test_provision()
    test_letterbox()
    test_frame_layout()
    test_color_engine()
    test_smoothing()
    test_adaptive_pace()
    test_recorder()
    test_safety()
    test_run_record()
    test_config()
    test_access()
    test_board_answering()
    test_board_urls()
    test_pairing_qr()
    test_frame_wizard()
    test_firmware_download()
    test_quartz_capture()
    test_sync_intent()
    test_sync_routes()
    test_board_deploy()
    test_board_ui()
    test_phone_page_connection()
    test_fake_wled_presets()
    test_fake_wled_ddp()
    test_display_follow()
    test_camera_calibration_maths()
    test_fitted_matrix_in_use()
    test_calibration_routes()
    test_camera_calibration_flow()
    test_board_address_cache()
    test_camera_choice()
    test_mouse_sync()
    test_immersion()
    test_fire_glow()
    test_bass_beats()
    test_experience()
    test_trippy()
    test_user_profiles()
    test_look_notes()
    test_backup()
    test_privacy()
    test_sync_modes()
    test_vivid_mode()
    test_enriched()
    test_dark_scenes()
    test_adaptive_brightness()
    test_hue_balance()
    test_colour_locks()
    test_webcam_job()
    test_bed_strip()
    test_shared_supply()
    test_engine_parts()
    test_telemetry()
    test_engine_telemetry()
    test_protected_glow()
    test_one_interface()
    test_soak_verdict()
    test_soak_live()
    test_latency_script()
    test_sync_worktrees()
    with sandbox_hub() as base:
        test_telemetry_api(base)
        test_api(base)
        test_wizard(base)
        test_serial_api(base)
        test_provision_api(base)
        test_security_api(base)
        test_backup_api(base)
        test_room_api(base)
        test_look_notes_api(base)
        test_connect_api(base)
        test_page_in_browser(base)
        test_page_in_webkit(base)

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    FAILED: {f}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
