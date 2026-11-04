"""Single-file JSON config, saved on every change."""
import ipaddress
import json
import logging
import math
import os
import re
import tempfile
import threading
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

log = logging.getLogger(__name__)

# Moods trade smoothness for reaction time. They only touch motion, never the
# colour tuning, so picking one cannot undo a calibration. "blend" is the
# Response setting: 0.3 settles in ~0.37s, 0.7 in ~0.13s (smoothing.settle_seconds).
PROFILES = {
    "movie": {"blend": 0.3, "fps": 60},
    "game": {"blend": 0.70, "fps": 60},
}

DEFAULTS = {
    "wled_ip": "4.3.2.1",
    "wled_port": 21324,
    "wled_http_port": 80,
    "http_port": 8080,
    "pin": "",
    # Which display to mirror: 0 = automatic (the external monitor when one is
    # connected, else the built-in screen); 1, 2... = that display.
    "monitor_index": 0,
    "led_count": 240,
    # Blind default: span the whole strip until the setup wizard calibrates.
    "left_led": 0,
    "right_led": 239,
    "fade_leds": 12,
    "band_height": 150,
    "profile": "movie",
    "saturation": 1.2,
    "blend": 0.3,
    "brightness": 1.0,
    "fps": 60,
    # Colour engine. colour_match converts screen colour into the WS2812B's own
    # primaries (colour.LED_MATRIX), which already corrects its cold white; the
    # white-balance gains are left neutral for fine-tuning on /calibrate.
    "colour_match": True,
    # Screen-to-strip colour matrix measured by the webcam calibration
    # (camcal.py), row-major 3x3; empty = the WS2812B datasheet model.
    "led_matrix": [],
    # The strip's white point. 6500 keeps the screen's own D65; lower is warmer.
    "white_kelvin": 4500,
    "wb_r": 1.0,
    "wb_g": 1.0,
    "wb_b": 1.0,
    "led_gamma": 1.5,
    # S-curve after the LED gamma: brights brighter, darks darker. 1.0 = none.
    "contrast": 1.5,
    "black_level": 0.03,
    "colour_weight": 0.6,
    # LED safety (safety.py). Defaults suit a 5V 5A supply: peaks stay under
    # 3.8A (WLED's own limiter, set to 4A, is the hardware-side backstop) and a
    # bright scene held for minutes settles at 3A.
    "power_limit_ma": 3800,
    "sustained_ma": 3000,
    "spread": 0.3,
    "sync_on_start": False,
    # The WLED preset that means "follow my monitor" (saved on the board).
    "sync_preset": 1,
    # Skip black bars under video that is not the monitor's shape.
    "letterbox": True,
    # "strip" follows the bottom edge only; "frame" wraps the whole monitor,
    # starting bottom-left and running bottom, right, top, left.
    "layout": "strip",
    "edge_bottom": 36,
    "edge_right": 20,
    "edge_top": 36,
    "edge_left": 20,
    # Physical size of the visible screen (27" 16:9) and the strip's LED pitch,
    # so a strip stuck inside the monitor's edge maps to the stretch it covers.
    "screen_mm_w": 597.0,
    "screen_mm_h": 336.0,
    "led_pitch_mm": 16.7,
    # Measured size of the strip's rectangle; 0 = estimate from LED counts.
    "box_mm_w": 0.0,
    "box_mm_h": 0.0,
    "edge_skip": 8,
    "zones": [],
    "output": "udp",
    "serial_port": "",
    "serial_baud": 921600,
}


class Config:
    def __init__(self, path=CONFIG_PATH):
        self._path = Path(path)
        self._lock = threading.Lock()
        self.save_lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        if not self._path.exists():
            return
        try:
            stored = json.loads(self._path.read_text())
            if not isinstance(stored, dict):
                raise ValueError("top level is not an object")
        except (OSError, ValueError) as exc:
            # A crash loop under launchd helps nobody: keep the bad file for
            # inspection and start from defaults, loudly.
            aside = self._path.with_name(self._path.name + ".bad")
            os.replace(self._path, aside)
            log.error("config %s is unreadable (%s); moved to %s, using defaults",
                      self._path, exc, aside)
            return
        # Unknown keys are dropped so a stale config cannot inject surprises,
        # and every value is validated as if it had arrived over the network.
        clean = {}
        for key, raw in stored.items():
            if key not in DEFAULTS:
                continue
            try:
                clean[key] = self.validate(key, raw)
            except ValueError as exc:
                log.warning("ignoring stored %s: %s", key, exc)
        with self._lock:
            self._data.update(clean)

    def save(self):
        """Write atomically: a temp file in the same folder, flushed, then renamed."""
        with self.save_lock:
            with self._lock:
                payload = json.dumps(self._data, indent=2, sort_keys=True)
            handle, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(handle, "w") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self._path)
            except OSError:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def validate(self, key, raw, led_count=None):
        if key == "zones":
            from .zones import normalize

            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError as exc:
                    raise ValueError(f"zones is not valid JSON: {exc}") from None
            return normalize(raw, led_count or self._data["led_count"])
        if key == "led_matrix":
            return colour_matrix(raw)
        return _coerce(key, raw)

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def get(self, key):
        with self._lock:
            return self._data[key]

    def update(self, changes):
        """Apply validated changes and persist. Returns the keys that changed."""
        clean = {key: self.validate(key, raw) for key, raw in changes.items()
                 if key in DEFAULTS and key != "zones"}
        # Zones are checked against the LED count they will live with, which may
        # be arriving in this same request.
        if "zones" in changes:
            clean["zones"] = self.validate("zones", changes["zones"], clean.get("led_count"))
        with self._lock:
            changed = [k for k, v in clean.items() if self._data.get(k) != v]
            self._data.update(clean)
            # A shorter strip invalidates any zone that ran off the old end.
            if "led_count" in clean and self._data["zones"]:
                from .zones import normalize

                reclamped = normalize(self._data["zones"], self._data["led_count"])
                if reclamped != self._data["zones"]:
                    self._data["zones"] = reclamped
                    changed.append("zones")
        if changed:
            self.save()
        return changed

    def apply_profile(self, name):
        if name not in PROFILES:
            raise ValueError(f"unknown profile {name!r}")
        changes = dict(PROFILES[name])
        changes["profile"] = name
        return self.update(changes)


_LIMITS = {
    "saturation": (0.0, 3.0),
    "blend": (0.01, 1.0),
    "brightness": (0.0, 1.0),
    "fps": (5, 120),
    "band_height": (10, 1000),
    "fade_leds": (0, 500),
    "wb_r": (0.2, 1.0),
    "wb_g": (0.2, 1.0),
    "wb_b": (0.2, 1.0),
    "led_gamma": (0.5, 3.0),
    "white_kelvin": (2700, 6500),
    "contrast": (0.5, 2.5),
    "black_level": (0.0, 0.2),
    "colour_weight": (0.0, 1.0),
    "power_limit_ma": (200, 20000),
    "sustained_ma": (200, 20000),
    "spread": (0.0, 0.8),
    "sync_preset": (1, 250),
    "led_count": (1, 1500),
    "left_led": (0, 1499),
    "right_led": (0, 1499),
    "monitor_index": (0, 8),
    "edge_bottom": (0, 500),
    "edge_right": (0, 500),
    "edge_top": (0, 500),
    "edge_left": (0, 500),
    "screen_mm_w": (50.0, 5000.0),
    "screen_mm_h": (50.0, 5000.0),
    "box_mm_w": (0.0, 5000.0),
    "box_mm_h": (0.0, 5000.0),
    "led_pitch_mm": (5.0, 100.0),
    "edge_skip": (0, 100),
    "http_port": (1024, 65535),
    "wled_port": (1, 65535),
    "wled_http_port": (1, 65535),
    "serial_baud": (9600, 2000000),
}


# Settings with a fixed set of values. Matching is case-insensitive.
CHOICES = {
    "layout": ("strip", "frame"),
    "output": ("udp", "serial"),
    "profile": tuple(PROFILES),
}

# Free-text settings and the shape they must have. The controller address
# goes into URLs and sockets, so it must be a bare IP or host name.
HOST_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$")
PATTERNS = {
    "serial_port": re.compile(r"^(/dev/[A-Za-z0-9._-]+)?$"),
    "pin": re.compile(r"^[A-Za-z0-9]{0,16}$"),
}


def _coerce(key, raw):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "on", "yes")
        return bool(raw)
    if isinstance(default, (int, float)):
        return coerce_number(key, raw, int if isinstance(default, int) else float)
    return coerce_text(key, str(raw).strip())


def coerce_number(key, raw, kind):
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, not {raw!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number")
    lo, hi = _LIMITS[key]
    return max(lo, min(hi, kind(number)))


def coerce_text(key, text):
    if key in CHOICES:
        lowered = text.lower()
        if lowered not in CHOICES[key]:
            raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
        return lowered
    if key == "wled_ip":
        return controller_address(text)
    if key in PATTERNS and not PATTERNS[key].match(text):
        raise ValueError(f"{key} has an invalid format")
    return text


def colour_matrix(raw):
    """[] or nine finite numbers (a 3x3 matrix, row by row)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError("led_matrix must be a JSON list of nine numbers") from None
    if raw == []:
        return []
    if not isinstance(raw, list) or len(raw) != 9:
        raise ValueError("led_matrix must be empty or have nine numbers")
    try:
        values = [float(v) for v in raw]
    except (TypeError, ValueError):
        raise ValueError("led_matrix must contain only numbers") from None
    if not all(math.isfinite(v) and abs(v) <= 10 for v in values):
        raise ValueError("led_matrix values must be finite and modest (|v| <= 10)")
    return values


def controller_address(text):
    """A bare IP address or host name (e.g. wled-6b0bc6.local), nothing else."""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if len(text) > 253 or not HOST_NAME.match(text):
        raise ValueError("the controller address must be an IP address or a host name")
    return text.lower()
