"""Temporal smoothing: how the light moves between frames.

A critically damped spring per LED channel (HyperHDR's default is a damped
spring too), integrated exactly, so the motion is the same whatever the frame
rate. On top of it:

- a colour with far to go (or every colour, in a scene cut) snaps most of
  the way in under a tenth of a second, while small drifts glide slowly;
- brightness may only climb so fast, so no frame jumps straight to full
  (the idea behind Philips' perceptual rate limit);
- the 8-bit output only moves when the colour has really moved, so a slow fade
  cannot flicker between two neighbouring levels.
"""
import numpy as np

from .color import LUMA

SPRING_SETTLE = 5.8     # a critically damped spring has ~2% left after 5.8/omega
# Tuned on a 20s live recording: 0.12 fired 30 times (1.5/s) on busy footage
# and made the strip jump; 0.3 fires on real cuts only (4) with 40% fewer
# large output steps.
CUT_CHANGE = 0.3        # mean per-LED change (0..1) that marks a scene cut
CUT_HOLD_S = 0.3        # a cut keeps the snapping pace this long
# A colour that has far to go snaps there, then glides the last stretch; a
# small drift glides all the way. Lag shows on big changes, flicker on small.
SNAP_FROM, SNAP_FULL = 0.15, 0.4    # distance (0..1) where snapping starts / is complete
SNAP_S = 0.08           # settle time while snapping
RISE_PER_S = 2.5        # max brightness climb per second while gliding (full range in 0.4s)
SNAP_RISE_PER_S = 10.0  # ...and while snapping (full in 0.1s; never full in one frame)
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

    def snap_share(self, offset, now):
        """Per LED, 0 (glide) to 1 (snap): by how far it still has to go, or 1 in a cut."""
        if self.cutting(now):
            return np.ones(len(offset), dtype=np.float32)
        t = np.clip((np.abs(offset).max(axis=1) - SNAP_FROM) / (SNAP_FULL - SNAP_FROM), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

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
        offset = self.pos - self.target
        snap = self.snap_share(offset, now)
        settle = settle_s * (min(SNAP_S, settle_s) / max(settle_s, 1e-3)) ** snap
        omega = (SPRING_SETTLE / np.maximum(settle, 1e-3))[:, None]

        # Exact solution of x'' + 2wx' + w^2 x = 0 over dt, per LED: stable at any frame rate.
        drive = self.vel + omega * offset
        decay = np.exp(-omega * dt)
        new_pos = self.target + (offset + drive * dt) * decay
        new_vel = (self.vel - omega * drive * dt) * decay

        climb = (new_pos - self.pos) @ LUMA
        allowed = (RISE_PER_S + (SNAP_RISE_PER_S - RISE_PER_S) * snap) * dt
        over = climb > allowed
        if over.any():
            scale = (allowed[over] / climb[over])[:, None]
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
