"""The Ambient screen's presets: looks picked by feel, made of existing settings.

TV ambient lighting and light-sync apps offer a few named looks instead of
sliders: how colourful the light is, how fast it follows the picture, a glow
for the room when the screen is not followed, and how closely lights round
the room follow. These tables give the phone page and the Mac app the same
choices. Applying one goes through the ordinary /api/set and /api/effect/run,
so nothing here is a setting of its own; docs/AMBIENT.md maps each to its source.
"""

# How colourful the light is while it follows the screen. Balanced is the
# hub's own default (config.DEFAULTS), so a fresh install shows it as chosen.
STYLES = (
    {"name": "true", "label": "True", "about": "The screen's colours as they are.",
     "set": {"saturation": 1.0, "immersion": 0.0}},
    {"name": "balanced", "label": "Balanced", "about": "A little richer than the screen.",
     "set": {"saturation": 1.2, "immersion": 0.0}},
    {"name": "bold", "label": "Bold", "about": "Strong colour for daylight and cartoons.",
     "set": {"saturation": 1.6, "immersion": 0.3}},
    {"name": "immersive", "label": "Immersive", "about": "Floods the room with the scene's colour.",
     "set": {"saturation": 1.3, "immersion": 0.8}},
)

# How fast the light follows ("blend", the Response setting). Smooth is the
# Cinema profile's pace and Instant is Gaming's, so those profiles light them too.
PACES = (
    {"name": "gentle", "label": "Gentle", "about": "Drifts slowly between colours.", "set": {"blend": 0.15}},
    {"name": "smooth", "label": "Smooth", "about": "Glides with the picture.", "set": {"blend": 0.3}},
    {"name": "quick", "label": "Quick", "about": "Keeps up with cuts.", "set": {"blend": 0.5}},
    {"name": "instant", "label": "Instant", "about": "Follows every flash.", "set": {"blend": 0.7}},
)

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


def tables():
    """Every preset, as the clients read them."""
    return {"styles": list(STYLES), "paces": list(PACES), "glows": list(GLOWS), "bed": list(BED_FOLLOW)}
