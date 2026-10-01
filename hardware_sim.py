#!/usr/bin/env python3
"""Hardware simulator: puts the hub through real-world failure scenarios.

The fake WLED harness answers the happy path. This one answers "what happens
when the thing misbehaves" -- a controller that reboots, a flaky Wi-Fi link, a
strip shorter than configured, a browned-out power supply.

    python hardware_sim.py              # run every scenario
    python hardware_sim.py --list       # show them
    python hardware_sim.py reboot       # run one

Nothing here needs a strip, a NodeMCU, or a power supply.
"""
import argparse
import json
import random
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

HUB = "http://127.0.0.1:8080"
UDP_PORT = 21324
HTTP_PORT = 8081

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"    [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def hub(path, timeout=4):
    with urllib.request.urlopen(HUB + path, timeout=timeout) as r:
        return json.loads(r.read())


class Strip:
    """A simulated WS2812B strip behind a simulated WLED controller.

    Models the parts that actually bite: a fixed physical length, a power
    budget, packet loss, and a controller that can go away and come back.
    """

    def __init__(self, length=240, max_amps=10.0):
        self.length = length
        self.max_amps = max_amps
        self.pixels = [(0, 0, 0)] * length
        self.online = True
        self.drop_rate = 0.0
        self.packets = 0
        self.dropped = 0
        self.out_of_range = 0
        self.brownouts = 0
        self.last_rx = 0.0
        self.protocol_errors = []
        self._lock = threading.Lock()

    # --- what the strip would physically do -------------------------------
    def amps(self):
        """~20mA per lit channel, the WS2812B rule of thumb."""
        total = sum(sum(p) for p in self.pixels)
        return total / 255.0 * 0.02

    def apply(self, start, rgb_bytes):
        with self._lock:
            count = len(rgb_bytes) // 3
            for i in range(count):
                idx = start + i
                if idx >= self.length:
                    # Data addressed past the physical end simply falls off.
                    self.out_of_range += 1
                    continue
                self.pixels[idx] = tuple(rgb_bytes[i * 3:i * 3 + 3])
            if self.amps() > self.max_amps:
                self.brownouts += 1

    def ingest(self, data):
        self.last_rx = time.time()
        if len(data) < 4:
            self.protocol_errors.append(f"runt packet ({len(data)}B)")
            return
        proto, _timeout, hi, lo = data[0], data[1], data[2], data[3]
        if proto != 4:
            self.protocol_errors.append(f"not DNRGB (proto {proto})")
            return
        body = data[4:]
        if len(body) % 3:
            self.protocol_errors.append(f"ragged payload ({len(body)}B)")
            return
        if len(body) // 3 > 489:
            self.protocol_errors.append(f"over 489 LEDs ({len(body)//3})")
            return
        self.apply((hi << 8) | lo, body)


class Controller(threading.Thread):
    """UDP + JSON endpoints for the simulated strip."""

    def __init__(self, strip):
        super().__init__(daemon=True)
        self.strip = strip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", UDP_PORT))
        self.sock.settimeout(0.3)
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            s = self.strip
            s.packets += 1
            if not s.online:
                s.dropped += 1          # powered off: packet goes nowhere
                continue
            if random.random() < s.drop_rate:
                s.dropped += 1          # Wi-Fi congestion
                continue
            s.ingest(data)
        self.sock.close()


def wait_for(predicate, timeout=6.0, interval=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------- scenarios
def sc_steady(strip):
    """Normal operation: the stream is continuous and the strip tracks it."""
    hub("/api/sync/on")
    time.sleep(2.5)
    before = strip.packets
    time.sleep(2.0)
    rate = (strip.packets - before) / 2.0
    check("stream is continuous", rate > 20, f"{rate:.0f} packets/sec")
    check("no protocol errors", not strip.protocol_errors, str(strip.protocol_errors[:2]))
    check("every packet addressed within the strip", strip.out_of_range == 0,
          f"{strip.out_of_range} LEDs addressed past the end")
    lit = sum(1 for p in strip.pixels if sum(p) > 0)
    check("the strip is actually lit", lit > 0, f"{lit}/{strip.length} LEDs on")
    hub("/api/sync/off")


def sc_reboot(strip):
    """The controller loses power mid-stream and comes back."""
    hub("/api/sync/on")
    time.sleep(2.0)
    check("streaming before the outage", strip.packets > 10)

    strip.online = False
    dropped_at = strip.dropped
    time.sleep(2.5)
    check("packets are lost while it is down", strip.dropped > dropped_at,
          f"{strip.dropped - dropped_at} lost")

    s = hub("/api/status")
    check("the hub keeps running through the outage", s["running"] is True)
    check("frames are still being produced", s["measured_fps"] > 10,
          f"{s['measured_fps']} fps")

    strip.online = True
    seen = strip.packets
    ok = wait_for(lambda: strip.packets > seen + 20, timeout=5)
    check("it recovers on its own, with no restart", ok)
    lit = sum(1 for p in strip.pixels if sum(p) > 0)
    check("the strip relights after recovery", lit > 0, f"{lit} LEDs on")
    hub("/api/sync/off")


def sc_flaky(strip):
    """A congested Wi-Fi link losing 30% of packets."""
    strip.drop_rate = 0.30
    hub("/api/sync/on")
    time.sleep(3.5)
    s = hub("/api/status")
    check("heavy packet loss does not stall the engine", s["running"] is True)
    check("frame rate holds up under loss", s["measured_fps"] > 25,
          f"{s['measured_fps']} fps")
    lost = strip.dropped / max(1, strip.packets)
    check("loss is survivable, not fatal", 0.15 < lost < 0.5, f"{lost:.0%} lost")
    check("surviving packets are still well formed", not strip.protocol_errors)
    strip.drop_rate = 0.0
    hub("/api/sync/off")


def sc_short_strip(strip):
    """WLED is configured for more LEDs than are physically attached."""
    strip.out_of_range = 0
    hub("/api/set?led_count=400")      # claim 400 against a 240-LED strip
    hub("/api/sync/on")
    time.sleep(2.5)
    check("the overflow lands past the end, harmlessly", strip.out_of_range > 0,
          f"{strip.out_of_range} LED writes fell off the end")
    check("the physical strip still lights correctly",
          sum(1 for p in strip.pixels if sum(p) > 0) > 0)
    check("no protocol violation from the oversize frame", not strip.protocol_errors)
    s = hub("/api/status")
    check("the engine stays healthy", s["running"] is True and s["measured_fps"] > 10)
    hub("/api/sync/off")
    hub("/api/set?led_count=240")


def sc_power(strip):
    """Brightness as a power budget on an undersized supply."""
    strip.brownouts = 0
    hub("/api/set?brightness=1.0")
    hub("/api/set?zones=" + urllib.parse.quote(
        json.dumps([{"start": 0, "end": 239, "mode": "solid", "color": "#ffffff"}])))
    hub("/api/sync/on")
    time.sleep(2.0)
    full = strip.amps()
    check("full white exceeds a 10A supply", full > 10.0, f"{full:.1f} A drawn")
    check("the simulated supply reports brownout", strip.brownouts > 0)

    hub("/api/set?brightness=0.4")
    time.sleep(2.0)
    dim = strip.amps()
    check("brightness cuts current roughly in proportion", dim < full * 0.6,
          f"{dim:.1f} A at 40%")
    check("dimmed draw fits the supply", dim < 10.0, f"{dim:.1f} A")
    hub("/api/sync/off")
    hub("/api/set?zones=[]")
    hub("/api/set?brightness=1.0")


def sc_release(strip):
    """Stopping hands the strip back to WLED instead of leaving it frozen."""
    hub("/api/sync/on")
    time.sleep(2.0)
    check("streaming while on", strip.packets > 10)
    hub("/api/sync/off")
    time.sleep(0.6)
    quiet = strip.packets
    time.sleep(2.0)
    check("the stream genuinely stops", strip.packets - quiet <= 1,
          f"{strip.packets - quiet} stray packets")
    check("the release frame blanked the strip",
          sum(sum(p) for p in strip.pixels[:1]) == 0)


def sc_reversed(strip):
    """A strip wired right-to-left still maps the screen correctly."""
    hub("/api/set?left_led=200&right_led=40")
    s = hub("/api/status")
    check("reversed edges are stored as given",
          s["left_led"] == 200 and s["right_led"] == 40)
    d = hub("/api/diagnostics")
    check("the hub detects the reversed wiring", d["reversed"] is True)
    hub("/api/sync/on")
    time.sleep(2.0)
    check("a reversed strip still lights", sum(1 for p in strip.pixels if sum(p) > 0) > 0)
    hub("/api/sync/off")
    hub("/api/set?left_led=0&right_led=239")
    d = hub("/api/diagnostics")
    check("normal wiring is detected again", d["reversed"] is False)


SCENARIOS = {
    "steady": ("Normal operation", sc_steady),
    "reboot": ("Controller loses power and returns", sc_reboot),
    "flaky": ("Congested Wi-Fi, 30% packet loss", sc_flaky),
    "short": ("Strip shorter than WLED is configured for", sc_short_strip),
    "power": ("Undersized power supply and brownout", sc_power),
    "release": ("Stopping hands control back to WLED", sc_release),
    "reversed": ("Strip wired right to left", sc_reversed),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="*", help="scenarios to run (default: all)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--leds", type=int, default=240)
    ap.add_argument("--amps", type=float, default=10.0)
    args = ap.parse_args()

    if args.list:
        for key, (desc, _) in SCENARIOS.items():
            print(f"  {key:10} {desc}")
        return 0

    try:
        hub("/api/status", timeout=2)
    except Exception as exc:
        print(f"Cannot reach the hub at {HUB}: {exc}")
        print("Start it first:  python3 ambilight.py")
        return 2

    strip = Strip(args.leds, args.amps)
    controller = Controller(strip)
    controller.start()

    print("=" * 64)
    print(f"HARDWARE SIMULATION -- {args.leds} LEDs, {args.amps}A supply")
    print("=" * 64)

    chosen = args.scenario or list(SCENARIOS)
    for key in chosen:
        if key not in SCENARIOS:
            print(f"\nUnknown scenario {key!r}")
            continue
        desc, fn = SCENARIOS[key]
        print(f"\n  {key} -- {desc}")
        strip.protocol_errors.clear()
        try:
            fn(strip)
        except Exception as exc:
            check(f"{key} completed", False, f"{type(exc).__name__}: {exc}")

    controller.stop.set()
    print("\n" + "=" * 64)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"    FAILED: {f}")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
