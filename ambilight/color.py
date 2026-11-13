"""Colour grading: screen light in, LED-ready colour out.

Modelled on HyperHDR's float pipeline. Averages arrive in *linear* light (what
the screen actually emits). Saturation works in HSV on gamma-encoded values,
so a boost can never push a channel out of range and shift the hue. The
colour is then converted into the strip's own primaries (with a warmer white
point for neutrals), trimmed by the strip's channel balance, and given the LED
gamma: WS2812B brightness is linear in its PWM value, so sending the screen's
sRGB code values straight through makes dark greys glow far brighter on the
wall than on the screen.
"""
from functools import lru_cache

import numpy as np

from . import enrich

# Luma weights (Rec. 709), for "how bright does this look".
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def srgb_to_linear(values):
    v = np.asarray(values, dtype=np.float32)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(values):
    v = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055).astype(np.float32)


# 8-bit sRGB code value -> linear light, built once.
SRGB_LUT = srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)

# Linear sRGB -> CIE XYZ (D65).
SRGB_TO_XYZ = np.array([[0.4124, 0.3576, 0.1805],
                        [0.2126, 0.7152, 0.0722],
                        [0.0193, 0.1192, 0.9505]])

# WS2812B primaries per the WorldSemi datasheet: CIE 1931 xy of the centre
# wavelength, and luminous intensity (mcd) at full drive.
WS2812B_PRIMARIES = ((0.7006, 0.2993, 405.0),   # red   620-630nm, 390-420mcd
                     (0.0743, 0.8338, 690.0),   # green 515-530nm, 660-720mcd
                     (0.1241, 0.0578, 190.0))   # blue  465-475nm, 180-200mcd


# Bradford cone response, the standard chromatic-adaptation transform.
BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                     [-0.7502, 1.7135, 0.0367],
                     [0.0389, -0.0685, 1.0296]])
D65_XY = (0.3127, 0.3290)
NEUTRAL_KELVIN = 6500   # at or above this, the strip keeps the screen's own D65 white


def blackbody_xy(kelvin):
    """CIE 1931 xy of a blackbody (Kim et al. cubic fit, valid 1667-25000K)."""
    t = float(kelvin)
    if t <= 4000:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.240390
    if t <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def white_xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def adaptation(kelvin):
    """XYZ -> XYZ that moves the screen's D65 white to a blackbody white."""
    if kelvin >= NEUTRAL_KELVIN:
        return np.eye(3)
    source = BRADFORD @ white_xyz(D65_XY)
    target = BRADFORD @ white_xyz(blackbody_xy(kelvin))
    return np.linalg.inv(BRADFORD) @ np.diag(target / source) @ BRADFORD


@lru_cache(maxsize=16)
def srgb_adaptation(kelvin):
    """The warmth adaptation expressed on linear sRGB, for a measured matrix."""
    return (np.linalg.inv(SRGB_TO_XYZ) @ adaptation(kelvin) @ SRGB_TO_XYZ).astype(np.float32)


@lru_cache(maxsize=16)
def led_matrix(kelvin, primaries=WS2812B_PRIMARIES):
    """Linear screen light -> linear LED channel light (Philips' XYZ route).

    The strip's primaries are not the screen's: its blue is relatively bright,
    so driving it 1:1 turns warm colours and white bluish-white. Going through
    CIE XYZ asks each LED channel for exactly the light that reproduces the
    screen colour, seen under a `kelvin` white instead of the screen's D65.
    Scaled so white puts the busiest channel at full.
    """
    columns = [np.array([x * lum / y, lum, (1 - x - y) * lum / y]) for x, y, lum in primaries]
    matrix = np.linalg.solve(np.column_stack(columns), adaptation(kelvin) @ SRGB_TO_XYZ)
    return (matrix / (matrix @ np.ones(3)).max()).astype(np.float32)


def smoothstep(lo, hi, x):
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def rgb_to_hsv(rgb):
    rgb = np.asarray(rgb, dtype=np.float32)
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    delta = mx - mn
    s = np.where(mx > 0, delta / np.maximum(mx, 1e-12), 0.0)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    safe = np.maximum(delta, 1e-12)
    h = np.where(mx == r, ((g - b) / safe) % 6.0,
                 np.where(mx == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0)) / 6.0
    h = np.where(delta > 0, h, 0.0)
    return np.stack([h, s, mx], axis=1).astype(np.float32)


def hsv_to_rgb(hsv):
    h, s, v = hsv[:, 0] * 6.0, hsv[:, 1], hsv[:, 2]
    sector = np.floor(h).astype(np.int64) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    choices = [
        np.stack([v, t, p], 1), np.stack([q, v, p], 1), np.stack([p, v, t], 1),
        np.stack([p, q, v], 1), np.stack([t, p, v], 1), np.stack([v, p, q], 1),
    ]
    out = np.zeros_like(hsv)
    for k, c in enumerate(choices):
        out[sector == k] = c[sector == k]
    return out


def grade(linear, cfg):
    """Linear per-LED averages -> perceptual (sRGB-encoded) 0..1 colours.

    The saturation boost is a *vibrance*: real colours get it, near-greys and
    near-blacks do not, so compression noise in a dark scene cannot be blown up
    into garish colour. Very dark colours are eased toward neutral for the same
    reason.
    """
    hsv = rgb_to_hsv(linear_to_srgb(np.asarray(linear, dtype=np.float32)))
    sat, val = hsv[:, 1], hsv[:, 2]

    gain = cfg["saturation"]
    if gain > 1.0:
        protect = smoothstep(0.06, 0.25, sat) * smoothstep(0.06, 0.25, val)
        sat = np.minimum(1.0, sat * (1.0 + (gain - 1.0) * protect))
    else:
        sat = sat * gain
    sat = sat * smoothstep(0.02, 0.10, val)

    hsv[:, 1] = sat
    graded = np.clip(hsv_to_rgb(hsv), 0.0, 1.0)
    amount = cfg.get("immersion", 0.0)
    if amount > 0:
        graded = immerse(graded, amount)
    return enrich.apply(graded, cfg.get("enrich", 0.0))


IMMERSE_LIFT = 0.55     # at full immersion a colourful LED's level is raised to the power 0.45
IMMERSE_PURITY = 1.5    # ...and its remaining greyness shrinks to the power 2.5
IMMERSE_FLOOD = 0.4     # dull LEDs move this far toward the scene's colour
HUE_FAMILIES = 12       # 30-degree hue slices; the theme is the strongest one and its neighbours


def skin(hue, sat):
    """1 for skin and other soft warm tones, which a stronger look should leave alone."""
    warm = smoothstep(0.0, 0.03, hue) * (1.0 - smoothstep(0.12, 0.15, hue))
    return warm * (1.0 - smoothstep(0.55, 0.75, sat))


def immerse(perceptual, amount):
    """Push one frame's colours into the scene's colour, the way a theatre floods a stage.

    Deep shades (dark red, maroon, navy) are what LED gamma and contrast crush
    most, so colourful LEDs are lifted more the darker they are, and made
    purer. Then dull or dark LEDs are pulled toward the scene's own colour, so
    a red scene fills the room with red. Greys, skin and black are left alone,
    and a scene without a real colour (or with one small light) is not flooded.
    """
    hsv = rgb_to_hsv(perceptual)
    hue, sat, level = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    colourful = smoothstep(0.15, 0.5, sat) * (1.0 - skin(hue, sat))
    hsv[:, 2] = level ** (1.0 - IMMERSE_LIFT * amount * colourful)
    hsv[:, 1] = 1.0 - (1.0 - sat) ** (1.0 + IMMERSE_PURITY * amount * colourful)
    lifted = hsv_to_rgb(hsv)

    presence = colourful * level
    colour, strength = theme(lifted, hue, presence)
    if colour is None:
        return lifted
    pull = IMMERSE_FLOOD * amount * strength * (1.0 - presence)
    return np.clip(lifted + (colour - lifted) * pull[:, None], 0.0, 1.0)


def colourful_presence(perceptual):
    """(hue, how much each LED shows a real colour): saturated and lit, skin excluded."""
    hsv = rgb_to_hsv(perceptual)
    hue, sat, level = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    return hue, smoothstep(0.15, 0.5, sat) * (1.0 - skin(hue, sat)) * level


def theme(colours, hue, presence):
    """The scene's colour: (mean of its strongest colour family, 0..1 strength), or (None, 0).

    The strongest family, not the average of all of them: half red, half blue
    is a red or a blue scene, never a purple one. The strength stays low unless
    the family is a real share of the frame, not one small light in the dark.
    """
    family = (hue * HUE_FAMILIES).astype(np.int64) % HUE_FAMILIES
    mass = np.bincount(family, weights=presence, minlength=HUE_FAMILIES)
    peak = int(np.argmax(mass + np.roll(mass, 1) + np.roll(mass, -1)))
    apart = (family - peak + HUE_FAMILIES // 2) % HUE_FAMILIES - HUE_FAMILIES // 2
    members = presence * (np.abs(apart) <= 1)
    if members.sum() <= 0:
        return None, 0.0
    colour = (colours * members[:, None]).sum(axis=0) / members.sum()
    return colour, float(smoothstep(0.05, 0.25, members.mean()))


def contrast_curve(level, amount):
    """S-curve on 0..1 drive levels: brights up, darks down, ends fixed.

    amount 1.0 leaves levels unchanged; 1.5 lifts an 82% level to 91% and drops
    a 12% level to 5%.
    """
    if abs(amount - 1.0) < 1e-3:
        return level
    level = np.clip(level, 0.0, 1.0)
    up, down = level ** amount, (1.0 - level) ** amount
    return up / np.maximum(up + down, 1e-12)


def to_led(perceptual, cfg):
    """Perceptual colours -> 0..255 LED drive levels (float, not yet quantized).

    Below `black_level` the light fades out rather than glowing on noise.
    """
    p = np.clip(np.asarray(perceptual, dtype=np.float32), 0.0, 1.0)
    black = cfg["black_level"]
    if black > 0:
        p = p * smoothstep(black * 0.5, black, p @ LUMA)[:, None]
    # Strip balance: this strip's own channel strengths (its green runs strong
    # next to the datasheet's average), trimmed in LED light so every colour
    # scales alike.
    balance = np.array([cfg["wb_r"], cfg["wb_g"], cfg["wb_b"]], dtype=np.float32)
    if not cfg["colour_match"]:
        return contrast_curve(p ** cfg["led_gamma"], cfg["contrast"]) * balance * 255.0

    linear = srgb_to_linear(p)
    # A webcam-measured matrix (camcal.py) beats the datasheet model when present.
    fitted = cfg.get("led_matrix") or []
    neutral = np.array(fitted, np.float32).reshape(3, 3) if len(fitted) == 9 else None
    light = linear @ (neutral if neutral is not None else led_matrix(NEUTRAL_KELVIN)).T
    kelvin = int(cfg["white_kelvin"])
    if kelvin < NEUTRAL_KELVIN:
        # Warmth is for whites, greys and skin. The eye stays adapted to the
        # screen, so saturated colours keep the screen's own look; warming a
        # sky blue toward the warm white would only make it pale.
        warmer = (neutral @ srgb_adaptation(kelvin) if neutral is not None
                  else led_matrix(kelvin))
        warm = linear @ warmer.T
        share = 1.0 - smoothstep(0.35, 0.75, rgb_to_hsv(p)[:, 1:2])
        light = light + (warm - light) * share
    light = np.clip(light, 0.0, None) * balance
    peak = light.max(axis=1, keepdims=True)
    # The matrix sets only the mix of the three channels. Brightness keeps the
    # unmatched curve, taken from the screen colour itself, so a saturated
    # red is as bright on the wall as it is on screen.
    level = contrast_curve(p.max(axis=1, keepdims=True) ** cfg["led_gamma"], cfg["contrast"])
    return light * (level / np.maximum(peak, 1e-12)) * 255.0
