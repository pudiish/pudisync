#!/usr/bin/env python3
"""How visible a bass beat is on the strip: the audio handoff's section 2 table.

    python3 tools/beat_visibility.py [repo root]

For a bright, a mid and a dark even-coloured scene at Pulse settings on 240
LEDs, one full-strength beat is run through the real output stage (bounce,
shake, zones, brightness, the safety guard and quantizing) at 60 frames a
second and compared with the same frames without it. Reported per scene:

- LEDs changed: at the strongest frame, LEDs whose level moved by more than 10%
  of their own level (and at least one step of 255).
- Biggest change: the largest move of any LED's channel, of 255.
- Lasts: how long at least a twentieth of the strongest frame's changed LEDs
  stay changed.
- Mean swing: the largest frame-to-frame move of the whole strip's average, as
  a share of full; the guard counts 10% as a flash.

Pass another checkout's root to measure its code with the same method, which
is how a change's before-and-after table is made. No sound and no network.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ambilight import beat, color, engine  # noqa: E402
from ambilight.config import DEFAULTS, PROFILES  # noqa: E402

LEDS = 240
FPS = 60
SCENES = {"Bright": 143, "Mid": 58, "Dark": 2}
WARM = np.array([1.0, 0.62, 0.3], np.float32)   # an even warm colour, as a lamp-lit room


def settings():
    return {**DEFAULTS, **PROFILES["pulse"], "led_count": LEDS, "left_led": 0, "right_led": LEDS - 1,
            "fade_leds": 0, "layout": "strip", "zones": []}


def target_for(level, cfg):
    """The even perceptual colour whose brightest channel reaches `level` of 255 after to_led."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        got = float(color.to_led(np.tile(WARM * mid, (LEDS, 1)), cfg).max())
        lo, hi = (mid, hi) if got < level else (lo, mid)
    return np.tile(WARM * hi, (LEDS, 1))


def run(target, cfg, beat_on):
    stage = engine.OutputStage()
    stage.meter = beat.BassMeter()
    stage.meter.last_beat, stage.meter.strength = (1.0, 1.0) if beat_on else (-1e9, 0.0)
    stage.set_target(target, 0.0)
    # Settle the smoother and the guard first, then follow the beat for a second.
    for i in range(FPS):
        stage.frame(i / FPS * 0.9, cfg)
    return [stage.frame(1.0 + i / FPS, cfg).astype(np.float32) for i in range(FPS)]


def measure(level, cfg):
    target = target_for(level, cfg)
    with_beat, without = run(target, cfg, True), run(target, cfg, False)
    changed, biggest = [], 0.0
    for a, b in zip(with_beat, without):
        move = np.abs(a - b).max(axis=1)
        changed.append(int(np.sum((move > 0.1 * b.max(axis=1)) & (move >= 1.0))))
        biggest = max(biggest, float(move.max()))
    peak = max(changed)
    lasting = [i for i, n in enumerate(changed) if peak and n >= max(1, peak / 20)]
    lasts_ms = (lasting[-1] - lasting[0] + 1) * 1000 / FPS if lasting else 0
    means = [float(f.mean()) / 255 for f in with_beat]
    swing = max(abs(b - a) for a, b in zip(means, means[1:]))
    return {"level": round(float(without[0].max())), "changed": peak, "biggest": round(biggest),
            "lasts_ms": round(lasts_ms), "swing": round(swing, 3)}


def main():
    cfg = settings()
    print(f"Beat visibility, Pulse settings, {LEDS} LEDs, code at {ROOT}")
    print("| Scene | Level | LEDs changed >10% | Biggest change (of 255) | Lasts | Max frame swing |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, level in SCENES.items():
        m = measure(level, cfg)
        print(f"| {name} | {m['level']} | {m['changed']} | {m['biggest']} | {m['lasts_ms']} ms | {m['swing']:.1%} |")


if __name__ == "__main__":
    main()
