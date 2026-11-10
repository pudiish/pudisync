"""A short, curated list of WLED effects for an addressable (WS2812B) strip.

WLED ships well over a hundred effects; the UI offers these as one-tap tiles
(calm ones first, since this is bias lighting) and keeps the full list behind
"More effects". Ids were read from this strip's WLED 16.0.1 (/json/effects),
not guessed; they match WLED's FX.h.
"""

# (id, label, description, default speed, default intensity)
EFFECTS = [
    (63,  "Pride",      "Slow, rich rainbow that drifts",      128, 128),
    (67,  "Colorwaves", "Soft waves of shifting colour",       100, 128),
    (38,  "Aurora",     "Northern-lights ribbons",             90, 128),
    (101, "Pacifica",   "Calm ocean blues and greens",         100, 128),
    (75,  "Lake",       "Gentle rippling water",               80, 128),
    (2,   "Breathe",    "One colour fading in and out",        80, 128),
    (88,  "Candle",     "Warm, flickering candlelight",        96, 224),
    (66,  "Fire",       "Flames rising up the strip",          130, 160),
    (80,  "Twinkle",    "Stars fading in and out",             100, 128),
    (97,  "Plasma",     "Flowing colour clouds",               100, 128),
    (9,   "Rainbow",    "Classic rainbow cycle",               100, 128),
    (76,  "Meteor",     "Bright streaks shooting round",       128, 128),
]


def catalogue():
    """Effects as the UI wants them."""
    return [{"fx": fx, "name": name, "about": about, "speed": sx, "intensity": ix}
            for fx, name, about, sx, ix in EFFECTS]


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
