"""Frame maths. Everything here is vectorised; no per-LED Python loops."""
import numpy as np

ROW_SAMPLES = 12  # sub-sampled rows: plenty of colour signal, a fraction of the work


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


def zone_colors(band, mapping):
    """Mean colour per in-zone LED from a sub-sampled band. Returns (span, 3) float32."""
    h = band.shape[0]
    step = max(1, h // ROW_SAMPLES)
    rows = band[::step]
    # Collapse rows first so the per-LED reduction works on a 1-D colour profile.
    profile = rows.mean(axis=0, dtype=np.float32)  # (width, 3)
    cumsum = np.concatenate(
        [np.zeros((1, 3), dtype=np.float32), np.cumsum(profile, axis=0, dtype=np.float32)]
    )
    starts, stops = mapping["starts"], mapping["stops"]
    totals = cumsum[stops] - cumsum[starts]
    widths = (stops - starts).astype(np.float32)[:, None]
    return totals / widths


def extend_edges(zone, low_w, high_w):
    """Prepend/append faded copies of the nearest zone-edge colour."""
    parts = []
    if low_w.size:
        parts.append(zone[0][None, :] * low_w[:, None])
    parts.append(zone)
    if high_w.size:
        parts.append(zone[-1][None, :] * high_w[:, None])
    return np.concatenate(parts, axis=0) if len(parts) > 1 else zone


def saturate(frame, amount):
    """Push colours away from their grey level. amount 1.0 is a no-op."""
    if abs(amount - 1.0) < 1e-3:
        return frame
    grey = frame.mean(axis=1, keepdims=True)
    return grey + (frame - grey) * amount


def finalize(frame, brightness):
    """Scale, clip, and cast to the uint8 bytes the wire format wants."""
    return np.clip(frame * brightness, 0, 255).astype(np.uint8)
