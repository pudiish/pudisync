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
    """Clamp, sort and resolve overlaps so zones always describe a valid strip.

    Later zones win overlaps. An overlap that falls in the middle of an earlier
    zone splits it in two rather than truncating it, so putting a gap inside a
    lamp leaves lamp on both sides -- what the user drew, not a lost tail.

    Anything unparseable is dropped rather than raising: these values arrive
    from the network (iOS Shortcuts, curl) and must never crash the engine.
    """
    if not isinstance(zones, (list, tuple)):
        return []
    led_count = max(1, int(led_count))

    clean = []
    for z in zones:
        if not isinstance(z, dict):
            continue
        try:
            start = int(z.get("start", 0))
            end = int(z.get("end", -1))
        except (TypeError, ValueError):
            continue        # a zone we cannot read is a zone we ignore
        # A negative end means "to the end of the strip"; negative start does not.
        end = led_count - 1 if end < 0 else end
        if start < 0:
            continue
        start = min(led_count - 1, start)
        end = max(0, min(led_count - 1, end))
        if end < start:
            start, end = end, start
        mode = z.get("mode", "sync")
        if mode not in MODES:
            mode = "sync"
        try:
            brightness = float(z.get("brightness", 1.0))
        except (TypeError, ValueError):
            brightness = 1.0
        clean.append({
            "start": start, "end": end, "mode": mode,
            "color": _hex(z.get("color", "#ffb46b")),
            "brightness": max(0.0, min(1.0, brightness)),
        })

    return _resolve_overlaps(clean)


def _resolve_overlaps(zones):
    """Paint zones in order onto the strip; the last one to cover a LED wins.

    Working per-LED and rebuilding runs afterwards keeps every case consistent --
    partial overlap, full containment, and a zone buried under two others.
    """
    if not zones:
        return []
    span = max(z["end"] for z in zones) + 1
    owner = [-1] * span
    for index, z in enumerate(zones):
        for led in range(z["start"], z["end"] + 1):
            owner[led] = index

    out = []
    led = 0
    while led < span:
        index = owner[led]
        if index < 0:
            led += 1
            continue
        run_start = led
        while led < span and owner[led] == index:
            led += 1
        source = zones[index]
        out.append({**source, "start": run_start, "end": led - 1})
    return out


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
