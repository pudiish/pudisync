"""The Enriched look: blacks that stay black, highlights that reach full, true colour.

Lightness is reshaped in Oklab (Ottosson 2020), a perceptual space built so
that changing lightness leaves hue alone, unlike HSV or per-channel curves,
which push a dark orange toward red. Each colour keeps its hue and its
saturation (chroma relative to lightness): it only gets darker or lighter.
A colour pushed past what the LEDs can show is brought back by giving up
chroma, never by clipping a channel, which would shift its hue.

The conversions come from colour-science, the reference Python colour library,
not hand-copied matrices.
"""
import colour
import numpy as np

TONE = 1.35        # S-curve strength on Oklab lightness at full Enriched (1 = none)
GAMUT_STEPS = 8    # halvings of the chroma range when a colour lands outside the LEDs' gamut
SLACK = 1e-4       # conversion round-off that still counts as inside the gamut


def to_oklab(perceptual):
    return colour.XYZ_to_Oklab(colour.sRGB_to_XYZ(perceptual))


def from_oklab(lab):
    return colour.XYZ_to_sRGB(colour.Oklab_to_XYZ(lab))


def tone(lightness, amount):
    """Darks down, lights up, mid-grey and both ends fixed."""
    level = np.clip(lightness, 0.0, 1.0)
    up, down = level ** TONE, (1.0 - level) ** TONE
    curved = up / np.maximum(up + down, 1e-12)
    return lightness + (curved - lightness) * amount


def outside(rgb):
    return ((rgb < -SLACK) | (rgb > 1.0 + SLACK)).any(axis=1)


def apply(perceptual, amount):
    """sRGB-encoded 0..1 colours -> the same colours with Enriched tone, `amount` 0..1."""
    perceptual = np.asarray(perceptual, dtype=np.float32)
    if amount <= 0:
        return perceptual
    lab = to_oklab(perceptual.astype(np.float64))
    lightness = lab[:, 0]
    toned = tone(lightness, amount)
    keep = np.where(lightness > 1e-6, toned / np.maximum(lightness, 1e-6), 0.0)
    lab = np.column_stack([toned, lab[:, 1] * keep, lab[:, 2] * keep])
    rgb = from_oklab(lab)

    rows = np.flatnonzero(outside(rgb))
    if rows.size:
        # Largest share of the chroma that still fits, by bisection; grey always fits.
        low, high = np.zeros(rows.size), np.ones(rows.size)
        for _ in range(GAMUT_STEPS):
            middle = (low + high) / 2.0
            trial = lab[rows] * np.column_stack([np.ones(rows.size), middle, middle])
            fits = ~outside(from_oklab(trial))
            low, high = np.where(fits, middle, low), np.where(fits, high, middle)
        rgb[rows] = from_oklab(lab[rows] * np.column_stack([np.ones(rows.size), low, low]))
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)
