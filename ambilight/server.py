"""HTTP server. Every UI action is a plain GET URL so iOS Shortcuts can call it."""
import json
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import PROFILES
from .plugins.capture_mss import MSSCapture
from .plugins.output_serial import SerialOutput, list_ports, required_baud
from .provision import Provisioner, esptool_available, known_networks
from .plugins.output_wled_udp import WLEDUDPOutput

WEB_DIR = Path(__file__).resolve().parent / "web"


def lan_ip():
    """Our address on the LAN, found without sending anything."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))  # reserved, never routed
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "AmbilightHub"
    hub = None  # injected by serve()

    def log_message(self, fmt, *args):
        pass  # the hub prints what matters; access logs only add noise

    # --- plumbing ----------------------------------------------------------
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _authorized(self, query):
        pin = self.hub.cfg.get("pin")
        if not pin:
            return True
        given = query.get("pin", [""])[0] or self.headers.get("X-Ambilight-Pin", "")
        return given == pin

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            try:
                self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            except OSError as exc:
                self._json({"error": f"UI missing: {exc}"}, 500)
            return

        if not path.startswith("/api/"):
            self._json({"error": "not found"}, 404)
            return

        if not self._authorized(query):
            self._json({"error": "PIN required or incorrect"}, 403)
            return

        try:
            self._route(path, query)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # --- routes ------------------------------------------------------------
    def _route(self, path, query):
        hub = self.hub
        one = lambda key, default=None: query.get(key, [default])[0]

        if path == "/api/status":
            self._json(hub.status())

        elif path == "/api/sync/on":
            hub.wizard.stop()
            hub.engine.start()
            self._json(hub.status())
        elif path == "/api/sync/off":
            hub.engine.stop()
            self._json(hub.status())
        elif path == "/api/sync/toggle":
            (hub.engine.stop if hub.engine.running else hub.engine.start)()
            self._json(hub.status())

        elif path.startswith("/api/profile/"):
            name = path.rsplit("/", 1)[-1]
            if name not in PROFILES:
                self._json({"error": f"unknown profile {name}"}, 400)
                return
            hub.cfg.apply_profile(name)
            self._json(hub.status())

        elif path == "/api/set":
            changes = {k: v[0] for k, v in query.items() if k != "pin"}
            try:
                changed = hub.cfg.update(changes)
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"changed": changed, **hub.status()})

        elif path == "/api/config":
            self._json(hub.cfg.snapshot())

        # --- WLED passthrough
        elif path == "/api/wled/meta":
            self._json(hub.wled.meta())
        elif path == "/api/wled/power":
            self._json({"ok": hub.wled.set_power(one("on", "1") in ("1", "true", "on"))})
        elif path == "/api/wled/brightness":
            self._json({"ok": hub.wled.set_brightness(one("value", "128"))})
        elif path in ("/api/wled/preset", "/api/wled/effect", "/api/wled/palette"):
            # Realtime UDP overrides WLED's own rendering, so stop sync first.
            hub.engine.stop()
            hub.wizard.stop()
            if path.endswith("preset"):
                ok = hub.wled.set_preset(one("id", "1"))
            elif path.endswith("effect"):
                ok = hub.wled.set_effect(one("index", "0"))
            else:
                ok = hub.wled.set_palette(one("index", "0"))
            self._json({"ok": ok, **hub.status()})

        # --- wizard
        elif path == "/api/wizard/count/start":
            hub.engine.stop()
            reported = hub.wled.led_count() or hub.cfg.get("led_count")
            self._json(hub.wizard.count_start(reported))
        elif path == "/api/wizard/count/answer":
            self._json(hub.wizard.count_answer(one("lit", "0") in ("1", "true", "yes")))
        elif path == "/api/wizard/marker":
            hub.engine.stop()
            self._json(hub.wizard.marker(one("index", "0")))
        elif path == "/api/wizard/gradient":
            hub.engine.stop()
            self._json(hub.wizard.gradient())
        elif path == "/api/wizard/stop":
            self._json(hub.wizard.stop())

        elif path == "/api/serial/ports":
            cfg = hub.cfg.snapshot()
            self._json({
                "ports": list_ports(),
                "current": cfg["serial_port"],
                "output": cfg["output"],
                "baud": cfg["serial_baud"],
                "recommended_baud": required_baud(cfg["led_count"], cfg["fps"]),
                "pyserial": _pyserial_available(),
            })

        elif path == "/api/flash/check":
            self._json({
                "esptool": esptool_available(),
                "pyserial": _pyserial_available(),
                "ports": list_ports(),
            })
        elif path == "/api/flash/networks":
            self._json(known_networks())
        elif path == "/api/flash/start":
            port = one("port", "")
            ssid = one("ssid", "")
            if not port or not ssid:
                self._json({"error": "a port and a Wi-Fi name are required"}, 400)
                return
            try:
                hub.engine.stop()   # the cable cannot carry pixels and a flash at once
                hub.provisioner.start(port, ssid, one("password", ""),
                                      flash=one("flash", "0") == "1")
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
                return
            self._json(hub.provisioner.status())
        elif path == "/api/flash/status":
            self._json(hub.provisioner.status())
        elif path == "/api/flash/adopt":
            # Point the hub at the board that was just provisioned.
            st = hub.provisioner.status()
            if not st.get("ip"):
                self._json({"error": "no address to adopt yet"}, 409)
                return
            hub.cfg.update({"wled_ip": st["ip"], "output": "udp"})
            self._json(hub.status())

        elif path == "/api/diagnostics":
            self._json(hub.diagnostics())
        else:
            self._json({"error": "not found"}, 404)


class Hub:
    """Ties config, engine, WLED client and wizard together for the handler."""

    def __init__(self, config, engine, wled, wizard):
        self.cfg = config
        self.engine = engine
        self.wled = wled
        self.wizard = wizard
        self.provisioner = Provisioner()

    def status(self):
        snap = self.cfg.snapshot()
        snap.pop("pin", None)  # never echo the PIN back to a client
        state = self.engine.status()
        wled_state = self.wled.state() if not self.engine.running else None
        return {**snap, **state,
                "preview": self.engine.preview(),
                "wled_bri": (wled_state or {}).get("bri"),
                "wled_on": (wled_state or {}).get("on"),
                "has_pin": bool(self.cfg.get("pin"))}

    def diagnostics(self):
        cfg = self.cfg.snapshot()
        engine = self.engine
        plugins = [
            {"kind": "capture", "name": MSSCapture.name,
             "available": MSSCapture.available(),
             "active": engine.capture_name == MSSCapture.name},
            {"kind": "capture", "name": "macos-native",
             "available": False, "active": False},
            {"kind": "output", "name": WLEDUDPOutput.name,
             "available": WLEDUDPOutput.available(),
             "active": engine.output_name == WLEDUDPOutput.name},
            {"kind": "output", "name": SerialOutput.name,
             "available": SerialOutput.available(),
             "active": engine.output_name == SerialOutput.name},
        ]
        return {
            "running": engine.running,
            "fps": engine.fps,  # measured, not the target
            "last_error": engine.last_error or self.wled.last_error,
            "wled_reachable": self.wled.reachable,
            "wled_led_count": self.wled.led_count(),
            "capture_size": engine.capture_size,
            "left_led": cfg["left_led"],
            "right_led": cfg["right_led"],
            "reversed": cfg["left_led"] > cfg["right_led"],
            "plugins": plugins,
        }


def _pyserial_available():
    try:
        import serial  # noqa: F401
    except ImportError:
        return False
    return True


def serve(hub):
    Handler.hub = hub
    port = hub.cfg.get("http_port")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    return server
