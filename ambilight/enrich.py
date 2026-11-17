"""The Enriched look: blacks that stay black, highlights that reach full, true colour.

Lightness is reshaped in Oklab (Ottosson 2020), a perceptual space built so
that changing lightness leaves hue alone, unlike HSV or per-channel curves,
which push a dark orange toward red. Each colour keeps its hue and its
saturation (chroma relative to lightness): it only gets darker or lighter.
A colour pushed past what the LEDs can show is brought back by giving up
chroma, never by clipping a channel, which would shift its hue.

The conversions use colour-science's own constants (sRGB per IEC 61966-2-1,
Oklab per Ottosson), copied verbatim; tests/oklab_reference.npz pins the
results to that library's output. Importing the library itself cost the hub
~3 s of start-up and ~86 MB of memory for these two functions.
"""
import numpy as np

TONE = 1.35        # S-curve strength on Oklab lightness at full Enriched (1 = none)
# The pivot sits a little under the frame's average lightness: sinking costs
# more LED light than the same lift adds (light grows faster than lightness),
# and a vivid colour's lift is capped by the gamut, so at the average itself a
# bright frame came out dimmer. At 0.9 bright frames keep their level.
PIVOT_SHARE = 0.9
PIVOT_MIN, PIVOT_MAX = 0.15, 0.85   # keeps a near-black or near-white frame's curve from going flat on one side
GAMUT_STEPS = 8    # halvings of the chroma range when a colour lands outside the LEDs' gamut
SLACK = 1e-4       # conversion round-off that still counts as inside the gamut


_RGB_TO_XYZ = np.array([[0.4124, 0.3576, 0.1805],
                        [0.2126, 0.7152, 0.0722],
                        [0.0193, 0.1192, 0.9505]])
_XYZ_TO_RGB = np.array([[3.2406, -1.5372, -0.4986],
                        [-0.9689, 1.8758, 0.0415],
                        [0.0557, -0.2040, 1.0570]])
_XYZ_TO_LMS = np.array([[0.8189330101, 0.3618667424, -0.1288597137],
                        [0.0329845436, 0.9293118715, 0.0361456387],
                        [0.0482003018, 0.2643662691, 0.6338517070]])
_LMS_TO_LAB = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                        [1.9779984951, -2.4285922050, 0.4505937099],
                        [0.0259040371, 0.7827717662, -0.8086757660]])
_LAB_TO_LMS = np.array([[0.9999999984505196, 0.3963377921737678, 0.21580375806075877],
                        [1.0000000088817607, -0.10556134232365633, -0.0638541747717059],
                        [1.0000000546724108, -0.08948418209496574, -1.2914855378640917]])
_LMS_TO_XYZ = np.array([[1.2270138511035211, -0.5577999806518222, 0.2812561489664678],
                        [-0.0405801784232806, 1.11225686961683, -0.07167667866560119],
                        [-0.0763812845057069, -0.4214819784180127, 1.5861632204407947]])
# One matrix per direction: the linear steps around the sRGB curve fold together.
_LINEAR_TO_LMS = _XYZ_TO_LMS @ _RGB_TO_XYZ
_LMS_TO_LINEAR = _XYZ_TO_RGB @ _LMS_TO_XYZ
_SRGB_KNEE = 0.0031308


def _spow(x, power):
    """Sign-preserving power, so out-of-gamut negatives survive the round trip."""
    return np.sign(x) * np.abs(x) ** power


def to_oklab(perceptual):
    v = np.asarray(perceptual, dtype=np.float64)
    linear = np.where(v <= _SRGB_KNEE * 12.92, v / 12.92, _spow((v + 0.055) / 1.055, 2.4))
    return _spow(linear @ _LINEAR_TO_LMS.T, 1 / 3) @ _LMS_TO_LAB.T


def from_oklab(lab):
    linear = _spow(np.asarray(lab, dtype=np.float64) @ _LAB_TO_LMS.T, 3) @ _LMS_TO_LINEAR.T
    return np.where(linear <= _SRGB_KNEE, linear * 12.92, 1.055 * _spow(linear, 1 / 2.4) - 0.055)


def tone(lightness, amount):
    """Darker than the frame's own average goes down, lighter goes up; both ends stay put.

    The pivot follows the frame, not mid-grey: most of a film sits below
    mid-grey, and a fixed pivot sank whole scenes rather than shaping them.
    """
    level = np.clip(lightness, 0.0, 1.0)
    pivot = float(np.clip(PIVOT_SHARE * level.mean(), PIVOT_MIN, PIVOT_MAX)) if level.size else 0.5
    below = pivot * (level / pivot) ** TONE
    above = 1.0 - (1.0 - pivot) * ((1.0 - level) / (1.0 - pivot)) ** TONE
    curved = np.where(level < pivot, below, above)
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
