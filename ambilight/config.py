"""Single-file JSON config, saved on every change."""
import json
import threading
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Profiles tune responsiveness vs. smoothness. Values here override the live
# settings whenever a profile is selected.
PROFILES = {
    "movie": {"saturation": 1.35, "blend": 0.25, "fps": 50},
    "game": {"saturation": 1.15, "blend": 0.70, "fps": 60},
}

DEFAULTS = {
    "wled_ip": "4.3.2.1",
    "wled_port": 21324,
    "wled_http_port": 80,
    "http_port": 8080,
    "pin": "",
    "monitor_index": 1,
    "led_count": 240,
    # Blind default: span the whole strip until the setup wizard calibrates.
    "left_led": 0,
    "right_led": 239,
    "fade_leds": 12,
    "band_height": 150,
    "profile": "game",
    "saturation": 1.15,
    "blend": 0.70,
    "brightness": 1.0,
    "fps": 60,
    "sync_on_start": False,
    "zones": [],
    "output": "udp",
    "serial_port": "",
    "serial_baud": 921600,
}


class Config:
    def __init__(self, path=CONFIG_PATH):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        if not self._path.exists():
            return
        try:
            stored = json.loads(self._path.read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"config at {self._path} is unreadable: {exc}") from exc
        # Unknown keys are dropped so a stale config cannot inject surprises.
        with self._lock:
            self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})

    def save(self):
        with self._lock:
            payload = json.dumps(self._data, indent=2, sort_keys=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(payload)
        tmp.replace(self._path)

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def get(self, key):
        with self._lock:
            return self._data[key]

    def update(self, changes):
        """Apply validated changes and persist. Returns the keys that changed."""
        clean = {}
        for key, raw in changes.items():
            if key not in DEFAULTS:
                continue
            if key == "zones":
                from .zones import normalize
                if isinstance(raw, str):
                    raw = json.loads(raw)
                clean[key] = normalize(raw, self._data["led_count"])
                continue
            clean[key] = _coerce(key, raw)
        with self._lock:
            changed = [k for k, v in clean.items() if self._data.get(k) != v]
            self._data.update(clean)
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
    "led_count": (1, 1500),
    "left_led": (0, 1499),
    "right_led": (0, 1499),
    "monitor_index": (0, 8),
    "http_port": (1024, 65535),
    "wled_port": (1, 65535),
    "wled_http_port": (1, 65535),
    "serial_baud": (9600, 2000000),
}


def _coerce(key, raw):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "on", "yes")
        return bool(raw)
    if isinstance(default, int):
        value = int(float(raw))
    elif isinstance(default, float):
        value = float(raw)
    else:
        return str(raw).strip()
    lo, hi = _LIMITS[key]
    return max(lo, min(hi, value))
