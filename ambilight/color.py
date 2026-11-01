"""Colour grading: screen light in, LED-ready colour out.

Modelled on HyperHDR's float pipeline. Averages arrive in *linear* light (what
the screen actually emits); white balance works there. Saturation then works
in HSV on gamma-encoded values, so a boost can never push a channel out of
range and shift the hue. Last comes the LED gamma: WS2812B brightness is
linear in its PWM value, so sending the screen's sRGB code values straight
through makes dark greys glow far brighter on the wall than on the screen.
"""
from functools import lru_cache

import numpy as np

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
    balanced = np.asarray(linear, dtype=np.float32) * np.array(
        [cfg["wb_r"], cfg["wb_g"], cfg["wb_b"]], dtype=np.float32)
    hsv = rgb_to_hsv(linear_to_srgb(balanced))
    sat, val = hsv[:, 1], hsv[:, 2]

    gain = cfg["saturation"]
    if gain > 1.0:
        protect = smoothstep(0.06, 0.25, sat) * smoothstep(0.06, 0.25, val)
        sat = np.minimum(1.0, sat * (1.0 + (gain - 1.0) * protect))
    else:
        sat = sat * gain
    sat = sat * smoothstep(0.02, 0.10, val)

    hsv[:, 1] = sat
    return np.clip(hsv_to_rgb(hsv), 0.0, 1.0)


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
    if not cfg["colour_match"]:
        return contrast_curve(p ** cfg["led_gamma"], cfg["contrast"]) * 255.0

    linear = srgb_to_linear(p)
    light = linear @ led_matrix(NEUTRAL_KELVIN).T
    kelvin = int(cfg["white_kelvin"])
    if kelvin < NEUTRAL_KELVIN:
        # Warmth is for whites, greys and skin. The eye stays adapted to the
        # screen, so saturated colours keep the screen's own look; warming a
        # sky blue toward the warm white would only make it pale.
        warm = linear @ led_matrix(kelvin).T
        share = 1.0 - smoothstep(0.35, 0.75, rgb_to_hsv(p)[:, 1:2])
        light = light + (warm - light) * share
    light = np.clip(light, 0.0, None)
    peak = light.max(axis=1, keepdims=True)
    # The matrix sets only the mix of the three channels. Brightness keeps the
    # unmatched curve, taken from the screen colour itself, so a saturated
    # red is as bright on the wall as it is on screen.
    level = contrast_curve(p.max(axis=1, keepdims=True) ** cfg["led_gamma"], cfg["contrast"])
    return light * (level / np.maximum(peak, 1e-12)) * 255.0
