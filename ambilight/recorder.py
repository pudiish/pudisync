"""Record what the screen fed the lights, and replay it through the colour path.

Tuning by eye on live video is unrepeatable: the scene is gone before the
setting is judged. A recording keeps the per-LED linear averages (tiny next to
the frames themselves) with their timestamps, so a dark film scene or a hard
cut can be replayed through grading and smoothing as often as needed.
"""
import time
from pathlib import Path

import numpy as np

from . import color

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"


def new_path():
    RECORDINGS_DIR.mkdir(exist_ok=True)
    return RECORDINGS_DIR / time.strftime("rec-%Y%m%d-%H%M%S.npz")


class Recorder:
    def __init__(self, path, seconds, now):
        self.path = Path(path)
        self.until = now + seconds
        self.times = []
        self.frames = []

    def add(self, now, linear):
        """Keep one frame. Returns False once the recording's time is up."""
        if now > self.until:
            return False
        self.times.append(now)
        self.frames.append(np.asarray(linear, dtype=np.float32).copy())
        return True

    def save(self):
        np.savez_compressed(self.path, times=np.array(self.times, dtype=np.float64),
                            frames=np.array(self.frames, dtype=np.float32))
        return self.path


def replay(path, cfg):
    """Run a recording through grading and smoothing. Returns (bytes, stats)."""
    from .engine import OutputStage   # the engine imports this module

    data = np.load(path)
    times, frames = data["times"], data["frames"]
    stage = OutputStage()
    # The real output path, safety layer included; zones do not apply to a
    # recording of the sync layer alone.
    replay_cfg = dict(cfg, zones=[], led_count=frames.shape[1] if frames.ndim == 3 else 0)
    out = []
    for now, linear in zip(times, frames):
        stage.set_target(color.grade(linear, cfg), now)
        out.append(stage.frame(now, replay_cfg))
    out = np.array(out)

    steps = np.abs(np.diff(out.astype(np.int16), axis=0)) if len(out) > 1 else np.zeros((0,))
    lit = out.max(axis=2) > 0
    stats = {
        "frames": int(len(out)),
        "seconds": float(times[-1] - times[0]) if len(times) > 1 else 0.0,
        "flicker": float(steps.mean()) if steps.size else 0.0,
        "largest_step": int(steps.max()) if steps.size else 0,
        "on_off_toggles": int(np.abs(np.diff(lit.astype(np.int8), axis=0)).sum())
        if len(out) > 1 else 0,
    }
    return out, stats
