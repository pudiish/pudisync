"""Backups: settings saved as a file, and a file read back only after a report.

A backup is plain JSON, never code, and never holds the PIN or the power
limits. Reading one changes nothing: `plan` validates every setting as the
hub would store it and says what would change, what the hub would adjust and
what it refuses. `apply` then writes a restore point and makes one
Config.update, which checks the whole set before touching anything and saves
atomically, so an import lands whole or not at all.
"""
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from .access import OwnerOnly
from .config import DEFAULTS, MAX_PROFILES, PROFILE_KEYS, PROFILES, profile_name

FORMAT = "pudisync-backup"
SCHEMA_VERSION = 1
MAX_BYTES = 256 * 1024      # a full backup is a few kB; this bounds the work a file can cause
MAX_DEPTH = 8               # deepest real shape: file > settings > room_custom > scene > audio > mappings > mapping > value
MAX_LOOKS_IN_FILE = 100
KEEP_RESTORE_POINTS = 10
HOLD_S = 600.0              # how long a previewed import waits for its Apply

# Each setting a backup carries, by what it describes. Looks travel by default;
# the rest belong to one strip, one board or one Mac, so restoring them is a choice.
GROUPS = {
    "looks": ("profile", "saturation", "blend", "brightness", "fps", "white_kelvin", "contrast",
              "colour_weight", "immersion", "enrich", "dark_boost", "adaptive", "hue_balance",
              "adaptive_pace", "bass_bounce", "bass_shake", "spread", "user_profiles", "mouse_sync", "bed_mode", "bed_look",
              "bed_side", "bed_colour", "bed_effect", "bed_speed", "bed_brightness", "style", "audio_react", "calm",
              "trippy_speed", "trippy_warp", "trippy_colour", "trippy_texture", "cinematic_motion", "room_scene",
              "room_brightness", "room_speed", "room_intensity", "room_audio", "room_custom"),
    "layout": ("led_count", "left_led", "right_led", "fade_leds", "band_height", "layout", "edge_bottom",
               "edge_right", "edge_top", "edge_left", "screen_mm_w", "screen_mm_h", "led_pitch_mm",
               "box_mm_w", "box_mm_h", "edge_skip", "letterbox", "bed_leds", "bed_reverse", "zones"),
    "calibration": ("colour_match", "led_matrix", "wb_r", "wb_g", "wb_b", "led_gamma", "black_level"),
    "connection": ("wled_ip", "wled_port", "ddp_port", "wled_http_port", "http_port", "monitor_index",
                   "output", "serial_port", "serial_baud", "sync_preset", "bed_pin", "bed_start",
                   "sync_on_start"),
}
DEFAULT_GROUPS = ("looks",)
# Never read from a file and never written to one: the PIN and protected mode
# guard the hub, and the current limits guard the adapter and the strips.
LOCAL_ONLY = ("pin", "unprotected", "power_limit_ma", "sustained_ma", "supply_ma", "bed_power_ma")
GROUP_OF = {key: group for group, keys in GROUPS.items() for key in keys}
RESTORE_POINT = re.compile(r"^config-([0-9]{8}-[0-9]{6})(?:-([0-9]{1,2}))?\.json$")


def export(snapshot, kind, app_version, exported_at):
    """The backup file's content: every portable setting, or only your looks."""
    if kind == "full":
        settings = {key: value for key, value in snapshot.items() if key in GROUP_OF}
    elif kind == "looks":
        settings = {"user_profiles": snapshot["user_profiles"]}
    else:
        raise ValueError("kind must be full or looks")
    return {"format": FORMAT, "schema_version": SCHEMA_VERSION, "kind": kind,
            "app_version": app_version, "exported_at": exported_at, "settings": settings}


def depth(value):
    """How deeply `value` nests, counted without recursion."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        deepest = max(deepest, level)
        if isinstance(item, dict):
            stack.extend((child, level + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, level + 1) for child in item)
    return deepest


def parse(raw):
    """Bytes from an upload or a restore point -> the backup, or ValueError saying why not."""
    if len(raw) > MAX_BYTES:
        raise ValueError(f"the file is over {MAX_BYTES // 1024} kB; a PudiSync backup is far smaller")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("the file is not valid JSON") from None
    if depth(data) > MAX_DEPTH:
        raise ValueError("the file nests too deeply to be a PudiSync backup")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("this is not a PudiSync backup")
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("the backup has no valid schema_version")
    if version > SCHEMA_VERSION:
        raise ValueError("this backup was made by a newer PudiSync; update the hub first")
    if not isinstance(data.get("settings"), dict):
        raise ValueError("the backup has no settings")
    return data


def plan(cfg, data):
    """What importing `data` would do, setting by setting. Changes nothing."""
    current = cfg.snapshot()
    settings = data["settings"]
    # Zones are checked against the strip length they would live with.
    try:
        led_count = cfg.validate("led_count", settings.get("led_count", current["led_count"]))
    except ValueError:
        led_count = current["led_count"]
    rows, looks = [], []
    for key, value in settings.items():
        if key == "user_profiles":
            looks = plan_looks(cfg, current["user_profiles"], value)
        else:
            rows.append(plan_setting(cfg, key, value, current, led_count))
    return {"kind": data.get("kind") if data.get("kind") in ("full", "looks") else None,
            "app_version": str(data.get("app_version") or "")[:64],
            "rows": rows, "looks": looks, "has_looks": "user_profiles" in settings,
            "default_groups": list(DEFAULT_GROUPS)}


def plan_setting(cfg, key, value, current, led_count):
    row = {"key": key[:64], "group": GROUP_OF.get(key)}
    if key in LOCAL_ONLY:
        return {**row, "status": "local"}
    if key not in GROUP_OF:
        return {**row, "status": "unknown"}
    if isinstance(DEFAULTS[key], (bool, int, float, str)) and not isinstance(value, (bool, int, float, str)):
        return {**row, "status": "rejected", "reason": f"{key} must be a single value"}
    try:
        stored = cfg.validate(key, value, led_count)
    except ValueError as exc:
        return {**row, "status": "rejected", "reason": str(exc)}
    row.update(current=current[key], stored=stored)
    if stored == current[key]:
        return {**row, "status": "same"}
    if clamped(key, value, stored):
        return {**row, "status": "adjusted", "asked": value}
    return {**row, "status": "change"}


def clamped(key, asked, stored):
    """True when the hub would store a different number from the one asked for."""
    default = DEFAULTS[key]
    if isinstance(default, bool) or not isinstance(default, (int, float)):
        return False
    try:
        return float(asked) != float(stored)
    except (TypeError, ValueError):
        return False


def plan_looks(cfg, saved, raw):
    """Each look in the file: new, replacing yours, already the same, refused, or over the limit."""
    if not isinstance(raw, dict) or len(raw) > MAX_LOOKS_IN_FILE:
        return [{"name": "", "status": "rejected", "reason": "the looks in this file are not readable"}]
    out, total = [], len(saved)
    for raw_name, look in raw.items():
        entry = {"name": str(raw_name)[:40]}
        try:
            name = profile_name(raw_name)
            if name in PROFILES:
                raise ValueError(f"{name} is a built-in look")
            if not isinstance(look, dict):
                raise ValueError("it is not a list of settings")
            if any(not isinstance(v, (bool, int, float, str)) for k, v in look.items() if k in PROFILE_KEYS):
                raise ValueError("a setting in it is not a single value")
            clean = {k: cfg.validate(k, v) for k, v in look.items() if k in PROFILE_KEYS}
        except ValueError as exc:
            out.append({**entry, "status": "rejected", "reason": str(exc)})
            continue
        ignored = sorted(str(k)[:40] for k in look if k not in PROFILE_KEYS)[:10]
        if name in saved:
            status = "same" if saved[name] == clean else "replaces"
        elif total >= MAX_PROFILES:
            status = "full"
        else:
            status, total = "new", total + 1
        out.append({**entry, "name": name, "status": status, "look": clean, "ignored": ignored})
    return out


def changes_for(report, groups, saved, exact=False):
    """The Config.update that applies `report` for the chosen groups.

    An import adds its looks to yours. An exact apply (a restore) makes your
    looks the file's, so undoing an import also removes the looks it added.
    """
    chosen = set(groups)
    unknown = chosen - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown group {sorted(unknown)[0]!r}")
    changes = {row["key"]: row["stored"] for row in report["rows"]
               if row["status"] in ("change", "adjusted") and row["group"] in chosen}
    if "looks" not in chosen:
        return changes
    if exact and report["has_looks"]:
        looks = {entry["name"]: entry["look"] for entry in report["looks"] if entry["status"] != "rejected"}
        if len(looks) > MAX_PROFILES:
            raise ValueError(f"the file has more than {MAX_PROFILES} looks")
        if looks != saved:
            changes["user_profiles"] = looks
        return changes
    adding = {entry["name"]: entry["look"] for entry in report["looks"]
              if entry["status"] in ("new", "replaces")}
    if adding:
        merged = {**saved, **adding}
        # Looks saved since the preview may have used up the room it counted on.
        if len(merged) > MAX_PROFILES:
            raise ValueError(f"that would make more than {MAX_PROFILES} looks; delete some first")
        changes["user_profiles"] = merged
    return changes


def apply(cfg, report, groups, folder, app_version, now, exact=False):
    """Write a restore point, then apply the plan in one update. Returns what happened."""
    changes = changes_for(report, groups, cfg.get("user_profiles"), exact)
    if not changes:
        return {"changed": [], "restore_point": None, "mismatched": []}
    point = save_restore_point(cfg.snapshot(), folder, app_version, now)
    changed = cfg.update(changes)
    after = cfg.snapshot()
    # Read back: anything not stored as planned is reported, never assumed.
    mismatched = sorted(key for key, value in changes.items() if after[key] != value)
    return {"changed": changed, "restore_point": point, "mismatched": mismatched}


def save_restore_point(snapshot, folder, app_version, now):
    """The settings as they are, as a backup file in `folder`; keeps the newest few."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    name = f"config-{stamp}.json"
    for n in range(2, 100):
        if not (folder / name).exists():
            break
        name = f"config-{stamp}-{n}.json"
    body = json.dumps(export(snapshot, "full", app_version, iso(now)), indent=2, sort_keys=True)
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=".restore-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, folder / name)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    for old in restore_points(folder)[KEEP_RESTORE_POINTS:]:
        (folder / old["file"]).unlink()
    return name


def restore_points(folder):
    """Restore points in `folder`, newest first, by the time in their names."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = RESTORE_POINT.match(path.name)
        if not match:
            continue
        try:
            saved = time.mktime(time.strptime(match.group(1), "%Y%m%d-%H%M%S"))
        except ValueError:
            continue    # a name shaped like ours but not a real time is not ours
        found.append((saved, int(match.group(2) or 1), path.name))
    return [{"file": name, "saved_at": iso(saved)} for saved, _, name in sorted(found, reverse=True)]


def restore(cfg, folder, file, app_version, now, connection=True):
    """Put back a restore point, every group, after saving one of the present settings.

    With connection False (a sender who is not the owner), a restore that would
    change where the hub sends the screen's colours is refused, before anything changes.
    """
    name = Path(file).name
    if not RESTORE_POINT.match(name) or not (Path(folder) / name).is_file():
        raise FileNotFoundError(f"no restore point named {name!r}")
    report = plan(cfg, parse((Path(folder) / name).read_bytes()))
    if not connection and any(row["group"] == "connection" and row["status"] in ("change", "adjusted")
                              for row in report["rows"]):
        raise OwnerOnly("this restore changes the board connection")
    return apply(cfg, report, GROUPS, folder, app_version, now, exact=True)


def iso(when):
    """Local time with a +05:30-style offset, which every browser's Date can read."""
    return datetime.fromtimestamp(when).astimezone().isoformat(timespec="seconds")


class Pending:
    """The one import a hub holds between its preview and its Apply."""

    def __init__(self, hold_s=HOLD_S):
        self.hold_s = hold_s
        self.lock = threading.Lock()
        self.held = None

    def hold(self, report, now):
        token = secrets.token_hex(8)
        with self.lock:
            self.held = (token, report, now + self.hold_s)
        return token

    def take(self, token, now):
        """The report held under `token`, once: an Apply cannot run twice."""
        with self.lock:
            held = self.held
            if held is None or now > held[2] or not hmac.compare_digest(held[0], str(token)):
                raise ValueError("that import has expired or was replaced; open the file again")
            self.held = None
        return held[1]
