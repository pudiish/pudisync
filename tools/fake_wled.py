#!/usr/bin/env python3
"""Fake WLED: UDP realtime (DNRGB) and DDP listeners + JSON API stub. No hardware needed.

    python tools/fake_wled.py            # 21324 (UDP realtime), 4048 (DDP), 8081 (HTTP)
    python tools/fake_wled.py --quiet    # stats only, no per-packet lines

DDP packets are checked against what WLED 16.0.1's receiver accepts
(wled00/e131.cpp, ESPAsyncE131.h); anything it would drop or misread is
counted as a violation in /stats.
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

stats = {"packets": 0, "leds": 0, "bytes": 0, "last": 0.0, "timeouts": set(), "starts": []}
ddp_stats = {"packets": 0, "pushes": 0, "leds": 0, "bytes": 0, "violations": []}
_lock = threading.Lock()

DDP_HEADER_LEN = 10
DDP_MAX_DATA = 1440     # WLED's DDP_CHANNELS_PER_PACKET: 480 RGB LEDs


def parse_ddp(data):
    """One DDP packet -> its fields, plus what WLED would drop or misread in it."""
    if len(data) < DDP_HEADER_LEN:
        return {"violations": [f"SHORT({len(data)}B)"]}
    flags, seq, dtype, dest = data[0], data[1] & 0x0F, data[2], data[3]
    offset = int.from_bytes(data[4:8], "big")
    length = int.from_bytes(data[8:10], "big")
    body = len(data) - DDP_HEADER_LEN
    problems = []
    if flags & 0xC0 != 0x40:
        problems.append(f"NOT-V1(0x{flags:02x})")
    if flags & 0x0E:
        problems.append("QUERY/REPLY/STORAGE")    # WLED drops these
    if flags & 0x10:
        problems.append("TIMECODE")               # WLED would skip 4 data bytes
    if dtype != 0x0B:
        problems.append(f"TYPE(0x{dtype:02x})")
    if dest != 1:
        problems.append(f"DEST({dest})")
    if offset % 3:
        problems.append(f"UNALIGNED({offset})")
    if length != body:
        problems.append(f"LENGTH({length}!={body})")
    if length % 3:
        problems.append(f"RAGGED({length}B)")
    if length > DDP_MAX_DATA:
        problems.append(f"OVER-1440({length})")
    return {"push": bool(flags & 0x01), "seq": seq, "offset": offset,
            "leds": length // 3, "violations": problems}


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
            stats["bytes"] += len(data)
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


def ddp_listener(port, quiet):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    print(f"[ddp ] listening on 0.0.0.0:{port}")
    pushed_seq = None
    while True:
        data = sock.recvfrom(65535)[0]
        packet = parse_ddp(data)
        problems = packet["violations"]
        # A packet still carrying a frame's sequence after that frame was pushed
        # means push was not on the frame's last packet.
        if packet.get("seq") and packet["seq"] == pushed_seq:
            problems.append("AFTER-PUSH")
        if packet.get("push"):
            pushed_seq = packet["seq"]
        with _lock:
            ddp_stats["packets"] += 1
            ddp_stats["pushes"] += int(packet.get("push", False))
            ddp_stats["leds"] += packet.get("leds", 0)
            ddp_stats["bytes"] += len(data)
            ddp_stats["violations"].extend(problems)
            stats["last"] = time.time()
            n = ddp_stats["packets"]
        if not quiet:
            print(f"[ddp ] #{n:<5} seq={packet.get('seq')} offset={packet.get('offset')} "
                  f"leds={packet.get('leds')} push={packet.get('push')} {' '.join(problems)}")
        elif problems:
            print(f"[ddp ] #{n} VIOLATION {' '.join(problems)}")


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
                            "bytes": stats["bytes"],
                            "timeouts": sorted(stats["timeouts"]),
                            "distinct_starts": sorted(set(stats["starts"])),
                            "idle_s": round(time.time() - stats["last"], 2) if stats["last"] else None,
                            "ddp": {**ddp_stats, "violations": ddp_stats["violations"][:20]}})
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
        # Like the real board: any change that is not itself loading a preset
        # clears the active preset ("ps" goes back to -1).
        if "ps" not in patch:
            STATE["ps"] = -1
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
    ap.add_argument("--ddp-port", type=int, default=4048)
    ap.add_argument("--http-port", type=int, default=8081)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    threading.Thread(target=udp_listener, args=(args.udp_port, args.quiet), daemon=True).start()
    threading.Thread(target=ddp_listener, args=(args.ddp_port, args.quiet), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", args.http_port), Handler)
    print(f"[http] serving WLED JSON stub on 0.0.0.0:{args.http_port}")
    print(f"[http] packet stats at http://127.0.0.1:{args.http_port}/stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with _lock:
            print(f"\n[stat] {stats['packets']} packets, {stats['leds']} LED writes, "
                  f"timeouts seen: {sorted(stats['timeouts'])}; DDP: {ddp_stats['packets']} packets, "
                  f"{ddp_stats['pushes']} frames, {len(ddp_stats['violations'])} violations")


if __name__ == "__main__":
    main()
