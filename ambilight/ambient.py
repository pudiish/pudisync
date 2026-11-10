"""The Ambient screen's choices: picked by feel, made of what the hub already has.

TV ambient lighting and light-sync apps offer a few named choices instead of
sliders: a mode for following the picture, a glow for the room when the screen
is not followed, and how closely lights round the room follow. These tables
give the phone page and the Mac app the same choices. A sync mode is one of
the built-in profiles (config.PROFILES, applied by /api/profile/<name>); the
rest go through the ordinary /api/set and /api/effect/run, so nothing here is
a setting of its own. docs/AMBIENT.md maps each to its source.
"""
from .config import PROFILES

# The two ways of following the screen are the built-in profiles themselves;
# only their names on screen live here, so the profiles own every value.
MODE_TEXT = {
    "movie": ("Cinema", "Glides gently between colours, for films and series."),
    "game": ("Gaming", "Follows the action quickly, for games and sport."),
}

# Light for the room when the screen is not followed, run on the board itself,
# so it keeps going while the Mac sleeps. Effect ids are from effects.EFFECTS
# (0 is WLED's plain Solid colour).
GLOWS = (
    {"name": "ember", "label": "Ember", "about": "Flickering reds and oranges.",
     "fx": 88, "color": "ff4010", "speed": 96, "intensity": 224},
    {"name": "deep-sea", "label": "Deep sea", "about": "Slow ocean blues and greens.",
     "fx": 101, "color": None, "speed": 100, "intensity": 128},
    {"name": "forest", "label": "Forest", "about": "A green that breathes slowly.",
     "fx": 2, "color": "30d050", "speed": 40, "intensity": 128},
    {"name": "warm-white", "label": "Warm white", "about": "A steady lamp-like glow.",
     "fx": 0, "color": "ffb46b", "speed": 128, "intensity": 128},
    {"name": "cool-white", "label": "Cool white", "about": "A steady daylight glow.",
     "fx": 0, "color": "dfe8ff", "speed": 128, "intensity": 128},
)

# How closely the bed strip follows the screen: one mood colour, the nearest
# corner easing into the mood, or a whole edge of the screen.
BED_FOLLOW = (
    {"name": "off", "label": "Off", "about": "The bed strip stays dark.", "set": {"bed_mode": "off"}},
    {"name": "loosely", "label": "Loosely", "about": "One slow colour: the scene's strongest.",
     "set": {"bed_mode": "screen", "bed_look": "mood"}},
    {"name": "closely", "label": "Closely", "about": "The nearest corner, easing into the mood.",
     "set": {"bed_mode": "screen", "bed_look": "blend"}},
    {"name": "exactly", "label": "Exactly", "about": "The screen's edges stretched along the bed.",
     "set": {"bed_mode": "screen", "bed_look": "side", "bed_side": "whole"}},
)


def modes():
    """The sync modes, one per built-in profile, in the profiles' order."""
    return [{"name": name, "label": MODE_TEXT[name][0], "about": MODE_TEXT[name][1]} for name in PROFILES]


def tables():
    """Every choice, as the clients read them."""
    return {"modes": modes(), "glows": list(GLOWS), "bed": list(BED_FOLLOW)}
