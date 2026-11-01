"""Temporal smoothing: how the light moves between frames.

A critically damped spring per LED channel (HyperHDR's default is a damped
spring too), integrated exactly, so the motion is the same whatever the frame
rate. On top of it:

- scene cuts are detected and settle faster, while small drifts glide slowly;
- brightness may only climb so fast, so a flash cannot strobe the wall
  (the idea behind Philips' perceptual rate limit);
- the 8-bit output only moves when the colour has really moved, so a slow fade
  cannot flicker between two neighbouring levels.
"""
import math

import numpy as np

from .color import LUMA

SPRING_SETTLE = 5.8     # a critically damped spring has ~2% left after 5.8/omega
# Tuned on a 20s live recording: 0.12 fired 30 times (1.5/s) on busy footage
# and made the strip jump; 0.3 fires on real cuts only (4) with 40% fewer
# large output steps.
CUT_CHANGE = 0.3        # mean per-LED change (0..1) that marks a scene cut
CUT_HOLD_S = 0.3        # a cut keeps the faster pace this long
CUT_SPEEDUP = 0.7       # cuts settle in this share of the normal time
RISE_PER_S = 2.5        # max brightness climb per second (full range in 0.4s)
MAX_STEP_S = 0.1        # a stall must not fling the spring forward
HYSTERESIS = 1.0        # output levels a colour must move before the byte changes

# "Response" slider: 0 = glide (0.8s), 1 = instant (0.06s), log-spaced between.
SLOWEST_S, FASTEST_S = 0.8, 0.06


def settle_seconds(response):
    """Time for a change to settle, from the 0..1 Response setting."""
    response = min(1.0, max(0.0, float(response)))
    return FASTEST_S * (SLOWEST_S / FASTEST_S) ** (1.0 - response)


class Smoother:
    """Moves the shown colours toward the latest target, timed in seconds."""

    def __init__(self):
        self.pos = None
        self.vel = None
        self.target = None
        self.last_step = None
        self.cut_until = -1.0

    def set_target(self, target, now):
        target = np.asarray(target, dtype=np.float32)
        if self.target is not None and self.target.shape == target.shape:
            if float(np.abs(target - self.target).mean()) > CUT_CHANGE:
                self.cut_until = now + CUT_HOLD_S
        self.target = target

    def cutting(self, now):
        return now < self.cut_until

    def step(self, now, settle_s):
        """Advance to `now`. Returns the colours to show, or None before any target."""
        if self.target is None:
            return None
        if self.pos is None or self.pos.shape != self.target.shape:
            self.pos = self.target.copy()
            self.vel = np.zeros_like(self.pos)
            self.last_step = now
            return self.pos.copy()

        dt = min(max(now - self.last_step, 0.0), MAX_STEP_S)
        self.last_step = now
        settle = settle_s * (CUT_SPEEDUP if self.cutting(now) else 1.0)
        omega = SPRING_SETTLE / max(settle, 1e-3)

        # Exact solution of x'' + 2wx' + w^2 x = 0 over dt: stable at any frame rate.
        offset = self.pos - self.target
        drive = self.vel + omega * offset
        decay = math.exp(-omega * dt)
        new_pos = self.target + (offset + drive * dt) * decay
        new_vel = (self.vel - omega * drive * dt) * decay

        climb = (new_pos - self.pos) @ LUMA
        allowed = RISE_PER_S * dt
        over = climb > allowed
        if over.any():
            scale = (allowed / climb[over])[:, None]
            new_pos[over] = self.pos[over] + (new_pos[over] - self.pos[over]) * scale
            new_vel[over] *= scale

        self.pos = np.clip(new_pos, 0.0, 1.0)
        self.vel = new_vel
        return self.pos.copy()


class Quantizer:
    """Float drive levels -> bytes, holding each LED until it has really moved.

    All three channels commit together, so a slow fade keeps its hue instead
    of one channel stepping ahead of the others.
    """

    def __init__(self):
        self.held = None

    def apply(self, levels):
        levels = np.clip(np.asarray(levels, dtype=np.float32), 0.0, 255.0)
        rounded = np.rint(levels)
        if self.held is None or self.held.shape != levels.shape:
            self.held = rounded
        else:
            moved = np.abs(levels - self.held).max(axis=1) > HYSTERESIS
            self.held[moved] = rounded[moved]
        return self.held.astype(np.uint8)
