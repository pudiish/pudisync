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
import urllib.request

import numpy as np

sys.path.insert(0, ".")
from ambilight import processing
from ambilight.plugins.output_wled_udp import MAX_LEDS_PER_PACKET, WLEDUDPOutput

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


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
    check("normal: screen-left maps to left_led (red)", tuple(z[0].astype(int)) == (255, 0, 0))
    check("normal: screen-right maps to right_led (blue)", tuple(z[-1].astype(int)) == (0, 0, 255))

    r = processing.build_mapping(240, 110, 10, W)
    zr = processing.zone_colors(band, r)
    # Strip order runs lo..hi; with left_led=110 the strip START (LED 10) is screen RIGHT.
    check("reversed: LED 10 shows screen-right (blue)", tuple(zr[0].astype(int)) == (0, 0, 255))
    check("reversed: LED 110 shows screen-left (red)", tuple(zr[-1].astype(int)) == (255, 0, 0))
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
    try:
        urllib.request.urlopen(base + "/api/status", timeout=2)
    except Exception as exc:
        print(f"\n  (skipping HTTP tests -- hub not running at {base}: {exc})")
    else:
        test_api(base)
        test_wizard(base)
        test_serial_api(base)

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    FAILED: {f}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
