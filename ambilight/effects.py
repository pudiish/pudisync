"""Standalone colour effects for a single-pixel analogue RGB strip.

An analogue strip is one light: every LED shows the same colour. Effects that
vary over SPACE (chase, scan, comet) have nowhere to travel and look dead, so
only TIME-varying effects are offered here.

Effect ids are from WLED's own FX.h, not guessed:
https://github.com/wled/WLED/blob/main/wled00/FX.h
"""

# (id, label, description, default speed, default intensity)
ANALOG_EFFECTS = [
    (9,  "Rainbow",   "Smooth cycle through every colour", 100, 128),
    (2,  "Breathe",   "Gentle fade in and out",             80, 128),
    (12, "Fade",      "Slow crossfade between two colours", 70, 128),
    (5,  "Random",    "Jumps to a new colour at random",   110, 128),
    (7,  "Dynamic",   "Shifting colour, never repeating",  100, 128),
    (45, "Fire",      "Warm flicker, like a candle",       130, 180),
    (57, "Lightning", "Occasional bright flashes",         110, 160),
    (0,  "Solid",     "One steady colour you pick",        128, 128),
]

EFFECT_IDS = {fx for fx, *_ in ANALOG_EFFECTS}


def catalogue():
    """Effects as the UI wants them."""
    return [{"fx": fx, "name": name, "about": about, "speed": sx, "intensity": ix}
            for fx, name, about, sx, ix in ANALOG_EFFECTS]


def is_analog_effect(fx):
    return fx in EFFECT_IDS


def state_for(fx, speed=None, intensity=None, color=None, brightness=None):
    """A WLED state payload that runs one effect on segment 0."""
    segment = {"id": 0, "fx": int(fx)}
    if speed is not None:
        segment["sx"] = max(0, min(255, int(speed)))
    if intensity is not None:
        segment["ix"] = max(0, min(255, int(intensity)))
    if color:
        segment["col"] = [list(color)]
    payload = {"on": True, "seg": [segment]}
    if brightness is not None:
        payload["bri"] = max(0, min(255, int(brightness)))
    return payload
