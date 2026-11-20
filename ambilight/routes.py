"""The HTTP API: one small function per URL, looked up in ROUTES.

Every handler takes the hub and the request's query, and returns
(payload, status code). Adding an endpoint is one function plus one entry.
A ValueError raised anywhere becomes a 400 in the server.
"""
import time
import urllib.parse
from pathlib import Path

import numpy as np

from . import backup, bed, experience, qr, version
from .access import OwnerOnly
from .config import DEFAULTS, PROFILES
from .discovery import hub_urls
from .effects import catalogue as effect_catalogue, state_for
from .plugins.output_serial import list_ports, required_baud
from .provision import esptool_available, known_networks
from .recorder import RECORDINGS_DIR, replay
from .safety import shares

OK = 200
BRIGHTNESS_SETTLE_S = 1.5   # the board loads a preset in its next loop; give it time before looking
CONFIG_SAVE_S = 1.0     # the board writes new settings to flash in its next loop; reboot after that


class Query:
    """The parsed query string, read one value at a time, and whether its sender is the owner."""

    def __init__(self, parsed, owner=True):
        self.parsed = parsed
        self.owner = owner

    def one(self, key, default=None):
        return self.parsed.get(key, [default])[0]

    def flag(self, key, default="0"):
        return self.one(key, default) in ("1", "true", "on", "yes")


# Without a PIN, any device on the same Wi-Fi can drive the hub, which is fine for
# lights and looks. These could send the screen's colours elsewhere, open a camera,
# keep a recording, reveal the Mac's Wi-Fi networks or lock the owner out, so they
# need the owner: this Mac itself, or a device that knows the PIN.
OWNER_SETTINGS = frozenset({"wled_ip", "wled_port", "ddp_port", "wled_http_port", "output", "serial_port",
                            "serial_baud", "http_port", "unprotected", "new_pin"})
OWNER_ONLY = ("only this Mac can do that while the hub has no PIN; set a PIN on the Mac "
              "(Settings › Access and devices) and your other devices can too")


def need_owner(q):
    if not q.owner:
        raise OwnerOnly(OWNER_ONLY)


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
    hub.intent.nudge()      # the preset may have dropped the bed's segment
    return hub.status(), OK


def sync_off(hub, q):
    hub.engine.stop()
    hub.wled.apply(FALLBACK_LOOK)
    return hub.status(), OK


def lights_off(hub, q):
    """Every light dark at once: the monitor strip, the bed and the mouse.

    Power alone keeps WLED's active preset, which the watcher reads as "sync
    on", and realtime packets light a board that is off. So the board gets a
    real change of look with its power, before the stream stops; the watcher
    then has no preset to restart sync from.
    """
    hub.wizard.stop()
    ok = hub.wled.apply({"on": False, **FALLBACK_LOOK})
    hub.engine.stop()
    return {"ok": ok, **hub.status()}, OK


def sync_toggle(hub, q):
    return (sync_off if hub.engine.running else sync_on)(hub, q)


def profile(hub, q, name):
    hub.cfg.apply_profile(name)
    return hub.status(), OK


def profiles(hub, q):
    return {"builtin": list(PROFILES), "builtin_looks": PROFILES, "user": hub.cfg.get("user_profiles"),
            "active": hub.cfg.get("profile")}, OK


def profile_rename(hub, q):
    hub.cfg.rename_profile(q.one("name", ""), q.one("to", ""))
    return hub.status(), OK


def profile_duplicate(hub, q):
    hub.cfg.duplicate_profile(q.one("name", ""), q.one("to", ""))
    return hub.status(), OK


def profile_save(hub, q):
    hub.cfg.save_profile(q.one("name", ""), q.one("note"))
    return hub.status(), OK


def profile_describe(hub, q):
    hub.cfg.describe_profile(q.one("name", ""), q.one("note", ""))
    return hub.status(), OK


def profile_delete(hub, q):
    hub.cfg.delete_profile(q.one("name", ""))
    return hub.status(), OK


def set_values(hub, q):
    if OWNER_SETTINGS & set(q.parsed):
        need_owner(q)
    # "pin" in a query authenticates (Shortcuts); changing it is "new_pin".
    changes = {k: v[0] for k, v in q.parsed.items() if k not in ("pin", "new_pin")}
    if "new_pin" in q.parsed:
        changes["pin"] = q.one("new_pin")
    changed = hub.cfg.update(changes)
    settings_changed(hub, changed)
    return {"changed": changed, **hub.status()}, OK


def settings_changed(hub, changed):
    """What else must happen after settings change, however they were changed."""
    if any(key.startswith("bed_") for key in changed):
        hub.intent.nudge()


# --- Room Studio: scenes from a palette, no screen capture (experience.py) ----------
ROOM_FIELDS = {"motion_speed", "colour_intensity", "warp_strength", "texture_amount", "noise_scale",
               "wave_count", "brightness"}


def room_scenes(hub, q):
    cfg = hub.cfg.snapshot()
    return {"scenes": experience.catalogue(cfg["room_custom"]), "active": experience.scene_for(cfg)["id"],
            "running": hub.engine.running and hub.engine.mode == "room",
            "levels": list(experience.AUDIO_LEVELS)}, OK


def room_start(hub, q):
    """Light the strip with a scene. The screen is not captured; the board is asked for the
    sync preset as for screen sync, so its watcher keeps the stream on."""
    scene = q.one("scene")
    if scene:
        if scene not in experience.all_scenes(hub.cfg.get("room_custom")):
            return {"error": f"no scene called {scene!r}"}, 404
        hub.cfg.update({"room_scene": scene})
    hub.wizard.stop()
    hub.intent.resume()
    hub.wled.set_preset(hub.cfg.get("sync_preset"))
    hub.engine.start("room")
    hub.intent.nudge()
    return hub.status(), OK


def room_stop(hub, q):
    if hub.engine.running and hub.engine.mode == "room":
        return sync_off(hub, q)
    return hub.status(), OK


def _custom(hub):
    return dict(hub.cfg.get("room_custom"))


def _own_scene(hub, scene_id):
    custom = _custom(hub)
    if scene_id not in custom:
        raise ValueError("only your own scenes can be changed; duplicate a built-in one first")
    return custom


def room_duplicate(hub, q):
    """A copy of any scene under a new name, made active; built-ins stay as they are."""
    source = experience.all_scenes(hub.cfg.get("room_custom")).get(q.one("id", ""))
    if source is None:
        return {"error": "no such scene"}, 404
    custom = _custom(hub)
    if len(custom) >= experience.MAX_CUSTOM_SCENES:
        raise ValueError(f"at most {experience.MAX_CUSTOM_SCENES} scenes of your own; delete one first")
    name = (q.one("name") or f"My {source['name']}").strip()
    clone = experience.copy_scene(source, name, set(experience.all_scenes(custom)))
    custom[clone["id"]] = clone
    hub.cfg.update({"room_custom": custom, "room_scene": clone["id"]})
    return {"scene": clone["id"], **hub.status()}, OK


def room_edit(hub, q):
    """Change one of your scenes: its name, a parameter, or its palette (hex colours, comma-separated)."""
    scene_id = q.one("id", "")
    custom = _own_scene(hub, scene_id)
    scene = {**custom[scene_id], "parameters": dict(custom[scene_id]["parameters"])}
    for key in q.parsed:
        if key in ("id", "pin"):
            continue
        value = q.one(key)
        if key == "name":
            scene["name"] = value
        elif key == "palette":
            colours = [c.strip() for c in value.split(",") if c.strip()]
            if len(colours) < 2:
                raise ValueError("a palette needs at least two colours")
            # The first colour closes the loop too, so the drift never shows a seam.
            colours = colours + [colours[0]]
            scene["palette"] = [{"position": round(i / (len(colours) - 1), 4), "hex": c if c.startswith("#")
                                 else "#" + c} for i, c in enumerate(colours)]
        elif key in ROOM_FIELDS:
            scene["parameters"][key] = value
        else:
            raise ValueError(f"a scene has no setting {key!r}")
    custom[scene_id] = experience.validate_scene(scene)
    hub.cfg.update({"room_custom": custom})
    return hub.status(), OK


def room_reset(hub, q):
    """Your copy of a built-in back to it, or the quick controls back to their defaults."""
    scene_id = q.one("id", "")
    custom = _custom(hub)
    changes = {k: DEFAULTS[k] for k in ("room_brightness", "room_speed", "room_intensity", "room_audio")}
    if scene_id in custom and custom[scene_id].get("based_on") in experience.SCENES:
        base = experience.SCENES[custom[scene_id]["based_on"]]
        custom[scene_id] = experience.validate_scene({**base, "id": scene_id, "name": custom[scene_id]["name"],
                                                      "based_on": base["id"]})
        changes["room_custom"] = custom
    hub.cfg.update(changes)
    return hub.status(), OK


def room_delete(hub, q):
    scene_id = q.one("id", "")
    custom = _own_scene(hub, scene_id)
    del custom[scene_id]
    changes = {"room_custom": custom}
    if hub.cfg.get("room_scene") == scene_id:
        changes["room_scene"] = DEFAULTS["room_scene"]
    hub.cfg.update(changes)
    return hub.status(), OK


def room_export(hub, q):
    scene = experience.all_scenes(hub.cfg.get("room_custom")).get(q.one("id", ""))
    if scene is None:
        return {"error": "no such scene"}, 404
    return scene, OK


def room_import(hub, q, body):
    """A scene file, checked strictly, added as one of your own under a free id."""
    if len(body) > ROOM_MAX_BODY:
        raise ValueError("a scene file is a few kB at most")
    try:
        scene = experience.validate_scene(body.decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError("the scene file is not text") from None
    custom = _custom(hub)
    if len(custom) >= experience.MAX_CUSTOM_SCENES:
        raise ValueError(f"at most {experience.MAX_CUSTOM_SCENES} scenes of your own; delete one first")
    scene["id"] = experience.free_id(scene["id"], set(experience.all_scenes(custom)))
    custom[scene["id"]] = experience.validate_scene(scene)
    hub.cfg.update({"room_custom": custom})
    return {"scene": scene["id"], **hub.status()}, OK


def beat_test(hub, q):
    """A synthetic beat through the normal output path and guard; no sound is opened."""
    hub.engine.meter.fire(time.perf_counter())
    return hub.status(), OK


# --- backups: a file of your settings, read back only after a report -----------
def backup_export(hub, q):
    return backup.export(hub.cfg.snapshot(), q.one("kind", "full"), version.build_id(),
                         backup.iso(time.time())), OK


def backup_preview(hub, q, body):
    report = backup.plan(hub.cfg, backup.parse(body))
    return {"import_id": hub.pending_import.hold(report, time.monotonic()), **report}, OK


def backup_apply(hub, q):
    groups = [g for g in q.one("groups", "").split(",") if g]
    if "connection" in groups:
        need_owner(q)
    report = hub.pending_import.take(q.one("id", ""), time.monotonic())
    result = backup.apply(hub.cfg, report, groups, hub.cfg.restore_folder(), version.build_id(), time.time())
    settings_changed(hub, result["changed"])
    return {**result, **hub.status()}, OK


def backup_list(hub, q):
    return {"restore_points": backup.restore_points(hub.cfg.restore_folder())}, OK


def backup_restore(hub, q):
    try:
        result = backup.restore(hub.cfg, hub.cfg.restore_folder(), q.one("file", ""), version.build_id(), time.time(),
                                connection=q.owner)
    except FileNotFoundError as exc:
        return {"error": str(exc)}, 404
    except OwnerOnly:
        raise OwnerOnly(OWNER_ONLY) from None
    settings_changed(hub, result["changed"])
    return {**result, **hub.status()}, OK


def config(hub, q):
    return hub.public_config(), OK


def pair(hub, q):
    """QR codes another device can scan to open the hub, or the board's own page.

    The hub's .local name comes first: it survives the Mac's address changing,
    which matters most for a page added to a Home Screen. The codes hold only
    addresses; a hub with a PIN still asks each new device for it once.
    """
    by_name, by_address = hub_urls(hub.port)
    board_named, *board_by_address = hub.board_urls()
    targets = [("Hub", by_name), ("Hub by IP address", by_address),
               ("Strip's own page", board_named)]
    targets += [("Strip's own page by IP address", url) for url in board_by_address]
    try:
        return {"targets": [{"label": label, "url": url, "modules": qr.modules(url)}
                            for label, url in targets]}, OK
    except RuntimeError as exc:
        return {"error": str(exc)}, 503


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
    need_owner(q)
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
    """The board's own brightness: every light, whatever it shows.

    A manual change may end the board's active preset, and with it "monitor
    sync" (board.py). While syncing, the same command loads the sync preset
    again, which leaves brightness alone, and the watcher is held off until
    the board has settled.
    """
    payload = {"bri": max(0, min(255, int(float(q.one("value", "128")))))}
    if hub.engine.running:
        hub.intent.pause(BRIGHTNESS_SETTLE_S)
        payload["ps"] = hub.cfg.get("sync_preset")
    return {"ok": hub.wled.apply(payload)}, OK


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
    total = hub.wled.led_count()     # the board counts the bed strip's LEDs too
    reported = total - hub.cfg.get("bed_leds") if total else hub.cfg.get("led_count")
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
    need_owner(q)
    return known_networks(), OK


def flash_start(hub, q):
    need_owner(q)
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


def bed_setup(hub, q):
    """Give the board a second output for the bed strip (0 LEDs removes it), then restart it."""
    try:
        leds, milliamps = int(q.one("leds", "0")), round(float(q.one("amps", "2")) * 1000)
        pin = int(q.one("pin", hub.cfg.get("bed_pin")))
    except ValueError:
        return {"error": "leds must be a whole number and amps a number"}, 400
    if hub.cfg.validate("bed_leds", leds) != leds or hub.cfg.validate("bed_power_ma", milliamps) != milliamps \
            or hub.cfg.validate("bed_pin", pin) != pin:
        return {"error": "the bed strip takes 0-600 LEDs on a 0.2-20 A adapter, on GPIO 1-16"}, 400
    board = hub.wled.config()
    if not board:
        return {"error": "the board is not answering; try again once it is back"}, 503
    # The board's limits are the hub's shares of the adapter, so they hold while it renders its own looks.
    monitor_share, bed_share = shares({**hub.cfg.snapshot(), "bed_leds": leds, "bed_power_ma": milliamps})
    payload = bed.bus_config(board["hw"]["led"], hub.cfg.get("led_count"), round(monitor_share["power_limit_ma"]),
                             leds, round(bed_share["power_limit_ma"]), pin)
    if not hub.wled.set_config(payload):
        return {"error": "the board did not take the new output; nothing changed"}, 502
    time.sleep(CONFIG_SAVE_S)
    hub.wled.reboot()
    hub.cfg.update({"bed_leds": leds, "bed_pin": pin, "bed_power_ma": milliamps, "bed_start": board["hw"]["led"]["ins"][0]["len"]})
    return {"rebooting": True, **hub.status()}, OK


def camera_list(hub, q):
    try:
        listed = hub.camera.cameras()
    except RuntimeError as exc:
        return {"error": str(exc)}, 503
    cameras = [{"name": name, "builtin": builtin} for name, builtin in listed]
    return {"cameras": cameras, **hub.camera.status()}, OK


def camera_start(hub, q):
    need_owner(q)
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
    need_owner(q)
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
    "/api/lights/off": lights_off,
    "/api/profiles": profiles,
    "/api/profiles/save": profile_save,
    "/api/profiles/describe": profile_describe,
    "/api/profiles/delete": profile_delete,
    "/api/profiles/rename": profile_rename,
    "/api/profiles/duplicate": profile_duplicate,
    "/api/set": set_values,
    "/api/config": config,
    "/api/pair": pair,
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
    "/api/bed/setup": bed_setup,
    "/api/camera/list": camera_list,
    "/api/camera/start": camera_start,
    "/api/camera/status": camera_status,
    "/api/serial/ports": serial_ports,
    "/api/flash/check": flash_check,
    "/api/flash/networks": flash_networks,
    "/api/flash/start": flash_start,
    "/api/flash/status": flash_status,
    "/api/flash/adopt": flash_adopt,
    "/api/backup/export": backup_export,
    "/api/backup/apply": backup_apply,
    "/api/backup/list": backup_list,
    "/api/backup/restore": backup_restore,
    "/api/room/scenes": room_scenes,
    "/api/room/start": room_start,
    "/api/room/stop": room_stop,
    "/api/room/duplicate": room_duplicate,
    "/api/room/edit": room_edit,
    "/api/room/reset": room_reset,
    "/api/room/delete": room_delete,
    "/api/room/export": room_export,
    "/api/beat/test": beat_test,
}

# The only addresses that take a request body: a JSON file, at most MAX_BODY bytes.
POST_ROUTES = {
    "/api/backup/preview": backup_preview,
    "/api/room/import": room_import,
}
ROOM_MAX_BODY = 16 * 1024
MAX_BODY = backup.MAX_BYTES


def handle_post(hub, path, parsed, body, owner=False):
    """Run the POST handler for `path` with the request body. Returns (payload, status code)."""
    handler = POST_ROUTES.get(path)
    if handler is None:
        return {"error": "not found"}, 404
    return guarded(lambda: handler(hub, Query(parsed, owner), body))


def handle(hub, path, parsed, owner=False):
    """Run the handler for `path`. Returns (payload, status code).

    `owner` is whether the sender is this Mac or knows the PIN; without it the
    owner-only actions are refused (fail closed when a caller forgets to say).
    """
    q = Query(parsed, owner)
    if path.startswith("/api/profile/"):
        return profile(hub, q, urllib.parse.unquote(path.rsplit("/", 1)[-1]))
    handler = ROUTES.get(path)
    if handler is None:
        return {"error": "not found"}, 404
    return guarded(lambda: handler(hub, q))


def guarded(run):
    try:
        return run()
    except OwnerOnly as exc:
        return {"error": str(exc)}, 409
