"""LED safety: protect the strip, its 5V supply and the people watching.

Runs on every output frame, after zones and brightness, before quantizing:

- power budget: the estimated current never exceeds `power_limit_ma`. Over
  budget the frame scales down at once; it comes back over RELEASE_S, so the
  limit glides instead of pumping. WLED's own limiter stays configured as a
  hardware-side backstop.
- heat: a bright scene held for minutes (a white page, a paused frame) settles
  to `sustained_ma`, so the strip and a budget adapter do not run hot at their
  peak rating. Short bright scenes are untouched.
- anti-strobe: WCAG 2.3.1 allows at most three flashes a second. Past that
  many reversals of large whole-strip swings, output is damped until the
  flashing stops.
"""
import math
from collections import deque

import numpy as np

# WLED's WS2812B estimate: 55mA per LED at full white, so a third per channel.
MA_PER_CHANNEL = 55.0 / 3.0
IDLE_MA_PER_LED = 1.0       # a dark WS2812B still powers its driver chip
RELEASE_S = 0.8             # brightness returns over this long after a power limit
HEAT_TAU_S = 120.0          # heat builds and fades over minutes, not frames
STROBE_SWING = 0.10         # a whole-strip jump of 10% of full counts as a flash (WCAG)
STROBE_WINDOW_S = 1.0
STROBE_MAX = 3              # flashes allowed per window (WCAG 2.3.1)
STROBE_DAMP = 0.15          # while strobing, output moves this share toward each frame


def estimate_ma(levels):
    """Approximate strip current for 0..255 drive levels, idle draw included."""
    levels = np.asarray(levels, dtype=np.float32)
    return float(levels.sum()) / 255.0 * MA_PER_CHANNEL + len(levels) * IDLE_MA_PER_LED


class Guard:
    def __init__(self):
        self.power_factor = 1.0
        self.heat_ma = 0.0
        self.last_time = None
        self.last_mean = None
        self.swing_direction = 0
        self.shown = None
        self.flashes = deque()
        self.status = {"power_ma": 0, "power_limited": False,
                       "heat_limited": False, "strobe_damped": False}

    def apply(self, levels, now, cfg):
        """Returns the levels that are safe to send now."""
        levels = np.asarray(levels, dtype=np.float32)
        dt = 0.0 if self.last_time is None else max(0.0, now - self.last_time)
        self.last_time = now

        strobing = self.watch_strobe(levels, now)
        if strobing and self.shown is not None and self.shown.shape == levels.shape:
            levels = self.shown + (levels - self.shown) * STROBE_DAMP

        idle = len(levels) * IDLE_MA_PER_LED
        active = max(estimate_ma(levels) - idle, 0.0)
        room = max(cfg["power_limit_ma"] - idle, 0.0)
        needed = min(1.0, room / active) if active > 0 else 1.0
        if needed < self.power_factor:
            self.power_factor = needed
        else:
            self.power_factor = min(needed, self.power_factor + dt / RELEASE_S)

        # Heat follows what the power budget lets through, so a long bright
        # scene converges on sustained_ma exactly.
        through = active * self.power_factor
        self.heat_ma += (through - self.heat_ma) * (1.0 - math.exp(-dt / HEAT_TAU_S))
        heat_room = max(cfg["sustained_ma"] - idle, 0.0)
        heat_factor = min(1.0, heat_room / self.heat_ma) if self.heat_ma > heat_room else 1.0

        out = levels * min(self.power_factor, heat_factor)
        self.shown = out
        self.status = {"power_ma": round(estimate_ma(out)),
                       "power_limited": self.power_factor < 0.999,
                       "heat_limited": heat_factor < 0.999,
                       "strobe_damped": strobing}
        return out

    def watch_strobe(self, levels, now):
        mean = float(levels.mean()) / 255.0
        if self.last_mean is not None and abs(mean - self.last_mean) > STROBE_SWING:
            # A flash is a change of direction (WCAG counts opposing changes):
            # a cut spread over a few frames is one change, not several flashes.
            direction = 1 if mean > self.last_mean else -1
            if direction != self.swing_direction:
                self.flashes.append(now)
                self.swing_direction = direction
        self.last_mean = mean
        while self.flashes and now - self.flashes[0] > STROBE_WINDOW_S:
            self.flashes.popleft()
        return len(self.flashes) > STROBE_MAX

    def state(self):
        return dict(self.status)
