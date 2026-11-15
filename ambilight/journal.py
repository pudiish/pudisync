"""An append-only record of settings changes, one JSON line each.

It answers "what was the look when this clip was measured?": every line names
the build and the old and new value of each setting that changed. The PIN is
never written, and large values are noted as changed rather than copied.
"""
import json
import logging
import os
from datetime import datetime

from . import version

log = logging.getLogger(__name__)

MAX_BYTES = 1_000_000       # past this the file is kept as .1 and a fresh one starts
HIDDEN = {"pin"}
SUMMARISED = {"zones", "led_matrix", "user_profiles"}


def record(path, changes, event="change", max_bytes=MAX_BYTES):
    """Append one line for `changes`, {setting: (old, new)}. Never raises: a full disk must not stop the lights."""
    shown = {key: "changed" if key in HIDDEN | SUMMARISED else list(pair) for key, pair in changes.items()}
    line = json.dumps({"t": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "build": version.build_id(), "event": event, "changed": shown})
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.replace(path, f"{path}.1")
        with open(path, "a", encoding="utf-8") as journal:
            journal.write(line + "\n")
    except OSError as exc:
        log.warning("could not write the settings journal %s: %s", path, exc)
