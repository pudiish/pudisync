#!/usr/bin/env python3
"""Fake WLED: UDP realtime listener + JSON API stub. No hardware needed.

    python fake_wled.py            # listen on 21324 (UDP) and 8081 (HTTP)
    python fake_wled.py --quiet    # stats only, no per-packet lines
"""
import argparse
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EFFECTS = ["Solid", "Blink", "Breathe", "Wipe", "Random Colors", "Rainbow", "Fire 2012"]
PALETTES = ["Default", "Random Cycle", "Party", "Cloud", "Lava", "Ocean", "Forest"]

STATE = {
    "on": True, "bri": 128, "ps": -1,
    "seg": [{"id": 0, "start": 0, "stop": 240, "on": True, "bri": 255, "fx": 0, "pal": 0}],
}
INFO = {
    "ver": "0.15.0-fake", "name": "FakeWLED",
    "leds": {"count": 240, "fps": 0, "maxpwr": 0},
    "fxcount": len(EFFECTS), "palcount": len(PALETTES), "live": False,
}
PRESETS = {
    "0": {},
    "1": {"n": "Warm White", "on": True, "bri": 200},
    "2": {"n": "Movie Dim", "on": True, "bri": 60},
    "3": {"n": "Rainbow Flow", "on": True, "bri": 180},
}

stats = {"packets": 0, "leds": 0, "last": 0.0, "timeouts": set(), "starts": []}
_lock = threading.Lock()


def udp_listener(port, quiet):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    print(f"[udp ] listening on 0.0.0.0:{port}")
    while True:
        data, addr = sock.recvfrom(65535)
        if len(data) < 4:
            print(f"[udp ] SHORT packet ({len(data)}B) from {addr[0]}")
            continue
        proto, timeout, hi, lo = data[0], data[1], data[2], data[3]
        start = (hi << 8) | lo
        body = len(data) - 4
        leds = body // 3
        with _lock:
            stats["packets"] += 1
            stats["leds"] += leds
            stats["last"] = time.time()
            stats["timeouts"].add(timeout)
            stats["starts"].append(start)
            n = stats["packets"]
        name = {1: "WARLS", 2: "DRGB", 3: "DRGBW", 4: "DNRGB"}.get(proto, f"?{proto}")
        flags = []
        if proto != 4:
            flags.append("NOT-DNRGB")
        if body % 3:
            flags.append(f"RAGGED({body}B)")
        if leds > 489:
            flags.append(f"OVER-489({leds})")
        if not quiet:
            first = tuple(data[4:7]) if body >= 3 else ()
            print(f"[udp ] #{n:<5} {name} timeout={timeout:<3} start={start:<4} "
                  f"leds={leds:<4} first={first} {' '.join(flags)}")
        elif flags:
            print(f"[udp ] #{n} VIOLATION {' '.join(flags)}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        routes = {
            "/json": {"state": STATE, "info": INFO, "effects": EFFECTS, "palettes": PALETTES},
            "/json/state": STATE, "/json/info": INFO,
            "/json/eff": EFFECTS, "/json/pal": PALETTES,
            "/json/si": {"state": STATE, "info": INFO},
            "/presets.json": PRESETS,
        }
        if path in routes:
            print(f"[http] GET {path}")
            self._json(routes[path])
        elif path == "/stats":
            with _lock:
                self._json({"packets": stats["packets"], "leds": stats["leds"],
                            "timeouts": sorted(stats["timeouts"]),
                            "distinct_starts": sorted(set(stats["starts"])),
                            "idle_s": round(time.time() - stats["last"], 2) if stats["last"] else None})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            patch = json.loads(raw)
        except ValueError:
            self.send_error(400)
            return
        print(f"[http] POST {self.path} {json.dumps(patch)}")
        for key, value in patch.items():
            if key == "seg" and isinstance(value, list):
                for seg in value:
                    STATE["seg"][0].update({k: v for k, v in seg.items() if k != "id"})
            else:
                STATE[key] = value
        self._json({"success": True})


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp-port", type=int, default=21324)
    ap.add_argument("--http-port", type=int, default=8081)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    threading.Thread(target=udp_listener, args=(args.udp_port, args.quiet), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.http_port), Handler)
    print(f"[http] serving WLED JSON stub on 0.0.0.0:{args.http_port}")
    print(f"[http] packet stats at http://127.0.0.1:{args.http_port}/stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with _lock:
            print(f"\n[stat] {stats['packets']} packets, {stats['leds']} LED writes, "
                  f"timeouts seen: {sorted(stats['timeouts'])}")


if __name__ == "__main__":
    main()
