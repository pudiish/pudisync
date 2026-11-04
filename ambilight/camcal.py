"""Webcam calibration: match the wall's colour to the screen's, picture by picture.

A webcam sees the monitor and the glow around it. Webcams re-expose and
re-balance all the time and blow out whichever light is brightest, so no
reading is trusted for brightness. Within one picture, though, the screen and
the wall pass through the same camera gains, so their colour *proportions*
compare fairly, and clipped pixels can simply be skipped: leaving pixels out
changes how bright a region looks, not its colour.

With white, red, green and blue on the screen in turn, the strip's drive is
nudged until the wall's proportions match the screen's. The matched primaries
and white fix a 3x3 matrix F (screen linear light -> strip drive), the way
monitors are calibrated. F replaces the datasheet matrix in colour.to_led.
"""
import numpy as np

from . import color

CLIP = 235          # camera code value from which a pixel's colour is unreliable
FLOOR = 12          # ...and below which it is sensor noise
MIN_PIXELS = 40     # fewer usable pixels than this and a region is not read
SCREEN_LEVEL = 0.5  # test colours at half code value: bright enough, rarely blown out
DRIVE_PEAK = 0.6    # the strip's busiest channel while matching (linear, 0..1)
PRIMARIES = {"white": (255, 255, 255), "red": (255, 0, 0),
             "green": (0, 255, 0), "blue": (0, 0, 255)}


def linear(code_values):
    return color.srgb_to_linear(np.asarray(code_values, dtype=np.float64) / 255.0).astype(np.float64)


def dimmed(colour):
    """A test colour at the level the webcam photographs without blowing out."""
    return tuple(round(v * SCREEN_LEVEL) for v in colour)


def proportions(image, mask):
    """Share of red, green and blue in a region's light, from its usable
    pixels only; None when too few pixels are neither blown out nor noise."""
    pixels = np.asarray(image, np.float64)[mask]
    brightest = pixels.max(axis=1)
    usable = pixels[(brightest < CLIP) & (brightest > FLOOR)]
    if len(usable) < MIN_PIXELS:
        return None
    light = linear(usable).sum(axis=0)
    return light / light.sum()


def mismatch(pairs):
    """How far the wall's colour is from the screen's, ignoring brightness.

    pairs: (screen proportions, wall proportions); unreadable pairs (None)
    are skipped. 0 means the wall shows the screen's colour.
    """
    scores = []
    for screen, wall in pairs:
        if screen is None or wall is None:
            continue
        ratio = np.log(np.maximum(wall, 1e-4) / np.maximum(screen, 1e-4))
        scores.append(np.abs(ratio - ratio.mean()).mean())
    if not scores:
        raise ValueError("no picture showed both the screen and the wall clearly")
    return float(np.mean(scores))


def first_guess(colour):
    """The datasheet model's drive for a screen colour, as a starting point.
    No channel starts at zero, or nudging could never bring it in."""
    drive = color.led_matrix(color.NEUTRAL_KELVIN) @ linear(colour)
    drive = np.maximum(drive, 0.03 * drive.max())
    return drive * DRIVE_PEAK / drive.max()


def nudge(drive, screen, wall, step=0.8, floor=0.02):
    """Move the strip's drive so the wall's proportions approach the screen's.
    `floor` keeps a channel that is nearly dark on both from swinging wildly."""
    drive = np.asarray(drive, np.float64) * ((screen + floor) / (wall + floor)) ** step
    return drive * DRIVE_PEAK / drive.max()


def matrix_from_primaries(matched):
    """F from the drives that matched white, red, green and blue.

    Each primary's drive is F's column for it, up to a scale; white, the sum
    of the primaries, fixes the three scales. Scaled so white drives the
    busiest channel at full.
    """
    columns = np.column_stack([matched[name] for name in ("red", "green", "blue")])
    scales = np.linalg.solve(columns, matched["white"])
    if (scales <= 0).any():
        raise ValueError("the matched colours are inconsistent (white is not a mix of "
                         "red, green and blue); check the camera sees the wall glow")
    F = columns * scales
    return F / (F @ np.ones(3)).max()


def grow(mask, radius):
    """Dilate a boolean mask by `radius` pixels (square), numpy only."""
    out = mask.copy()
    padded = np.pad(mask, radius)
    h, w = mask.shape
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            out |= padded[dy:dy + h, dx:dx + w]
    return out


def find_regions(screen_lit, strip_lit, screen_share=0.1, wall_share=0.25):
    """Masks of the screen and the wall glow from two camera pictures: only
    the screen lit, and only the strip lit.

    The webcam re-exposes between the two pictures, so each is scaled by its
    own brightest light before they are compared. A pixel belongs to whichever
    light makes it clearly brighter; the glossy screen reflecting the wall, or
    a wall lit by both, belongs to neither.
    """
    def light(img):
        # In light, not code values: gamma encoding would make faint screen
        # spill on the wall look a third as bright as the screen itself.
        lum = linear(img).mean(axis=2)
        return lum / max(np.percentile(lum, 99.5), 1e-6)

    from_screen, from_strip = light(screen_lit), light(strip_lit)
    screen = (from_screen > screen_share) & (from_screen > 2 * from_strip)
    margin = max(2, min(screen.shape) // 40)
    wall = (from_strip > wall_share) & (from_strip > 2 * from_screen) & ~grow(screen, margin)
    return screen, wall


def screen_edge(screen):
    """The screen's border band: the part the strip beside it follows, and
    away from the hot spot a webcam sees in the middle of a bright screen."""
    return screen & grow(~screen, max(2, min(screen.shape) // 25))


def looks_like_screen(screen, fill=0.6):
    """True when the mask is one solid (perspective-skewed) rectangle, not
    scattered patches of whatever the monitor happened to show."""
    rows, cols = np.flatnonzero(screen.any(axis=1)), np.flatnonzero(screen.any(axis=0))
    if not len(rows):
        return False
    box = (rows[-1] - rows[0] + 1) * (cols[-1] - cols[0] + 1)
    return screen.sum() / box >= fill
