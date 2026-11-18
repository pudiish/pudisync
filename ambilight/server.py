"""HTTP server. Every UI action is a plain GET URL so iOS Shortcuts can call it."""
import gzip
import json
import logging
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import hostcache, routes, version
from .access import PinGate, allowed_fetch_site, allowed_host
from .backup import Pending
from .bed import Keeper
from .camrun import CameraJob
from .discovery import board_urls, hub_urls
from .intent import SyncIntent
from .plugins import CAPTURES, OUTPUTS
from .provision import Provisioner

WEB_DIR = Path(__file__).resolve().parent / "web"

log = logging.getLogger(__name__)

# Fixed map of servable files: nothing outside it can be requested.
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/expert.js": ("expert.js", "text/javascript; charset=utf-8"),
    "/calibrate": ("calibrate.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    # iOS ignores SVG Home Screen icons; without a PNG it shows a screenshot.
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}


_PACKED = {}


def packed(name, body):
    """`body` gzipped, compressed once per version of each file; only the newest version is kept."""
    version = hash(body)
    cached = _PACKED.get(name)
    if cached is None or cached[0] != version:
        cached = _PACKED[name] = (version, gzip.compress(body, mtime=0))
    return cached[1]


class Handler(BaseHTTPRequestHandler):
    server_version = "AmbilightHub"
    hub = None  # injected by serve()
    gate = PinGate()
    timeout = 15    # a stalled client must not hold a handler thread forever

    def log_message(self, fmt, *args):
        pass  # the hub logs what matters; per-request access lines only add noise

    # --- plumbing ----------------------------------------------------------
    def _send(self, code, body, ctype, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for name, value in headers:
            self.send_header(name, value)
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
        return self.gate.check(given, self.hub.cfg.get("pin"), self.client_address[0],
                               unprotected=self.hub.cfg.get("unprotected"))

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
        if self.pin_refused(query):
            return
        self.run(path, lambda: routes.handle(self.hub, path, query))

    def do_POST(self):
        """Only for routes that take a file. JSON alone: a browser must ask before
        sending it cross-site, which the GET routes cannot make it do."""
        # One POST per connection: a refused request's unread body must never be
        # taken for the next request on the same socket.
        self.close_connection = True
        if not self.request_allowed():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if path not in routes.POST_ROUTES:
            self._json({"error": "not found"}, 404)
            return
        if self.pin_refused(query):
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._json({"error": "send the file as application/json"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json({"error": "the request needs a Content-Length"}, 411)
            return
        if not 0 <= length <= routes.MAX_BODY:
            self._json({"error": f"the file is over {routes.MAX_BODY // 1024} kB"}, 413)
            return
        body = self.rfile.read(length)
        self.run(path, lambda: routes.handle_post(self.hub, path, query, body))

    def pin_refused(self, query):
        """Answers the request itself, and returns True, when the PIN does not let it through."""
        verdict = self.pin_verdict(query)
        if verdict == "ok":
            return False
        message = {"locked": "too many wrong PINs from this device; try again in a minute",
                   "unconfigured": "protected mode is on but no PIN is set; fix it in config.json",
                   }.get(verdict, "PIN required or incorrect")
        self._json({"error": message}, 429 if verdict == "locked" else 403)
        return True

    def run(self, path, handler):
        try:
            payload, code = handler()
            self._json(payload, code)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception:
            log.exception("request %s failed", path)
            self._json({"error": "the hub hit an internal error; see its log"}, 500)

    def serve_page(self, path):
        name, ctype = PAGES[path]
        try:
            body = (WEB_DIR / name).read_bytes()
        except OSError:
            log.exception("page %s is missing", name)
            self._json({"error": "this page is missing from the install"}, 500)
            return
        # A phone on weak Wi-Fi loads the page in a third of the bytes.
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            self._send(200, packed(name, body), ctype, [("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding")])
        else:
            self._send(200, body, ctype, [("Vary", "Accept-Encoding")])


def board_answering(reachable, running, output_error):
    """Whether the LED board is answering, as the clients are told.

    While syncing, the stream's own sends are the freshest evidence, so a
    failed send means the board is gone even if its last JSON reply was fine.
    Otherwise the last JSON API call decides.
    """
    if running:
        return output_error is None
    return reachable


def board_look(state):
    """What the board's main segment shows: its effect id and first colour as hex."""
    seg = (state.get("seg") or [{}])[0]
    colour = (seg.get("col") or [[]])[0][:3]
    return {"wled_fx": seg.get("fx"),
            "wled_col": "".join(f"{int(c):02x}" for c in colour) if len(colour) == 3 else None}


class Hub:
    """Ties config, engine, WLED client and wizard together for the handler."""

    WLED_TTL_S = 2.0        # many devices polling must not mean many WLED requests
    WLED_BACKOFF_S = 6.0    # an unreachable controller is not asked again for a while

    def __init__(self, config, engine, wled, wizard, port=None):
        self.cfg = config
        self.engine = engine
        self.wled = wled
        self.wizard = wizard
        self.intent = SyncIntent(config, wled, engine)
        self.bed = Keeper(config, wled, self.intent)
        self.intent.watchers.append(self.bed.tick)
        self.port = port or config.get("http_port")
        self.provisioner = Provisioner()
        self.camera = CameraJob(self.port)
        self.pending_import = Pending()
        self.wled_lock = threading.Lock()
        self.wled_cached = None
        self.wled_cached_at = -1e9
        self.wled_cached_writes = -1

    def wled_state(self):
        """WLED's state, fetched at most every few seconds across all clients, and after every write."""
        with self.wled_lock:
            ttl = self.WLED_TTL_S if self.wled.reachable else self.WLED_BACKOFF_S
            writes = self.wled.writes
            if time.monotonic() - self.wled_cached_at > ttl or writes != self.wled_cached_writes:
                self.wled_cached = self.wled.state()
                self.wled_cached_at = time.monotonic()
                self.wled_cached_writes = writes
            return self.wled_cached or {}

    def public_config(self):
        snap = self.cfg.snapshot()
        snap.pop("pin", None)  # never echo the PIN back to a client
        snap["has_pin"] = bool(self.cfg.get("pin"))
        return snap

    def status(self):
        state = self.engine.status()
        # While streaming, the sync watcher's last look stands in for a request
        # of our own: the board is busy enough. A held-off watcher looks no more.
        watched = self.engine.running and not self.intent.paused()
        wled_state = (self.intent.last_state or {}) if watched else self.wled_state()
        if self.engine.running:
            mode = "sync"
        else:
            mode = "effect" if wled_state.get("on") else "off"
        return {**self.public_config(), **state,
                "preview": self.engine.preview(),
                "wled_bri": wled_state.get("bri"),
                "wled_on": wled_state.get("on"),
                **board_look(wled_state),
                "wled_reachable": board_answering(self.wled.reachable, self.engine.running,
                                                  self.engine.errors["output"]),
                "mode": mode,
                "version": version.build_id(),
                "urls": hub_urls(self.port),
                "board_urls": self.board_urls()}

    def board_urls(self):
        host = self.cfg.get("wled_ip")
        return board_urls(host, self.cfg.get("wled_http_port"), hostcache.BOARD.last_known(host))

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
            "telemetry": engine.telemetry.snapshot(),
        }


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
