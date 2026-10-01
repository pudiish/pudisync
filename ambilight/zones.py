"""Zones: contiguous LED regions, each rendering in its own mode.

A zone owns [start, end] of the strip and one mode. Rendering is vectorised per
zone; the engine composites them into one frame. Zones are the unit the UI's
timeline bar drags around.
"""
import time

import numpy as np

MODES = ("sync", "solid", "warm", "off")

# Zones render on top of the screen-sync frame, so a strip with no zones at all
# behaves exactly as before.
DEFAULT_ZONE = {"start": 0, "end": -1, "mode": "sync", "color": "#ffb46b",
                "brightness": 1.0}


def normalize(zones, led_count):
    """Clamp, sort and clip zones so they never overlap or run off the strip."""
    clean = []
    for z in zones or []:
        start = max(0, min(led_count - 1, int(z.get("start", 0))))
        end = int(z.get("end", -1))
        end = led_count - 1 if end < 0 else max(0, min(led_count - 1, end))
        if end < start:
            start, end = end, start
        mode = z.get("mode", "sync")
        if mode not in MODES:
            mode = "sync"
        clean.append({
            "start": start, "end": end, "mode": mode,
            "color": _hex(z.get("color", "#ffb46b")),
            "brightness": max(0.0, min(1.0, float(z.get("brightness", 1.0)))),
        })
    clean.sort(key=lambda z: z["start"])
    # Later zones win any overlap, so trim the earlier one's tail.
    for i in range(len(clean) - 1):
        if clean[i]["end"] >= clean[i + 1]["start"]:
            clean[i]["end"] = clean[i + 1]["start"] - 1
    return [z for z in clean if z["end"] >= z["start"]]


def _hex(value):
    text = str(value).strip()
    if not text.startswith("#"):
        text = "#" + text
    if len(text) != 7:
        return "#ffb46b"
    try:
        int(text[1:], 16)
    except ValueError:
        return "#ffb46b"
    return text.lower()


def rgb(hex_color):
    h = _hex(hex_color)
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)


def composite(sync_frame, zones, led_count, now=None):
    """Lay each zone's own rendering over the screen-sync frame."""
    out = sync_frame
    if not zones:
        return out
    out = out.copy()
    now = time.time() if now is None else now
    for z in zones:
        lo, hi = z["start"], min(z["end"], led_count - 1)
        if hi < lo:
            continue
        width = hi - lo + 1
        mode, bri = z["mode"], z["brightness"]
        if mode == "sync":
            out[lo:hi + 1] *= bri
        elif mode == "solid":
            out[lo:hi + 1] = rgb(z["color"]) * bri
        elif mode == "warm":
            # A lamp-like warm white, independent of the screen.
            out[lo:hi + 1] = np.array([255, 170, 95], dtype=np.float32) * bri
        elif mode == "off":
            out[lo:hi + 1] = 0.0
    return out
