"""HTTP server. Every UI action is a plain GET URL so iOS Shortcuts can call it."""
import json
import logging
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .access import PinGate, allowed_fetch_site, allowed_host
from .config import DEFAULTS, PROFILES
from .discovery import hub_urls
from .effects import catalogue as effect_catalogue, state_for
from .plugins import CAPTURES, OUTPUTS
from .plugins.output_serial import list_ports, required_baud
from .provision import Provisioner, esptool_available, known_networks
from .recorder import RECORDINGS_DIR, replay

WEB_DIR = Path(__file__).resolve().parent / "web"

log = logging.getLogger(__name__)

# Fixed map of servable files: nothing outside it can be requested.
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/calibrate": ("calibrate.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "AmbilightHub"
    hub = None  # injected by serve()
    gate = PinGate()
    timeout = 15    # a stalled client must not hold a handler thread forever

    def log_message(self, fmt, *args):
        pass  # the hub logs what matters; per-request access lines only add noise

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

    def pin_verdict(self, query):
        """The header is what the UI sends; ?pin= remains for Shortcuts."""
        given = self.headers.get("X-Ambilight-Pin") or query.get("pin", [""])[0]
        return self.gate.check(given, self.hub.cfg.get("pin"), self.client_address[0])

    def request_allowed(self):
        """Refuse other websites and DNS-rebinding names before doing anything."""
        if allowed_host(self.headers.get("Host", "")) and \
                allowed_fetch_site(self.headers.get("Sec-Fetch-Site")):
            return True
        self._json({"error": "request from another site was blocked"}, 403)
        return False

    def do_HEAD(self):
        # HEAD must never run an action; pages answer with headers only.
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if not self.request_allowed():
            return
        if path in PAGES:
            self.serve_page(path)
        else:
            self._json({"error": "HEAD is only for pages"}, 405)

    def do_GET(self):
        if not self.request_allowed():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)  # "new_pin=" clears

        if path in PAGES:
            self.serve_page(path)
            return
        if not path.startswith("/api/"):
            self._json({"error": "not found"}, 404)
            return

        verdict = self.pin_verdict(query)
        if verdict != "ok":
            message = ("too many wrong PINs from this device; try again in a minute"
                       if verdict == "locked" else "PIN required or incorrect")
            self._json({"error": message}, 429 if verdict == "locked" else 403)
            return

        try:
            self._route(path, query)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:
            log.exception("request %s failed", path)
            self._json({"error": "the hub hit an internal error; see its log"}, 500)

    def serve_page(self, path):
        name, ctype = PAGES[path]
        try:
            self._send(200, (WEB_DIR / name).read_bytes(), ctype)
        except OSError:
            log.exception("page %s is missing", name)
            self._json({"error": "this page is missing from the install"}, 500)

    # --- routes ------------------------------------------------------------
    def record_route(self, path, one):
        """Recordings: capture real scenes, replay them while tuning."""
        if path == "/api/record/start":
            seconds = max(1.0, min(120.0, float(one("seconds", "20"))))
            try:
                name = self.hub.engine.start_recording(seconds)
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
                return
            self._json({"recording": name, "seconds": seconds})
        elif path == "/api/record/replay":
            # Only a bare file name inside the recordings folder: no path tricks.
            name = Path(one("file", "")).name
            target = RECORDINGS_DIR / name
            if not name or not target.is_file():
                self._json({"error": f"no recording named {name!r}"}, 404)
                return
            self._json({"file": name, **replay(target, self.hub.cfg.snapshot())[1]})
        else:
            self._json({"error": "not found"}, 404)

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
            # "pin" in a query authenticates (Shortcuts); changing it is "new_pin".
            changes = {k: v[0] for k, v in query.items() if k not in ("pin", "new_pin")}
            if "new_pin" in query:
                changes["pin"] = query["new_pin"][0]
            changed = hub.cfg.update(changes)
            self._json({"changed": changed, **hub.status()})

        elif path == "/api/config":
            self._json(hub.public_config())
        elif path == "/api/defaults":
            self._json({k: v for k, v in DEFAULTS.items() if k != "pin"})
        elif path == "/api/monitors":
            self._json({"monitors": monitors()})

        elif path.startswith("/api/record/"):
            self.record_route(path, one)

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
            if port not in list_ports():
                self._json({"error": "that port is not a connected board"}, 400)
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

        elif path == "/api/effects":
            self._json({"effects": effect_catalogue()})
        elif path == "/api/effect/run":
            # An effect is rendered by the controller, so realtime data must stop
            # or it would overwrite every frame the effect draws.
            hub.engine.stop()
            hub.wizard.stop()
            colour = one("color")
            rgb = None
            if colour:
                text = colour.lstrip("#")
                if len(text) == 6:
                    try:
                        rgb = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
                    except ValueError:
                        rgb = None
            payload = state_for(one("fx", "9"), one("speed"), one("intensity"),
                                rgb, one("brightness"))
            self._json({"ok": hub.wled.apply(payload), **hub.status()})

        elif path == "/api/diagnostics":
            self._json(hub.diagnostics())
        else:
            self._json({"error": "not found"}, 404)


class Hub:
    """Ties config, engine, WLED client and wizard together for the handler."""

    WLED_TTL_S = 2.0        # many devices polling must not mean many WLED requests
    WLED_BACKOFF_S = 6.0    # an unreachable controller is not asked again for a while

    def __init__(self, config, engine, wled, wizard, port=None):
        self.cfg = config
        self.engine = engine
        self.wled = wled
        self.wizard = wizard
        self.port = port or config.get("http_port")
        self.provisioner = Provisioner()
        self.wled_lock = threading.Lock()
        self.wled_cached = None
        self.wled_cached_at = -1e9

    def wled_state(self):
        """WLED's state, fetched at most every few seconds across all clients."""
        with self.wled_lock:
            ttl = self.WLED_TTL_S if self.wled.reachable else self.WLED_BACKOFF_S
            if time.monotonic() - self.wled_cached_at > ttl:
                self.wled_cached = self.wled.state()
                self.wled_cached_at = time.monotonic()
            return self.wled_cached or {}

    def public_config(self):
        snap = self.cfg.snapshot()
        snap.pop("pin", None)  # never echo the PIN back to a client
        snap["has_pin"] = bool(self.cfg.get("pin"))
        return snap

    def status(self):
        state = self.engine.status()
        wled_state = {} if self.engine.running else self.wled_state()
        if self.engine.running:
            mode = "sync"
        else:
            mode = "effect" if wled_state.get("on") else "off"
        return {**self.public_config(), **state,
                "preview": self.engine.preview(),
                "wled_bri": wled_state.get("bri"),
                "wled_on": wled_state.get("on"),
                "wled_reachable": self.wled.reachable or self.engine.running,
                "mode": mode,
                "urls": hub_urls(self.port)}

    def diagnostics(self):
        cfg = self.cfg.snapshot()
        engine = self.engine
        plugins = [{"kind": kind, "key": key, "name": plugin.name,
                    "available": plugin.available(),
                    "active": plugin.name in (engine.capture_name, engine.output_name)}
                   for kind, registry in (("capture", CAPTURES), ("output", OUTPUTS))
                   for key, plugin in registry.items()]
        return {
            "running": engine.running,
            "fps": engine.fps,  # measured, not the target
            "output_fps": engine.output_fps,
            "last_error": engine.last_error or self.wled.last_error,
            "wled_reachable": self.wled.reachable,
            "wled_led_count": self.wled.led_count(),
            "capture_size": engine.capture_size,
            "layout": cfg["layout"],
            "safety": engine.stage.safety(),
            "plugins": plugins,
        }


def monitors():
    """Displays that can be captured, numbered as monitor_index expects."""
    import mss

    with mss.mss() as shots:
        return [{"index": i, "width": m["width"], "height": m["height"], "primary": i == 1}
                for i, m in enumerate(shots.monitors) if i > 0]


def _pyserial_available():
    try:
        import serial  # noqa: F401
    except ImportError:
        return False
    return True


class HubServer(ThreadingHTTPServer):
    """Answers on IPv4 and IPv6 alike, like http.server's own DualStackServer."""

    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def serve(hub, port):
    Handler.hub = hub
    try:
        return HubServer(("::", port), Handler)
    except OSError as exc:
        if exc.errno in (98, 48):   # port in use: say so, do not hide it behind IPv4
            raise
        log.warning("IPv6 unavailable (%s); serving IPv4 only", exc)
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        server.daemon_threads = True
        return server
