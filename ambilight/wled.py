"""WLED JSON API client. Field names verified against kno.wled.ge/interfaces/json-api.

Verified: /json/state (on, bri, ps, seg[].fx, seg[].pal), /json/info (leds.count,
fxcount), /json/eff, /json/pal.
NOT in the documented JSON API: /presets.json. WLED serves it in practice, so we
read it defensively and report presets as unsupported if it is absent.
"""
import json
import threading
import time
import urllib.error
import urllib.request

TIMEOUT = 1.5
META_TTL = 10.0  # effect/palette names never change at runtime; cache them


class WLEDClient:
    def __init__(self, config):
        self.cfg = config
        self._lock = threading.Lock()
        self._meta = None
        self._meta_at = 0.0
        self.reachable = False
        self.last_error = None

    def _url(self, path):
        port = self.cfg.get("wled_http_port")
        host = self.cfg.get("wled_ip")
        suffix = "" if port == 80 else f":{port}"
        return f"http://{host}{suffix}{path}"

    def _get(self, path):
        try:
            with urllib.request.urlopen(self._url(path), timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            self.reachable = True
            self.last_error = None
            return data
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            self.reachable = False
            self.last_error = f"WLED unreachable at {self.cfg.get('wled_ip')}: {exc}"
            return None

    def _post(self, payload):
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._url("/json/state"), data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                resp.read()
            self.reachable = True
            self.last_error = None
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self.reachable = False
            self.last_error = f"WLED write failed: {exc}"
            return False

    # --- reads -------------------------------------------------------------
    def state(self):
        return self._get("/json/state")

    def info(self):
        return self._get("/json/info")

    def led_count(self):
        info = self.info()
        if not info:
            return None
        return (info.get("leds") or {}).get("count")

    def meta(self, force=False):
        """Effects, palettes, presets and current selections. Cached briefly."""
        with self._lock:
            fresh = self._meta and (time.time() - self._meta_at) < META_TTL
            cached = dict(self._meta) if fresh else None
        if cached and not force:
            live = self.state() or {}
            cached.update(self._selections(live))
            return cached

        combined = self._get("/json")
        if combined is None:
            return {"effects": [], "palettes": [], "presets": [],
                    "offline": True, "error": self.last_error}

        meta = {
            "effects": combined.get("effects") or [],
            "palettes": combined.get("palettes") or [],
            "presets": self._presets(),
            "presets_supported": self._presets_supported,
        }
        with self._lock:
            self._meta, self._meta_at = dict(meta), time.time()
        meta.update(self._selections(combined.get("state") or {}))
        return meta

    _presets_supported = True

    def _presets(self):
        """/presets.json is undocumented; absence is not an error."""
        raw = self._get("/presets.json")
        if raw is None or not isinstance(raw, dict):
            self._presets_supported = False
            return []
        self._presets_supported = True
        out = []
        for key, body in raw.items():
            if not isinstance(body, dict) or not body:
                continue  # slot 0 and empty slots carry no name
            try:
                pid = int(key)
            except ValueError:
                continue
            if pid <= 0:
                continue
            out.append({"id": pid, "name": body.get("n") or f"Preset {pid}"})
        return sorted(out, key=lambda p: p["id"])

    @staticmethod
    def _selections(state):
        seg = state.get("seg") or []
        first = seg[0] if isinstance(seg, list) and seg else (seg if isinstance(seg, dict) else {})
        return {
            "on": state.get("on"),
            "bri": state.get("bri"),
            "ps": state.get("ps"),
            "fx": first.get("fx"),
            "pal": first.get("pal"),
        }

    # --- writes ------------------------------------------------------------
    def set_power(self, on):
        return self._post({"on": bool(on)})

    def set_brightness(self, value):
        return self._post({"bri": max(0, min(255, int(value)))})

    def set_preset(self, preset_id):
        return self._post({"ps": int(preset_id)})

    def set_effect(self, index):
        return self._post({"seg": [{"id": 0, "fx": int(index)}]})

    def set_palette(self, index):
        return self._post({"seg": [{"id": 0, "pal": int(index)}]})
