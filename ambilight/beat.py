"""Bass beats from the Mac's sound, and the motion they put into the strip.

BassMeter hears the kick and bass line in the system audio ScreenCaptureKit
delivers: every 10 ms hop it measures the energy between 40 and 160 Hz, and a
beat is a sharp rise in that energy (spectral flux, the standard onset cue)
above an adaptive threshold, the last second's mean plus 1.5 deviations. A
quiet film and a loud track both work because the threshold follows the music.
Audio stays in memory: a few hops of samples, never stored or sent.

The motion is built to feel locked in without strobing. WCAG 2.3.1 allows three
flashes a second and the safety guard damps more, so a beat is never a
whole-strip flash: Bounce sends a bright wave of the scene's own colour outward
from the middle of the screen, Shake jolts the colours sideways and lets them
settle. Beats are at least REFRACTORY_S apart, so at most three a second.
"""
import math
from collections import deque

import numpy as np

RATE = 48000                # ScreenCaptureKit's audio rate
HOP = 512                   # ~10.7 ms per step
WINDOW = 2048               # ~43 ms of sound per spectrum, enough to resolve 40 Hz
BASS_HZ = (40.0, 160.0)     # kick drum and bass line
HISTORY_S = 1.0             # the threshold follows this much recent flux
SENSITIVITY = 1.5           # a beat stands this many deviations above the recent mean
MIN_RISE = 0.08             # ...and rises at least this much (log10 energy), so hiss is no beat
SILENCE = -4.0              # log10 bass energy (a full-scale bass note is about +2.4) under which nothing counts
REFRACTORY_S = 0.35         # beats at least this far apart: never more than three a second

PULSE_S = 0.28              # a beat's energy fades over this long
WAVE_SPEED = 160.0          # LEDs per second the bounce wave travels outward
WAVE_WIDTH = 6.0            # LEDs either side of the wave front
WAVE_GAIN = 1.2             # how much brighter the wave makes what it passes, at full Bounce
SHAKE_LEDS = 3.0            # how far a full Shake jolts the colours
SHAKE_HZ = 9.0              # how fast they wobble back
SHAKE_S = 0.16              # how quickly the wobble settles


class BassMeter:
    """Beats in a stream of mono audio. Thread-safe enough for one writer and one reader."""

    def __init__(self, rate=RATE):
        self.rate = rate
        self._buffer = np.zeros(WINDOW, np.float32)
        self._pending = np.zeros(0, np.float32)
        self._window = np.hanning(WINDOW).astype(np.float32)
        freqs = np.fft.rfftfreq(WINDOW, 1.0 / rate)
        self._bins = (freqs >= BASS_HZ[0]) & (freqs <= BASS_HZ[1])
        self._flux = deque(maxlen=max(4, int(HISTORY_S * rate / HOP)))
        self._last_loud = None
        self.last_beat = -math.inf
        self.strength = 0.0
        self.beats = 0

    def feed(self, samples, now):
        """Take mono samples that ended arriving at `now` (seconds); returns how many beats they held."""
        samples = np.asarray(samples, np.float32).ravel()
        data = np.concatenate([self._pending, samples])
        hops = len(data) // HOP
        found = 0
        for i in range(hops):
            hop = data[i * HOP:(i + 1) * HOP]
            self._buffer = np.concatenate([self._buffer[HOP:], hop])
            # When this hop finished, by the clock the caller gave for the block's end.
            at = now - (len(data) - (i + 1) * HOP) / self.rate
            found += self._step(at)
        self._pending = data[hops * HOP:]
        return found

    def _step(self, at):
        spectrum = np.fft.rfft(self._buffer * self._window)
        energy = float(np.sum(np.abs(spectrum[self._bins]) ** 2)) / WINDOW
        loud = math.log10(energy + 1e-12)
        rise = 0.0 if self._last_loud is None else max(0.0, loud - self._last_loud)
        self._last_loud = loud
        history = np.asarray(self._flux, np.float32)
        self._flux.append(rise)
        if len(history) < 4 or loud < SILENCE or at - self.last_beat < REFRACTORY_S:
            return 0
        threshold = max(MIN_RISE, float(history.mean() + SENSITIVITY * history.std()))
        if rise <= threshold:
            return 0
        self.last_beat = at
        self.strength = min(1.0, rise / (2.0 * threshold))
        self.beats += 1
        return 1

    def pulse(self, now):
        """0..1: the last beat's energy, fading over PULSE_S."""
        age = now - self.last_beat
        return self.strength * math.exp(-age / (PULSE_S / 3.0)) if 0.0 <= age < PULSE_S * 2 else 0.0


def bounce(levels, centre, age, strength, depth):
    """A wave of the strip's own colour running outward from `centre` LED, `age` s after a beat."""
    if depth <= 0 or strength <= 0 or age < 0 or age >= PULSE_S * 2 or not len(levels):
        return levels
    front = WAVE_SPEED * age
    distance = np.abs(np.arange(len(levels), dtype=np.float32) - centre)
    fade = math.exp(-age / (PULSE_S / 3.0))
    wave = np.exp(-((distance - front) / WAVE_WIDTH) ** 2) * (fade * strength * depth)
    # Brighter where the wave passes, and dark LEDs take the scene's colour, so it reads as the scene, not a flash.
    scene = levels.mean(axis=0, keepdims=True)
    return levels * (1.0 + WAVE_GAIN * wave[:, None]) + scene * wave[:, None]


def shake(levels, age, strength, depth, ring=False):
    """The colours jolted sideways and settling back, `age` s after a beat."""
    if depth <= 0 or strength <= 0 or age < 0 or age >= SHAKE_S * 4 or len(levels) < 2:
        return levels
    offset = SHAKE_LEDS * depth * strength * math.exp(-age / SHAKE_S) * math.sin(2 * math.pi * SHAKE_HZ * age)
    positions = np.arange(len(levels), dtype=np.float32) - offset
    if ring:
        positions %= len(levels)
    else:
        positions = np.clip(positions, 0, len(levels) - 1)
    low = np.floor(positions).astype(np.int64)
    high = (low + 1) % len(levels) if ring else np.minimum(low + 1, len(levels) - 1)
    mix = (positions - low)[:, None]
    return levels[low] * (1.0 - mix) + levels[high] * mix
