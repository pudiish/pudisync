"""Bass beats from the Mac's sound, and the motion they put into the strip.

BassMeter hears the kick and bass line in the system audio ScreenCaptureKit
delivers: every 10 ms hop it measures the energy between 40 and 160 Hz, and a
beat is a sharp rise in that energy (spectral flux, the standard onset cue)
above an adaptive threshold, the last second's mean plus 1.5 deviations. A
quiet film and a loud track both work because the threshold follows the music.
Audio stays in memory: a few hops of samples, never stored or sent.

The same spectrum also gives the shared feature frame (features()): low, mid
and high band levels, a bass envelope, onset strength, confidence and
freshness, each 0..1. Looks and Room Studio scenes read only that frame; none
opens its own sound or runs its own detector.

The motion is built to feel locked in without strobing. WCAG 2.3.1 allows three
flashes a second and the safety guard damps more, so a beat is never a
whole-strip flash: Bounce sends a wave of the scene's own colour outward from
the middle of the screen, Shake ripples brightness along the strip and lets it
settle. Beats are at least REFRACTORY_S apart, so at most three a second, and
the light a beat adds to the whole strip stays within BEAT_BUDGET.
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

# Pulse, Phase A of the audio handoff (2026-11-20): at the old ±6 LEDs and 0.28 s
# only a tenth of the strip moved for ~150 ms, and a dark scene moved by 2/255.
PULSE_S = 0.45              # a beat's energy fades over this long (was 0.28)
ATTACK_S = 0.03             # ...and arrives over this long, so no frame jumps
WAVE_SPEED = 160.0          # LEDs per second the bounce wave travels outward
WAVE_WIDTH = 18.0           # LEDs either side of the wave front (was 6)
WAVE_GAIN = 1.2             # how much brighter the wave makes what it passes, at full Bounce
# Dark scenes: the wave also adds the scene's own hue at this level (of 255), so a
# dark film scene glows on the beat. A frame with no light at all adds nothing:
# true black, black bars and a dark strip stay dark.
FLOOR_LEVEL = 36.0
BLACK_CUT = 0.5             # mean drive level (of 255) at or under which a frame is black
# The most light a beat may add to the whole strip, as a share of full white:
# under the guard's 10% flash swing, so the beat moves the strip without strobing.
BEAT_BUDGET = 0.08
SHAKE_DEPTH = 0.35          # a full Shake ripples brightness this far either way
SHAKE_WAVE = 24.0           # LEDs from one ripple crest to the next
SHAKE_HZ = 6.0              # how fast the ripple runs along the strip
SHAKE_S = 0.16              # how quickly the ripple settles

# The shared feature frame.
BANDS = {"low": (40.0, 160.0), "mid": (250.0, 2000.0), "high": (4000.0, 12000.0)}
BAND_RANGE = 2.5            # a band reads 0..1 over this many log10 steps (25 dB) under its recent peak
PEAK_FALL = 0.25            # log10 per second the remembered peak falls, so a quiet passage reads again
FRESH_S = 0.25              # features older than this are stale: consumers return to neutral
ENVELOPE_ATTACK_S = 0.03
ENVELOPE_RELEASE_S = 0.35
ONSET_RELEASE_S = 0.25
TEMPO_BEATS = 8             # beats a tempo estimate looks back over (reported only; nothing predicts)


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
        self._band_bins = {name: (freqs >= lo) & (freqs <= hi) for name, (lo, hi) in BANDS.items()}
        self._peaks = {name: None for name in BANDS}
        self._beat_times = deque(maxlen=TEMPO_BEATS + 1)
        self._envelope = 0.0
        self._onset = 0.0
        self._last_at = None
        self._features = neutral_features()

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
        power = np.abs(np.fft.rfft(self._buffer * self._window)) ** 2
        energy = float(np.sum(power[self._bins])) / WINDOW
        loud = math.log10(energy + 1e-12)
        rise = 0.0 if self._last_loud is None else max(0.0, loud - self._last_loud)
        self._last_loud = loud
        history = np.asarray(self._flux, np.float32)
        self._flux.append(rise)
        found = 0
        threshold = max(MIN_RISE, float(history.mean() + SENSITIVITY * history.std())) if len(history) else MIN_RISE
        if len(history) >= 4 and loud >= SILENCE and at - self.last_beat >= REFRACTORY_S and rise > threshold:
            self.last_beat = at
            self.strength = min(1.0, rise / (2.0 * threshold))
            self.beats += 1
            self._beat_times.append(at)
            found = 1
        self._publish(power, loud, rise / threshold if loud >= SILENCE else 0.0, at)
        return found

    def _publish(self, power, loud, onset, at):
        """Fold this hop into the shared feature frame, replaced whole so a reader never sees half of one."""
        dt = HOP / self.rate if self._last_at is None else max(0.0, at - self._last_at)
        self._last_at = at
        levels = {}
        for name, bins in self._band_bins.items():
            level = math.log10(float(np.sum(power[bins])) / WINDOW + 1e-12)
            peak = self._peaks[name]
            peak = level if peak is None else max(level, peak - PEAK_FALL * dt)
            self._peaks[name] = peak
            levels[name] = 0.0 if loud < SILENCE else clamp01((level - (peak - BAND_RANGE)) / BAND_RANGE)
        self._envelope = follow(self._envelope, levels["low"], dt, ENVELOPE_ATTACK_S, ENVELOPE_RELEASE_S)
        self._onset = follow(self._onset, clamp01(onset / 2.0), dt, 0.0, ONSET_RELEASE_S)
        self._features = {
            "valid": True,
            "at": at,
            "confidence": clamp01((loud - SILENCE) / 2.0),
            "low_energy": levels["low"],
            "mid_energy": levels["mid"],
            "high_energy": levels["high"],
            "bass_envelope": self._envelope,
            "onset_strength": self._onset,
        }

    def features(self, now):
        """The newest feature frame, or the neutral one when no sound has arrived for FRESH_S."""
        frame = self._features
        if not frame["valid"] or now - frame["at"] > FRESH_S:
            return neutral_features()
        return frame

    def fire(self, now, strength=1.0):
        """A synthetic beat (the Test beat button): the effects run as for a real one, and no sound is opened."""
        self.last_beat, self.strength = now, clamp01(strength)
        self.beats += 1

    def tempo(self):
        """Beats per minute from the recent beats' median gap, or None. Reported only; nothing fires early."""
        times = list(self._beat_times)
        if len(times) < 4:
            return None
        gap = float(np.median(np.diff(times)))
        return round(60.0 / gap) if 0.25 <= gap <= 1.5 else None

    def status(self, now):
        """What the page shows: the bass level, beats so far and the tempo."""
        features = self.features(now)
        return {"hearing": features["valid"], "bass_level": round(features["bass_envelope"], 3),
                "beats": self.beats, "bpm": self.tempo(), "last_beat_age": round(now - self.last_beat, 2)
                if math.isfinite(self.last_beat) else None}

    def pulse(self, now):
        """0..1: the last beat's energy, fading over PULSE_S."""
        return self.strength * envelope(now - self.last_beat)


def neutral_features():
    """No sound: every level 0 and marked invalid, so audio modulation eases back to none."""
    return {"valid": False, "at": -math.inf, "confidence": 0.0, "low_energy": 0.0, "mid_energy": 0.0,
            "high_energy": 0.0, "bass_envelope": 0.0, "onset_strength": 0.0}


def clamp01(value):
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


def follow(current, target, dt, attack_s, release_s):
    """One step of an attack/release envelope follower, its coefficients taken from the real dt."""
    tau = attack_s if target > current else release_s
    if tau <= 0:
        return float(target)
    return float(current + (target - current) * (1.0 - math.exp(-dt / tau)))


def envelope(age):
    """0..1: a beat's shape, `age` s after it; a short rise, then a fade over PULSE_S."""
    if age < 0 or age >= PULSE_S * 2:
        return 0.0
    rise = min(1.0, age / ATTACK_S)
    return rise * math.exp(-age / (PULSE_S / 3.0))


def bounce(levels, centre, age, strength, depth):
    """A wave of the strip's own colour running outward from `centre` LED, `age` s after a beat."""
    if depth <= 0 or strength <= 0 or age < 0 or age >= PULSE_S * 2 or not len(levels):
        return levels
    front = WAVE_SPEED * age
    distance = np.abs(np.arange(len(levels), dtype=np.float32) - centre)
    wave = np.exp(-((distance - front) / WAVE_WIDTH) ** 2) * (envelope(age) * strength * depth)
    # Brighter where the wave passes, and every LED it passes takes some of the scene's own hue at a
    # floor level, so a dark scene glows on the beat and it reads as the scene, not a flash.
    scene = levels.mean(axis=0, keepdims=True)
    peak = float(scene.max())
    floor = scene * (max(FLOOR_LEVEL, peak) / peak) if peak > BLACK_CUT else np.zeros_like(scene)
    added = levels * (WAVE_GAIN * wave[:, None]) + floor * wave[:, None]
    return levels + within_budget(added)


def within_budget(added):
    """Scale what an effect adds so the whole strip brightens by at most BEAT_BUDGET of full white."""
    mean = float(added.mean())
    limit = BEAT_BUDGET * 255.0
    return added * (limit / mean) if mean > limit else added


def shake(levels, age, strength, depth, ring=False):
    """Brightness rippling along the strip and settling, `age` s after a beat.

    A ripple rather than a sideways shift: shifting an even colour changed
    nothing, while a ripple shows on any lit scene and keeps the strip's
    average where it was, so it is never a flash.
    """
    if depth <= 0 or strength <= 0 or age < 0 or age >= SHAKE_S * 4 or len(levels) < 2:
        return levels
    amount = SHAKE_DEPTH * depth * strength * math.exp(-age / SHAKE_S) * min(1.0, age / ATTACK_S)
    count = len(levels)
    # Round the monitor a whole number of ripples fits the loop, so the seam does not show.
    wave = count / max(1, round(count / SHAKE_WAVE)) if ring else SHAKE_WAVE
    x = np.arange(count, dtype=np.float32) / wave
    ripple = np.sin(2 * math.pi * (x - SHAKE_HZ * age))
    return levels * (1.0 + amount * ripple)[:, None]
