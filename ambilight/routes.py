"""The HTTP API: one small function per URL, looked up in ROUTES.

Every handler takes the hub and the request's query, and returns
(payload, status code). Adding an endpoint is one function plus one entry.
A ValueError raised anywhere becomes a 400 in the server.
"""
import urllib.parse
from pathlib import Path

import numpy as np

from .config import DEFAULTS, PROFILES
from .effects import catalogue as effect_catalogue, state_for
from .plugins.output_serial import list_ports, required_baud
from .provision import esptool_available, known_networks
from .recorder import RECORDINGS_DIR, replay

OK = 200


class Query:
    """The parsed query string, read one value at a time."""

    def __init__(self, parsed):
        self.parsed = parsed

    def one(self, key, default=None):
        return self.parsed.get(key, [default])[0]

    def flag(self, key, default="0"):
        return self.one(key, default) in ("1", "true", "on", "yes")


def pyserial_available():
    try:
        import serial  # noqa: F401
    except ImportError:
        return False
    return True


# --- sync and settings -------------------------------------------------------
def status(hub, q):
    return hub.status(), OK


# The look sync falls back to: a plain warm white. It differs from the sync
# preset's colour, which matters: WLED only clears the active preset on a real
# change, and the watcher in intent.py treats an active preset as "sync on".
FALLBACK_LOOK = {"seg": [{"id": 0, "fx": 0, "col": [[255, 170, 80]]}]}


def sync_on(hub, q):
    hub.wizard.stop()
    hub.intent.resume()
    hub.wled.set_preset(hub.cfg.get("sync_preset"))
    hub.engine.start()
    return hub.status(), OK


def sync_off(hub, q):
    hub.engine.stop()
    hub.wled.apply(FALLBACK_LOOK)
    return hub.status(), OK


def sync_toggle(hub, q):
    return (sync_off if hub.engine.running else sync_on)(hub, q)


def profile(hub, q, name):
    hub.cfg.apply_profile(name)
    return hub.status(), OK


def profiles(hub, q):
    return {"builtin": list(PROFILES), "user": hub.cfg.get("user_profiles"),
            "active": hub.cfg.get("profile")}, OK


def profile_save(hub, q):
    hub.cfg.save_profile(q.one("name", ""))
    return hub.status(), OK


def profile_delete(hub, q):
    hub.cfg.delete_profile(q.one("name", ""))
    return hub.status(), OK


def set_values(hub, q):
    # "pin" in a query authenticates (Shortcuts); changing it is "new_pin".
    changes = {k: v[0] for k, v in q.parsed.items() if k not in ("pin", "new_pin")}
    if "new_pin" in q.parsed:
        changes["pin"] = q.one("new_pin")
    changed = hub.cfg.update(changes)
    return {"changed": changed, **hub.status()}, OK


def config(hub, q):
    return hub.public_config(), OK


def defaults(hub, q):
    return {k: v for k, v in DEFAULTS.items() if k != "pin"}, OK


def displays(hub, q):
    return {"monitors": monitors()}, OK


def diagnostics(hub, q):
    return hub.diagnostics(), OK


def monitors():
    """Displays that can be captured, numbered as monitor_index expects."""
    import mss

    with mss.mss() as shots:
        return [{"index": i, "width": m["width"], "height": m["height"], "primary": i == 1}
                for i, m in enumerate(shots.monitors) if i > 0]


# --- recordings: capture real scenes, replay them while tuning ----------------
def record_start(hub, q):
    seconds = max(1.0, min(120.0, float(q.one("seconds", "20"))))
    try:
        name = hub.engine.start_recording(seconds)
    except RuntimeError as exc:
        return {"error": str(exc)}, 409
    return {"recording": name, "seconds": seconds}, OK


def record_replay(hub, q):
    # Only a bare file name inside the recordings folder: no path tricks.
    name = Path(q.one("file", "")).name
    target = RECORDINGS_DIR / name
    if not name or not target.is_file():
        return {"error": f"no recording named {name!r}"}, 404
    return {"file": name, **replay(target, hub.cfg.snapshot())[1]}, OK


# --- the controller's own modes ------------------------------------------------
def wled_meta(hub, q):
    return hub.wled.meta(), OK


def wled_power(hub, q):
    return {"ok": hub.wled.set_power(q.flag("on", "1"))}, OK


def wled_brightness(hub, q):
    return {"ok": hub.wled.set_brightness(q.one("value", "128"))}, OK


def hand_to_controller(hub):
    """Realtime data overrides WLED's own rendering, so sync must stop first."""
    hub.engine.stop()
    hub.wizard.stop()


def take_over_strip(hub):
    """Setup tools paint the strip themselves; keep the watcher from restarting sync."""
    hub.intent.pause()
    hub.engine.stop()


def wled_preset(hub, q):
    hand_to_controller(hub)
    return {"ok": hub.wled.set_preset(q.one("id", "1")), **hub.status()}, OK


def wled_effect(hub, q):
    hand_to_controller(hub)
    return {"ok": hub.wled.set_effect(q.one("index", "0")), **hub.status()}, OK


def wled_palette(hub, q):
    hand_to_controller(hub)
    return {"ok": hub.wled.set_palette(q.one("index", "0")), **hub.status()}, OK


def effects(hub, q):
    return {"effects": effect_catalogue()}, OK


def run_effect(hub, q):
    hand_to_controller(hub)
    payload = state_for(q.one("fx", "9"), q.one("speed"), q.one("intensity"),
                        hex_colour(q.one("color")), q.one("brightness"))
    return {"ok": hub.wled.apply(payload), **hub.status()}, OK


def hex_colour(text):
    """'ff8800' or '#ff8800' -> [255, 136, 0]; anything else -> None."""
    digits = (text or "").lstrip("#")
    if len(digits) != 6:
        return None
    try:
        return [int(digits[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return None


# --- setup wizard ----------------------------------------------------------------
def count_start(hub, q):
    take_over_strip(hub)
    reported = hub.wled.led_count() or hub.cfg.get("led_count")
    return hub.wizard.count_start(reported), OK


def count_answer(hub, q):
    return hub.wizard.count_answer(q.flag("lit")), OK


def marker(hub, q):
    take_over_strip(hub)
    return hub.wizard.marker(q.one("index", "0")), OK


def gradient(hub, q):
    take_over_strip(hub)
    return hub.wizard.gradient(), OK


def wizard_stop(hub, q):
    hub.intent.resume()
    return hub.wizard.stop(), OK


# --- webcam calibration (camrun.py drives these) ------------------------------
def calibrate_paint(hub, q):
    """Light the LEDs round the monitor in one raw colour, for the camera."""
    rgb = hex_colour(q.one("rgb"))
    if rgb is None:
        return {"error": "rgb must be six hex digits, e.g. ff8000"}, 400
    take_over_strip(hub)
    cfg = hub.cfg.snapshot()
    lit = (sum(cfg[k] for k in ("edge_bottom", "edge_right", "edge_top", "edge_left"))
           if cfg["layout"] == "frame" else cfg["led_count"])
    pixels = np.zeros((cfg["led_count"], 3), dtype=np.uint8)
    pixels[:min(lit, cfg["led_count"])] = rgb
    hub.engine.set_override(pixels)
    return {"painted": rgb, "leds": int(min(lit, cfg["led_count"]))}, OK


def calibrate_done(hub, q):
    hub.intent.resume()
    return hub.wizard.stop(), OK


# --- USB: serial output and flashing -----------------------------------------------
def serial_ports(hub, q):
    cfg = hub.cfg.snapshot()
    return {
        "ports": list_ports(),
        "current": cfg["serial_port"],
        "output": cfg["output"],
        "baud": cfg["serial_baud"],
        "recommended_baud": required_baud(cfg["led_count"], cfg["fps"]),
        "pyserial": pyserial_available(),
    }, OK


def flash_check(hub, q):
    return {"esptool": esptool_available(), "pyserial": pyserial_available(),
            "ports": list_ports()}, OK


def flash_networks(hub, q):
    return known_networks(), OK


def flash_start(hub, q):
    port, ssid = q.one("port", ""), q.one("ssid", "")
    if not port or not ssid:
        return {"error": "a port and a Wi-Fi name are required"}, 400
    if port not in list_ports():
        return {"error": "that port is not a connected board"}, 400
    try:
        take_over_strip(hub)    # the cable cannot carry pixels and a flash at once
        hub.provisioner.start(port, ssid, q.one("password", ""), flash=q.one("flash", "0") == "1")
    except RuntimeError as exc:
        return {"error": str(exc)}, 409
    return hub.provisioner.status(), OK


def camera_list(hub, q):
    try:
        listed = hub.camera.cameras()
    except RuntimeError as exc:
        return {"error": str(exc)}, 503
    cameras = [{"name": name, "builtin": builtin} for name, builtin in listed]
    return {"cameras": cameras, **hub.camera.status()}, OK


def camera_start(hub, q):
    try:
        hub.camera.start(q.one("task", "calibrate"), q.one("camera") or None, int(q.one("seconds", "60")))
    except RuntimeError as exc:
        return {"error": str(exc)}, 409
    return hub.camera.status(), OK


def camera_status(hub, q):
    return hub.camera.status(), OK


def flash_status(hub, q):
    return hub.provisioner.status(), OK


def flash_adopt(hub, q):
    """Point the hub at the board that was just provisioned."""
    st = hub.provisioner.status()
    if not st.get("ip"):
        return {"error": "no address to adopt yet"}, 409
    hub.cfg.update({"wled_ip": st["ip"], "output": "udp"})
    return hub.status(), OK


ROUTES = {
    "/api/status": status,
    "/api/sync/on": sync_on,
    "/api/sync/off": sync_off,
    "/api/sync/toggle": sync_toggle,
    "/api/profiles": profiles,
    "/api/profiles/save": profile_save,
    "/api/profiles/delete": profile_delete,
    "/api/set": set_values,
    "/api/config": config,
    "/api/defaults": defaults,
    "/api/monitors": displays,
    "/api/diagnostics": diagnostics,
    "/api/record/start": record_start,
    "/api/record/replay": record_replay,
    "/api/wled/meta": wled_meta,
    "/api/wled/power": wled_power,
    "/api/wled/brightness": wled_brightness,
    "/api/wled/preset": wled_preset,
    "/api/wled/effect": wled_effect,
    "/api/wled/palette": wled_palette,
    "/api/effects": effects,
    "/api/effect/run": run_effect,
    "/api/wizard/count/start": count_start,
    "/api/wizard/count/answer": count_answer,
    "/api/wizard/marker": marker,
    "/api/wizard/gradient": gradient,
    "/api/wizard/stop": wizard_stop,
    "/api/calibrate/paint": calibrate_paint,
    "/api/calibrate/done": calibrate_done,
    "/api/camera/list": camera_list,
    "/api/camera/start": camera_start,
    "/api/camera/status": camera_status,
    "/api/serial/ports": serial_ports,
    "/api/flash/check": flash_check,
    "/api/flash/networks": flash_networks,
    "/api/flash/start": flash_start,
    "/api/flash/status": flash_status,
    "/api/flash/adopt": flash_adopt,
}


def handle(hub, path, parsed):
    """Run the handler for `path`. Returns (payload, status code)."""
    q = Query(parsed)
    if path.startswith("/api/profile/"):
        return profile(hub, q, urllib.parse.unquote(path.rsplit("/", 1)[-1]))
    handler = ROUTES.get(path)
    if handler is None:
        return {"error": "not found"}, 404
    return handler(hub, q)
