"""Webcam calibration: fit the strip's colour from pictures of the screen and wall.

A webcam sees the monitor and the glow around it. The monitor shows test
colours while the strip is driven with colours chosen independently, and each
picture gives two measurements taken through the same sensor at the same
moment: the screen's colour and the wall's.

Webcams change exposure and white balance all the time. Within one picture
those act as the same per-channel gain on both regions, so only the *ratio*
wall/screen per channel is used, and the gains cancel. Per channel k:

    wall_k / screen_k = (m_k . drive + q_k . screen + b_k) / (a_k . screen + c_k)

where a, c describe how the screen looks to the camera, m how the strip lights
the wall, q how much screen light spills onto the wall, and b ambient light.
Cross-multiplied this is linear and homogeneous in (a, c, m, q, b), so each
channel is one SVD. Each channel is only known up to a scale, but that scale
is shared by a_k and m_k, so it cancels in the answer we need:

    F = M^-1 A    (screen light -> strip drive that makes the wall match)

F replaces the datasheet matrix in colour.to_led.
"""
import numpy as np

from . import color

SATURATED = 250     # camera code value at which a channel is treated as clipped
DARK = 3            # ...and below which it is lost in sensor noise
MIN_EQUATIONS = 14  # 11 unknowns per channel, plus a margin

# Screen test colours (sRGB code values): neutrals, primaries, secondaries and
# the colours people care about most on a wall (skin, warm, teal).
SCREEN_COLOURS = [
    (255, 255, 255), (180, 180, 180), (120, 120, 120), (255, 0, 0), (0, 255, 0),
    (0, 0, 255), (0, 255, 255), (255, 0, 255), (255, 255, 0), (224, 172, 138),
    (255, 140, 40), (140, 60, 255), (0, 170, 160), (255, 200, 90),
]


def patch_plan(seed=1, count=36):
    """(screen colour, strip drive) pairs, with drives independent of the screen.

    Independence is what lets the fit tell the strip's own light apart from
    screen light spilling onto the wall.
    """
    rng = np.random.default_rng(seed)
    pairs = []
    for i in range(count):
        screen = SCREEN_COLOURS[i % len(SCREEN_COLOURS)]
        drive = tuple(int(v) for v in rng.integers(60, 256, 3))
        pairs.append((screen, drive))
    return pairs


def linear(code_values):
    return color.srgb_to_linear(np.asarray(code_values, dtype=np.float64) / 255.0).astype(np.float64)


def fit_matrix(samples):
    """samples: (screen code, drive bytes, screen as seen by camera, wall as seen by camera).

    Returns the 3x3 matrix F (screen linear light -> strip linear drive),
    scaled so white drives the busiest channel at full.
    """
    rows_a, rows_m = [], []
    for k in range(3):
        equations = channel_equations(samples, k)
        if len(equations) < MIN_EQUATIONS:
            raise ValueError("too many camera readings were blown out or too dark; "
                             "lower the camera's exposure or move it further back")
        solution = np.linalg.svd(np.array(equations))[2][-1]
        rows_a.append(solution[0:3])
        rows_m.append(solution[4:7])
    A, M = np.array(rows_a), np.array(rows_m)
    F = np.linalg.solve(M, A)
    if np.linalg.cond(M) > 1e3 or (F @ np.ones(3)).max() <= 0:
        raise ValueError("the measurements do not determine the strip's colours; "
                         "check the camera sees both the screen and the glow")
    return F / (F @ np.ones(3)).max()


def channel_equations(samples, k):
    """One linear equation per usable picture for channel k (see module docstring)."""
    equations = []
    for screen_code, drive, screen_cam, wall_cam in samples:
        # A clipped channel says only "at least this bright": leave it out.
        if max(screen_cam[k], wall_cam[k]) >= SATURATED or min(screen_cam[k], wall_cam[k]) <= DARK:
            continue
        s, d = linear(screen_code), np.asarray(drive, np.float64) / 255.0
        seen_s, seen_w = linear(screen_cam)[k], linear(wall_cam)[k]
        row = np.concatenate([seen_w * s, [seen_w], -seen_s * d, -seen_s * s, [-seen_s]])
        equations.append(row / max(seen_s + seen_w, 1e-6))   # every picture counts alike
    return equations


def mismatch(pairs):
    """How far the wall's colour is from the screen's, ignoring brightness.

    pairs: (screen as seen by camera, wall as seen by camera), code values.
    0 means the wall is a (dimmer or brighter) copy of the screen's colour.
    """
    scores = []
    for screen_cam, wall_cam in pairs:
        ratio = np.log(np.maximum(linear(wall_cam), 1e-4) / np.maximum(linear(screen_cam), 1e-4))
        scores.append(np.abs(ratio - ratio.mean()).mean())
    return float(np.mean(scores)) if scores else 0.0


def grow(mask, radius):
    """Dilate a boolean mask by `radius` pixels (square), numpy only."""
    out = mask.copy()
    padded = np.pad(mask, radius)
    h, w = mask.shape
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            out |= padded[dy:dy + h, dx:dx + w]
    return out


def find_regions(dark, screen_lit, strip_lit, share=0.35):
    """Masks of the screen and the wall glow from three camera pictures:
    everything dark, only the screen lit, only the strip lit."""
    # In light, not code values: gamma encoding would make faint screen spill
    # on the wall look a third as bright as the screen itself.
    lum = lambda img: linear(img).mean(axis=2)
    screen_gain = lum(screen_lit) - lum(dark)
    strip_gain = lum(strip_lit) - lum(dark)
    screen = screen_gain > share * screen_gain.max()
    margin = max(2, min(screen.shape) // 40)
    wall = (strip_gain > share * strip_gain.max()) & ~grow(screen, margin)
    return screen, wall


def region_colour(image, mask):
    return np.asarray(image, np.float64)[mask].mean(axis=0)
