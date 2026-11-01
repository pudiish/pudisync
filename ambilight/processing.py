"""Frame maths. Everything here is vectorised; no per-LED Python loops."""
import numpy as np

from . import color

ROW_SAMPLES = 12  # sub-sampled rows: plenty of colour signal, a fraction of the work

BLACK_LEVEL = 16        # brighter than this on any channel counts as picture
PICTURE_ROW_SHARE = 0.25  # a row is picture once this share of it is lit
MIN_PICTURE_SHARE = 0.5   # never trust a picture narrower than this
SEARCH_COL_STEP = 8


def find_picture(strip):
    """Where the real picture sits in a tall strip taken from the screen bottom.

    Returns (inset, left, right): how many rows of black bar lie under the
    picture, and the columns the picture spans. Video that does not match the
    monitor's shape leaves black bars, and sampling those would feed the strip
    black.

    A row only counts as picture when a good share of it is lit, so subtitles
    sitting in the bar do not read as picture. Detection runs on every 8th
    column to stay cheap.
    """
    height, width = strip.shape[:2]
    lit = strip[:, ::SEARCH_COL_STEP].max(axis=2) > BLACK_LEVEL     # (h, w/8)
    picture_rows = np.flatnonzero(lit.mean(axis=1) > PICTURE_ROW_SHARE)
    if picture_rows.size == 0:
        return 0, 0, width          # nothing lit: black screen, keep the default
    last = int(picture_rows[-1])
    inset = height - 1 - last

    # Side bars: judge the columns on the picture rows only, not the bar below.
    cols = np.flatnonzero(lit[picture_rows].any(axis=0))
    left = int(cols[0]) * SEARCH_COL_STEP
    right = min(width, (int(cols[-1]) + 1) * SEARCH_COL_STEP)
    if right - left < width * MIN_PICTURE_SHARE:
        left, right = 0, width
    return inset, left, right


DARK_SHARE = 0.08   # below this share of lit pixels a frame says nothing about its bars


def is_dark(image):
    """True when too little of the image is lit to judge where its bars are."""
    lit = image[::SEARCH_COL_STEP, ::SEARCH_COL_STEP].max(axis=2) > BLACK_LEVEL
    return float(lit.mean()) < DARK_SHARE


def find_frame(frame):
    """The picture's rectangle in a full-screen grab: (top, bottom, left, right).

    Same idea as find_picture, but for all four sides, so the lights can hug
    the picture whatever shape the video is. Falls back to the whole screen.
    """
    height, width = frame.shape[:2]
    step = SEARCH_COL_STEP
    lit = frame[::step, ::step].max(axis=2) > BLACK_LEVEL
    rows = np.flatnonzero(lit.mean(axis=1) > PICTURE_ROW_SHARE)
    cols = np.flatnonzero(lit.mean(axis=0) > PICTURE_ROW_SHARE)
    if rows.size == 0 or cols.size == 0:
        return 0, height, 0, width
    top, bottom = int(rows[0]) * step, min(height, (int(rows[-1]) + 1) * step)
    left, right = int(cols[0]) * step, min(width, (int(cols[-1]) + 1) * step)
    if (bottom - top < height * MIN_PICTURE_SHARE
            or right - left < width * MIN_PICTURE_SHARE):
        return 0, height, 0, width
    return top, bottom, left, right


def coverage(led_count, pitch_mm, side_mm):
    """Share of a screen edge that a side's LEDs physically span (0..1].

    A strip stuck inside the monitor's edge is shorter than the edge, so its
    LEDs see only the middle of it. Spreading them over the whole edge would
    stretch the colours.
    """
    return max(0.05, min(1.0, led_count * pitch_mm / side_mm))


def box_coverage(counts, pitch_mm, screen_w_mm, screen_h_mm):
    """Share of each screen edge (bottom, right, top, left) the strip spans.

    The strip is one rectangle centred behind the screen, so both upright
    sides span its full height even when one has an LED fewer (lost at a
    bend), and top matches bottom. Its size comes from the longer side of
    each pair; each side's LEDs then spread evenly over the whole side.
    """
    bottom, right, top, left = counts
    across = coverage(max(bottom, top), pitch_mm, screen_w_mm)
    upright = coverage(max(right, left), pitch_mm, screen_h_mm)
    return across, upright, across, upright


def slice_edges(frame, rect, thickness, skip=0):
    """The four edges of the picture rectangle, each `thickness` deep.

    `skip` drops the outermost pixels: the detected rectangle can be a few
    pixels off, and bar edges or monitor borders there would darken the colours.
    """
    top, bottom, left, right = rect
    top, bottom = top + skip, bottom - skip
    left, right = left + skip, right - skip
    t = int(min(thickness, (bottom - top) // 2, (right - left) // 2))
    return {
        "bottom": frame[bottom - t:bottom, left:right],
        "top": frame[top:top + t, left:right],
        "left": frame[top:bottom, left:left + t],
        "right": frame[top:bottom, right - t:right],
    }


def edge_colors(band, count, reverse=False, colour_weight=0.0):
    """Mean colour of `count` equal slices along a band's width, in LED order."""
    edges = np.linspace(0, band.shape[1], count + 1).astype(np.int64)
    starts, stops = edges[:-1], np.maximum(edges[1:], edges[:-1] + 1)
    if reverse:
        starts, stops = starts[::-1].copy(), stops[::-1].copy()
    return zone_colors(band, {"starts": starts, "stops": stops}, colour_weight)


def _centered(band, share):
    """The middle `share` of a band's width."""
    margin = int(band.shape[1] * (1.0 - share) / 2)
    return band[:, margin:band.shape[1] - margin] if margin else band


def frame_colors(edges, counts, coverage=(1.0, 1.0, 1.0, 1.0), colour_weight=0.0):
    """LED colours (linear light) for a strip wrapped round the monitor.

    The strip starts at the bottom-left corner and runs bottom, right, top,
    left. Vertical bands are turned on their side so the same slicing applies.
    `coverage` is the share of each edge (same order) the side's LEDs span.
    """
    bottom, right, top, left = counts
    cover_b, cover_r, cover_t, cover_l = coverage
    w = colour_weight
    parts = []
    if bottom:
        parts.append(edge_colors(_centered(edges["bottom"], cover_b), bottom, False, w))
    if right:      # bottom to top, but the band's rows run top to bottom
        band = _centered(edges["right"].swapaxes(0, 1), cover_r)
        parts.append(edge_colors(band, right, True, w))
    if top:        # right to left
        parts.append(edge_colors(_centered(edges["top"], cover_t), top, True, w))
    if left:       # top to bottom
        parts.append(edge_colors(_centered(edges["left"].swapaxes(0, 1), cover_l), left, False, w))
    return np.concatenate(parts) if parts else np.zeros((0, 3), dtype=np.float32)


def build_mapping(led_count, left_led, right_led, width):
    """Column slice bounds per in-zone LED, plus the fade weights for the rest.

    left_led/right_led are the LEDs physically behind the monitor's left and
    right edges. If left_led > right_led the strip is wired the other way, so
    the column order is simply reversed -- no separate reverse flag needed.
    """
    led_count = max(1, int(led_count))
    left_led = max(0, min(led_count - 1, int(left_led)))
    right_led = max(0, min(led_count - 1, int(right_led)))

    lo, hi = min(left_led, right_led), max(left_led, right_led)
    span = hi - lo + 1
    edges = np.linspace(0, width, span + 1).astype(np.int64)
    starts, stops = edges[:-1], np.maximum(edges[1:], edges[:-1] + 1)

    if left_led > right_led:
        # Strip runs right-to-left: LED lo is the screen's RIGHT edge.
        starts, stops = starts[::-1].copy(), stops[::-1].copy()

    return {
        "led_count": led_count,
        "zone_lo": lo,
        "zone_hi": hi,
        "starts": starts,
        "stops": stops,
        "width": width,
    }


def build_fade(mapping, fade_leds):
    """Weights for out-of-zone LEDs: 1.0 at the zone edge decaying to 0.

    Returns (low_weights, high_weights) covering the LEDs below zone_lo and
    above zone_hi respectively, each already in strip order.
    """
    fade_leds = max(0, int(fade_leds))
    lo, hi, n = mapping["zone_lo"], mapping["zone_hi"], mapping["led_count"]

    def ramp(length):
        if length <= 0:
            return np.zeros(0, dtype=np.float32)
        if fade_leds == 0:
            return np.zeros(length, dtype=np.float32)  # hard cutoff
        # Distance 1..length from the zone edge, clipped at fade_leds.
        dist = np.arange(1, length + 1, dtype=np.float32)
        return np.clip(1.0 - dist / (fade_leds + 1), 0.0, 1.0)

    low = ramp(lo)[::-1].copy()  # nearest the zone sits last in strip order
    high = ramp(n - 1 - hi)
    return low, high


CHROMA_WEIGHT = 6.0     # how strongly colour_weight=1 favours a saturated pixel


def zone_colors(band, mapping, colour_weight=0.0):
    """Mean colour per in-zone LED, in linear light 0..1. Returns (span, 3) float32.

    Averaging happens on the light the screen emits, not on sRGB code values:
    half black, half white is half the light, which reads far brighter than a
    code-value average of 128. With colour_weight > 0, saturated pixels count
    for more (Philips' "colour support" rule), so a coloured object on a grey
    or dark edge decides the LED instead of washing out into it.
    """
    h = band.shape[0]
    step = max(1, h // ROW_SAMPLES)
    rows = band[::step]
    linear = color.SRGB_LUT[rows]                       # (rows, width, 3)
    if colour_weight > 0:
        chroma = (rows.max(axis=2).astype(np.float32) - rows.min(axis=2)) / 255.0
        weight = 1.0 + colour_weight * CHROMA_WEIGHT * chroma
    else:
        weight = np.ones(rows.shape[:2], dtype=np.float32)
    # Collapse rows first so the per-LED reduction works on a 1-D profile.
    num = (linear * weight[..., None]).sum(axis=0)      # (width, 3)
    den = weight.sum(axis=0)                            # (width,)
    zero3 = np.zeros((1, 3), dtype=np.float32)
    cum_num = np.concatenate([zero3, np.cumsum(num, axis=0, dtype=np.float32)])
    cum_den = np.concatenate([[0.0], np.cumsum(den, dtype=np.float32)]).astype(np.float32)
    starts, stops = mapping["starts"], mapping["stops"]
    totals = cum_num[stops] - cum_num[starts]
    weights = (cum_den[stops] - cum_den[starts])[:, None]
    return totals / np.maximum(weights, 1e-6)


def spread(frame, amount, ring=False):
    """Let each LED share a little light with its neighbours.

    Neighbouring LEDs see neighbouring slices of the screen, so blending them
    hides the per-LED jitter of small moving details. `ring` joins the two ends,
    for a strip that runs all the way round the monitor.
    """
    if amount <= 0 or len(frame) < 2:
        return frame
    if ring:
        before, after = np.roll(frame, 1, axis=0), np.roll(frame, -1, axis=0)
    else:
        # Mirror at the ends, so the end LEDs keep their own light.
        before = np.concatenate([frame[:1], frame[:-1]])
        after = np.concatenate([frame[1:], frame[-1:]])
    return (frame * (1.0 - amount) + (before + after) * (amount / 2.0)).astype(np.float32)


def extend_edges(zone, low_w, high_w):
    """Prepend/append faded copies of the nearest zone-edge colour."""
    parts = []
    if low_w.size:
        parts.append(zone[0][None, :] * low_w[:, None])
    parts.append(zone)
    if high_w.size:
        parts.append(zone[-1][None, :] * high_w[:, None])
    return np.concatenate(parts, axis=0) if len(parts) > 1 else zone


def finalize(frame, brightness):
    """Scale, clip, and cast to the uint8 bytes the wire format wants."""
    return np.clip(frame * brightness, 0, 255).astype(np.uint8)
