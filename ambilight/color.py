"""Colour grading: screen light in, LED-ready colour out.

Modelled on HyperHDR's float pipeline. Averages arrive in *linear* light (what
the screen actually emits); white balance works there. Saturation then works
in HSV on gamma-encoded values, so a boost can never push a channel out of
range and shift the hue. Last comes the LED gamma: WS2812B brightness is
linear in its PWM value, so sending the screen's sRGB code values straight
through makes dark greys glow far brighter on the wall than on the screen.
"""
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


def to_led(perceptual, cfg):
    """Perceptual colours -> 0..255 LED drive levels (float, not yet quantized).

    Below `black_level` the light fades out rather than glowing on noise.
    """
    p = np.clip(np.asarray(perceptual, dtype=np.float32), 0.0, 1.0)
    black = cfg["black_level"]
    if black > 0:
        p = p * smoothstep(black * 0.5, black, p @ LUMA)[:, None]
    return (p ** cfg["led_gamma"]) * 255.0
