"""Looks that move, and Room Studio: the experience engine (docs/EXPERIENCE.md).

Two destinations share one set of parts:

- Monitor Sync styles (Cinematic, Trippy) reshape the screen's own colours
  along the strip. The screen stays the source: a black frame stays black, and
  every colour shown is one the screen sent.
- Room Studio scenes paint the strip from a palette alone, with no screen
  capture at all.

Either may follow the sound, as an add-on with four levels (Off, Subtle,
Balanced, Expressive). Audio only modulates a few bounded parameters through
attack/release envelopes; when the sound stops or goes stale the envelopes
ease back to neutral and the look carries on as it was.

Motion is deterministic: a few sine waves and seeded value noise, advanced by
phase accumulators, so the same seed and time give the same frame, a change of
speed never jumps, and nothing loops back with a seam. All output then goes
through the one output stage and its safety guard (engine.OutputStage); nothing
here limits power, heat or flashing on its own.
"""
import copy
import json
import math
import re

import numpy as np

from .beat import clamp01, follow

SCHEMA_VERSION = 1

# Audio Reactivity: how much of each mapping's depth is used.
AUDIO_LEVELS = {"off": 0.0, "subtle": 0.35, "balanced": 0.7, "expressive": 1.0}
FEATURES = ("low_energy", "mid_energy", "high_energy", "bass_envelope", "onset_strength")
TARGETS = ("motion_speed", "warp_strength", "impact_amount", "texture_amount", "palette_phase",
           "brightness_modulation")
PARAMETERS = {
    # name: (low, high, kind)
    "motion_speed": (0.0, 2.0, float),
    "colour_intensity": (0.0, 1.0, float),
    "warp_strength": (0.0, 1.0, float),
    "texture_amount": (0.0, 1.0, float),
    "impact_amount": (0.0, 1.0, float),
    "noise_scale": (0.01, 10.0, float),
    "wave_count": (1, 8, int),
    "brightness": (0.0, 1.0, float),
}
MAX_DEPTH = 0.5
MAX_MAPPINGS = 12
MAX_STOPS = 12
MAX_CUSTOM_SCENES = 20
SCENE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Room scenes may brighten or dim by at most this much on the beat, under the guard's 10% flash swing.
ROOM_IMPACT = 0.08


# --- primitives ------------------------------------------------------------------
def hex_rgb(text):
    digits = text.lstrip("#")
    return np.array([int(digits[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0


def palette(stops, t):
    """Colours at positions `t` (wrapped into 0..1) along palette stops [(position, rgb)], blended smoothly."""
    positions = np.array([p for p, _ in stops], np.float32)
    colours = np.array([c for _, c in stops], np.float32)
    t = np.mod(np.asarray(t, np.float32), 1.0)
    return np.stack([np.interp(t, positions, colours[:, ch]) for ch in range(3)], axis=-1)


def _hash(cells, seed):
    """A repeatable pseudo-random 0..1 per integer cell; the same seed always gives the same field."""
    return np.mod(np.sin(cells * 127.1 + seed * 311.7) * 43758.5453, 1.0)


def noise(x, seed=0):
    """Smooth value noise, -1..1, continuous in x."""
    x = np.asarray(x, np.float64)
    cell = np.floor(x)
    frac = x - cell
    ease = frac * frac * (3.0 - 2.0 * frac)
    a, b = _hash(cell, seed), _hash(cell + 1.0, seed)
    return (a + (b - a) * ease) * 2.0 - 1.0


def waves(x, phase, count, seed=0):
    """Interfering travelling waves, -1..1: `count` sines with incommensurate spatial and temporal rates."""
    total = np.zeros_like(np.asarray(x, np.float64))
    for k in range(count):
        spatial = 1.0 + k * 1.618 + _hash(np.float64(k), seed) * 0.5
        temporal = (0.6 + 0.37 * k) * (1 if k % 2 == 0 else -1)
        offset = _hash(np.float64(k + 17), seed) * 2 * math.pi
        total += np.sin(2 * math.pi * (spatial * x + temporal * phase) + offset)
    return total / max(1, count)


def sample(levels, positions, ring):
    """The strip's colours read at fractional LED positions, blending neighbours; wraps round a ring."""
    count = len(levels)
    if ring:
        positions = np.mod(positions, count)
    else:
        # Reflect off the ends so the picture folds back instead of piling up there.
        span = 2 * (count - 1) if count > 1 else 1
        positions = np.mod(positions, span)
        positions = np.where(positions > count - 1, span - positions, positions)
    low = np.floor(positions).astype(np.int64)
    high = (low + 1) % count if ring else np.minimum(low + 1, count - 1)
    mix = (positions - low)[:, None].astype(np.float32)
    return levels[low] * (1.0 - mix) + levels[high] * mix


# --- audio as an add-on -------------------------------------------------------
class AudioMod:
    """Audio features -> bounded parameter offsets, each through its own attack/release envelope.

    Stale or low-confidence sound feeds 0, so every offset eases back to
    neutral at its own release speed rather than freezing where it was.
    """

    def __init__(self):
        self._values = {}
        self._last = None

    def update(self, features, mappings, level, now):
        dt = 0.0 if self._last is None else max(0.0, min(0.25, now - self._last))
        self._last = now
        scale = AUDIO_LEVELS.get(level, 0.0)
        out = {target: 0.0 for target in TARGETS}
        for i, m in enumerate(mappings):
            usable = scale > 0 and features["valid"] and features["confidence"] >= m.get("minimum_confidence", 0.0)
            wanted = features[m["feature"]] * m["depth"] * scale if usable else 0.0
            key = (i, m["feature"], m["target"])
            value = follow(self._values.get(key, 0.0), wanted, dt, m["attack_ms"] / 1000, m["release_ms"] / 1000)
            self._values[key] = value
            out[m["target"]] += value
        return out


class Motion:
    """Phase accumulators: speed changes alter the rate, never the position, so nothing jumps."""

    def __init__(self):
        self.phase = 0.0
        self.drift = 0.0
        self._last = None

    def advance(self, now, speed, drift_speed=0.0):
        dt = 0.0 if self._last is None else max(0.0, min(0.25, now - self._last))
        self._last = now
        self.phase += speed * dt
        self.drift += drift_speed * dt
        return self.phase, self.drift


# --- Monitor Sync styles --------------------------------------------------------------
def _mapping(feature, target, depth, attack_ms, release_ms, minimum_confidence):
    return {"feature": feature, "target": target, "depth": depth, "attack_ms": attack_ms,
            "release_ms": release_ms, "minimum_confidence": minimum_confidence}


# Each style's audio mappings. "none" is Natural, Vivid and Pulse: there the sound
# only breathes the brightness a little, and only when the person turns it on.
STYLES = {
    "none": {"name": "Natural", "mappings": [
        _mapping("bass_envelope", "brightness_modulation", 0.06, 120, 600, 0.3)]},
    "cinematic": {"name": "Cinematic", "mappings": [
        # Slow and gated high: dialogue rarely clears 0.65 confidence with a bass envelope worth showing.
        _mapping("bass_envelope", "brightness_modulation", 0.05, 500, 1800, 0.4),
        _mapping("onset_strength", "impact_amount", 0.08, 120, 900, 0.65)]},
    "trippy": {"name": "Trippy", "mappings": [
        _mapping("bass_envelope", "warp_strength", 0.18, 120, 700, 0.25),
        _mapping("onset_strength", "impact_amount", 0.22, 20, 420, 0.45),
        _mapping("mid_energy", "motion_speed", 0.12, 180, 900, 0.3),
        _mapping("high_energy", "texture_amount", 0.06, 100, 600, 0.35)]},
}


class StyleStage:
    """The selected Monitor Sync style, applied to LED levels after to_led and before the beat effects."""

    def __init__(self):
        self.audio = AudioMod()
        self.motion = Motion()
        self.kick = Motion()

    def apply(self, levels, now, cfg, features):
        style = cfg.get("style", "none")
        level = "off" if cfg.get("calm") else audio_level(cfg, style)
        mod = self.audio.update(features, STYLES.get(style, STYLES["none"])["mappings"], level, now)
        ring = cfg.get("layout") == "frame"
        if style == "trippy":
            return self.trippy(levels, now, cfg, mod, ring)
        if style == "cinematic":
            return self.cinematic(levels, now, cfg, mod)
        return breathe(levels, mod["brightness_modulation"])

    def trippy(self, levels, now, cfg, mod, ring):
        """The screen's colours folded, warped and flowing along the strip, like a 1D kaleidoscope.

        Every colour is read from the screen frame itself, so the strip still
        follows the picture; with the sound off it keeps moving on its own.
        """
        count = len(levels)
        if count < 2:
            return levels
        speed = cfg.get("trippy_speed", 0.55) * (1.0 + 2.0 * mod["motion_speed"])
        phase, drift = self.motion.advance(now, speed, speed * 0.21)
        # A beat kicks the phase forward smoothly, a ripple rather than a restart.
        kick, _ = self.kick.advance(now, mod["impact_amount"] * 4.0)
        phase += kick
        warp = clamp01(cfg.get("trippy_warp", 0.32) + mod["warp_strength"])
        x = np.arange(count, dtype=np.float64) / (count - 1)
        field = waves(x, phase, 3, seed=7) * 0.6 + noise(x * 3.0 + drift, seed=11) * 0.4
        warped = sample(levels, (x + warp * 0.18 * field) * (count - 1), ring)
        # Kaleidoscope fold: the screen read forward then back, three times along the strip, drifting.
        fold = np.abs(np.mod(x * 3.0 + drift * 0.5, 2.0) - 1.0)
        folded = sample(levels, fold * (count - 1), ring)
        mix = clamp01(cfg.get("trippy_colour", 0.72)) * 0.6
        out = warped * (1.0 - mix) + folded * mix
        out = saturate(out, 1.0 + 0.5 * cfg.get("trippy_colour", 0.72))
        texture = clamp01(cfg.get("trippy_texture", 0.22) + mod["texture_amount"])
        shimmer = waves(x * 4.0, phase * 1.7, 2, seed=23)
        return out * (1.0 + 0.25 * texture * shimmer + 0.15 * mod["impact_amount"] * field)[:, None]

    def cinematic(self, levels, now, cfg, mod):
        """The screen as it is, with a slow, faint drift of light along the strip; the sound only breathes it."""
        count = len(levels)
        if count < 2:
            return levels
        motion = clamp01(cfg.get("cinematic_motion", 0.3))
        phase, _ = self.motion.advance(now, 0.05 + 0.1 * motion)
        x = np.arange(count, dtype=np.float64) / (count - 1)
        drift = 0.12 * motion * waves(x, phase, 2, seed=3)
        swell = mod["brightness_modulation"] + mod["impact_amount"] * 0.5
        return levels * (1.0 + drift + swell)[:, None]


def breathe(levels, amount):
    """The whole strip a little brighter with the bass; amount is at most a few percent."""
    return levels if amount <= 0 else levels * (1.0 + amount)


def saturate(levels, amount):
    """Colour pushed away from each LED's grey by `amount`, brightness kept, never below zero."""
    grey = levels.mean(axis=1, keepdims=True)
    return np.clip(grey + (levels - grey) * amount, 0.0, None)


def audio_level(cfg, style):
    return cfg.get("audio_react", {}).get(style, "off")


def style_wants_sound(cfg):
    """True when the chosen style's Audio Reactivity is on (and Calm is off)."""
    return not cfg.get("calm") and audio_level(cfg, cfg.get("style", "none")) != "off"


# --- Room Studio --------------------------------------------------------------------
ROOM_RENDERERS = ("neon_coast_1d", "aurora_1d", "deep_ocean_1d", "golden_hour_1d", "hearth_1d")


def _scene(id_, name, description, renderer, parameters, stops, mappings):
    return {"schema_version": SCHEMA_VERSION, "id": id_, "name": name, "description": description,
            "experience_type": "room_scene", "renderer": renderer, "parameters": parameters,
            "palette": [{"position": p, "hex": h} for p, h in stops],
            "audio_reactivity": {"supported": True, "default_level": "off", "mappings": mappings},
            "compatibility": {"topology": "strip_1d", "requires_monitor_frame": False}}


_P = dict  # parameters, for brevity below
SCENES = {s["id"]: s for s in (
    # An original 1980s Miami-inspired night palette; an aesthetic only, no game's art or marks.
    _scene("neon_coast", "Neon Coast", "Midnight indigo, electric cyan and hot pink, drifting like neon on water.",
           "neon_coast_1d",
           _P(motion_speed=0.22, colour_intensity=0.68, warp_strength=0.12, texture_amount=0.08,
              impact_amount=0.12, noise_scale=0.5, wave_count=2, brightness=0.55),
           [(0.0, "#10142D"), (0.24, "#5545A9"), (0.49, "#24D9E8"), (0.72, "#FF4F9A"), (0.88, "#FF865C"),
            (1.0, "#10142D")],
           [_mapping("bass_envelope", "warp_strength", 0.08, 260, 1200, 0.3),
            _mapping("onset_strength", "impact_amount", 0.12, 60, 700, 0.5)]),
    _scene("aurora", "Aurora", "Slow teal, green and violet curtains of light.", "aurora_1d",
           _P(motion_speed=0.12, colour_intensity=0.62, warp_strength=0.28, texture_amount=0.12,
              impact_amount=0.0, noise_scale=0.6, wave_count=3, brightness=0.65),
           [(0.0, "#071B2D"), (0.30, "#087F75"), (0.58, "#63C98D"), (0.80, "#6152A5"), (1.0, "#071B2D")],
           [_mapping("bass_envelope", "motion_speed", 0.06, 700, 2000, 0.35),
            _mapping("mid_energy", "warp_strength", 0.05, 500, 1600, 0.35)]),
    _scene("deep_ocean", "Deep Ocean", "Navy and aqua caustics with the odd bioluminescent glint.", "deep_ocean_1d",
           _P(motion_speed=0.18, colour_intensity=0.6, warp_strength=0.2, texture_amount=0.3,
              impact_amount=0.05, noise_scale=0.8, wave_count=3, brightness=0.75),
           [(0.0, "#04102A"), (0.35, "#0B3D6B"), (0.62, "#127C9A"), (0.82, "#3FD6CF"), (1.0, "#04102A")],
           [_mapping("bass_envelope", "warp_strength", 0.08, 300, 1400, 0.3),
            _mapping("mid_energy", "texture_amount", 0.06, 300, 1200, 0.3)]),
    _scene("golden_hour", "Golden Hour", "Peach, amber and coral fading slowly toward violet.", "golden_hour_1d",
           _P(motion_speed=0.06, colour_intensity=0.7, warp_strength=0.06, texture_amount=0.0,
              impact_amount=0.0, noise_scale=0.4, wave_count=2, brightness=0.5),
           [(0.0, "#FFB38A"), (0.3, "#FF9A3C"), (0.55, "#F2705E"), (0.8, "#8E5A9E"), (1.0, "#FFB38A")],
           [_mapping("bass_envelope", "brightness_modulation", 0.04, 800, 2400, 0.4)]),
    _scene("hearth", "Hearth", "A warm, quiet fire glow; no harsh flicker.", "hearth_1d",
           _P(motion_speed=0.35, colour_intensity=0.8, warp_strength=0.1, texture_amount=0.25,
              impact_amount=0.0, noise_scale=1.2, wave_count=2, brightness=0.55),
           [(0.0, "#5A1A04"), (0.35, "#B33F0C"), (0.65, "#FF7A1A"), (0.85, "#FFB347"), (1.0, "#5A1A04")],
           [_mapping("bass_envelope", "brightness_modulation", 0.04, 400, 1600, 0.4)]),
)}


class RoomStage:
    """A Room Studio scene -> perceptual colours for every LED. Holds only runtime phase, never settings."""

    def __init__(self):
        self.audio = AudioMod()
        self.motion = Motion()
        self._key = None
        self._stops = None

    def render(self, scene, count, now, cfg, features):
        if self._key != id(scene):
            self._key = id(scene)
            self._stops = [(s["position"], hex_rgb(s["hex"])) for s in scene["palette"]]
        params = scene["parameters"]
        level = "off" if cfg.get("calm") else cfg.get("room_audio", "off")
        mod = self.audio.update(features, scene["audio_reactivity"]["mappings"], level, now)
        speed = params["motion_speed"] * cfg.get("room_speed", 1.0) * (1.0 + 2.0 * mod["motion_speed"])
        phase, drift = self.motion.advance(now, speed, speed * 0.25)
        warp = clamp01(params["warp_strength"] + mod["warp_strength"])
        texture = clamp01(params["texture_amount"] + mod["texture_amount"])
        x = np.arange(max(count, 1), dtype=np.float64) / max(count - 1, 1)
        renderer = RENDERERS[scene["renderer"]]
        colour, light = renderer(x, phase, drift, params, warp, texture, self._stops)
        intensity = clamp01(params["colour_intensity"] * cfg.get("room_intensity", 1.0) / 0.7)
        colour = _intensity(colour, intensity)
        swell = 1.0 + min(ROOM_IMPACT, mod["brightness_modulation"] + 0.6 * mod["impact_amount"])
        return np.clip(colour * (light * swell)[:, None], 0.0, 1.0).astype(np.float32)


def room_level(scene, cfg):
    """How bright the scene drives the strip, 0..1, applied to LED levels: the scene's own times Room brightness."""
    return scene["parameters"]["brightness"] * cfg.get("room_brightness", 1.0)


def _intensity(colour, amount):
    """0.7 keeps the palette as designed; less fades toward its grey, more deepens it."""
    grey = colour.mean(axis=1, keepdims=True)
    return np.clip(grey + (colour - grey) * (0.4 + amount * 0.86), 0.0, 1.0)


def _neon_coast(x, phase, drift, params, warp, texture, stops):
    scale = params["noise_scale"]
    position = x * scale + drift * 0.15 + warp * 0.25 * noise(x * 2.0 + drift * 0.3, seed=5)
    reflect = waves(x, phase, params["wave_count"], seed=1)
    light = 0.78 + 0.18 * reflect + 0.06 * texture * noise(x * 24.0 + phase * 2.0, seed=9)
    return palette(stops, position), light


def _aurora(x, phase, drift, params, warp, texture, stops):
    curtain = noise(x * 3.0 * params["noise_scale"] + drift * 0.4, seed=2)
    position = x * 0.6 + drift * 0.08 + warp * 0.35 * curtain
    ribbons = 0.5 + 0.5 * waves(x, phase * 0.6, params["wave_count"], seed=4)
    light = 0.45 + 0.55 * ribbons ** 1.5 + 0.05 * texture * noise(x * 18.0 + phase, seed=6)
    return palette(stops, position), light


def _deep_ocean(x, phase, drift, params, warp, texture, stops):
    swell = waves(x, phase, params["wave_count"], seed=8)
    position = 0.35 + 0.3 * swell * (0.5 + warp) + drift * 0.03
    caustic = np.abs(waves(x * 2.5 * params["noise_scale"], phase * 1.3, 3, seed=12)) ** 2
    # Bioluminescence: slow glints where a sparse noise field peaks, fading in and out.
    glint = np.clip(noise(x * 9.0 + drift * 0.2, seed=14) * 3.0 - 2.2, 0.0, 1.0) * texture
    light = 0.55 + 0.35 * caustic + 0.5 * glint
    colour = palette(stops, position)
    colour = colour + glint[:, None] * (hex_rgb("#7FFFE8") - colour) * 0.6
    return colour, light


def _golden_hour(x, phase, drift, params, warp, texture, stops):
    position = x * 0.35 + drift * 0.04 + warp * 0.1 * noise(x * 1.5 + drift * 0.2, seed=15)
    light = 0.85 + 0.1 * waves(x, phase * 0.3, params["wave_count"], seed=16)
    return palette(stops, position), light


def _hearth(x, phase, drift, params, warp, texture, stops):
    # Fire as filtered noise: slow octaves only (nothing faster than ~2 Hz), so it glows, never strobes.
    flame = (noise(x * 4.0 * params["noise_scale"] - phase * 1.1, seed=18) * 0.6
             + noise(x * 9.0 - phase * 1.9, seed=19) * 0.4 * texture)
    position = 0.55 + 0.25 * flame + warp * 0.1 * noise(x * 2.0 + drift, seed=20)
    light = 0.8 + 0.15 * flame
    return palette(stops, position), light


RENDERERS = {"neon_coast_1d": _neon_coast, "aurora_1d": _aurora, "deep_ocean_1d": _deep_ocean,
             "golden_hour_1d": _golden_hour, "hearth_1d": _hearth}


def room_wants_sound(cfg):
    return not cfg.get("calm") and cfg.get("room_audio", "off") != "off"


# --- scene configuration: validation, import and export ----------------------------------
SCENE_KEYS = {"schema_version", "id", "name", "description", "experience_type", "renderer", "parameters",
              "palette", "audio_reactivity", "compatibility", "based_on"}


def validate_scene(raw):
    """A room scene as stored or imported, checked strictly; ValueError says what is wrong.

    Unknown fields, renderers, features and versions are refused rather than
    ignored, so an imported file can never carry code, addresses or safety
    settings. Numbers out of range are clamped, as everywhere in the hub.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"the scene is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("a scene must be a JSON object")
    unknown = set(raw) - SCENE_KEYS
    if unknown:
        raise ValueError(f"a scene cannot have {', '.join(sorted(unknown))}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"this hub reads scene schema version {SCHEMA_VERSION}, not {raw.get('schema_version')!r}")
    if raw.get("experience_type", "room_scene") != "room_scene":
        raise ValueError("only room scenes can be imported here")
    scene_id = str(raw.get("id", ""))
    if not SCENE_ID.match(scene_id):
        raise ValueError("a scene id is 2-64 lower-case letters, digits or _, starting with a letter")
    name = str(raw.get("name", "")).strip()
    if not 1 <= len(name) <= 40 or any(ord(c) < 32 for c in name):
        raise ValueError("a scene name is 1-40 printable characters")
    description = str(raw.get("description", ""))[:240]
    renderer = raw.get("renderer")
    if renderer not in ROOM_RENDERERS:
        raise ValueError(f"unknown renderer {renderer!r}; this hub has {', '.join(ROOM_RENDERERS)}")
    clean = {"schema_version": SCHEMA_VERSION, "id": scene_id, "name": name, "description": description,
             "experience_type": "room_scene", "renderer": renderer,
             "parameters": _parameters(raw.get("parameters", {}), renderer),
             "palette": _palette(raw.get("palette")),
             "audio_reactivity": _audio(raw.get("audio_reactivity", {})),
             "compatibility": {"topology": "strip_1d", "requires_monitor_frame": False}}
    compatibility = raw.get("compatibility", {})
    if not isinstance(compatibility, dict) or compatibility.get("topology", "strip_1d") != "strip_1d":
        raise ValueError("this hub drives a single strip; only strip_1d scenes run here")
    if raw.get("based_on") in SCENES:
        clean["based_on"] = raw["based_on"]
    return clean


def _number(name, raw):
    lo, hi, kind = PARAMETERS[name]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return kind(max(lo, min(hi, value)))


def _parameters(raw, renderer):
    if not isinstance(raw, dict):
        raise ValueError("parameters must be an object")
    unknown = set(raw) - set(PARAMETERS)
    if unknown:
        raise ValueError(f"unknown parameters: {', '.join(sorted(unknown))}")
    base = next(s["parameters"] for s in SCENES.values() if s["renderer"] == renderer)
    return {name: _number(name, raw.get(name, base[name])) for name in PARAMETERS}


def _palette(raw):
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_STOPS:
        raise ValueError(f"a palette has 2-{MAX_STOPS} stops")
    stops = []
    for stop in raw:
        if not isinstance(stop, dict) or set(stop) != {"position", "hex"}:
            raise ValueError("each palette stop is {position, hex}")
        if not isinstance(stop["hex"], str) or not HEX.match(stop["hex"]):
            raise ValueError("a palette colour is #RRGGBB")
        position = float(stop["position"]) if isinstance(stop["position"], (int, float)) else math.nan
        if not 0.0 <= position <= 1.0:
            raise ValueError("a palette position is 0..1")
        stops.append({"position": position, "hex": stop["hex"].upper()})
    stops.sort(key=lambda s: s["position"])
    return stops


def _audio(raw):
    if not isinstance(raw, dict) or set(raw) - {"supported", "default_level", "mappings"}:
        raise ValueError("audio_reactivity has only supported, default_level and mappings")
    mappings = raw.get("mappings", [])
    if not isinstance(mappings, list) or len(mappings) > MAX_MAPPINGS:
        raise ValueError(f"at most {MAX_MAPPINGS} audio mappings")
    clean = []
    for m in mappings:
        if not isinstance(m, dict) or set(m) - {"feature", "target", "depth", "attack_ms", "release_ms",
                                                "minimum_confidence"}:
            raise ValueError("an audio mapping has feature, target, depth, attack_ms, release_ms, minimum_confidence")
        if m.get("feature") not in FEATURES:
            raise ValueError(f"unknown audio feature {m.get('feature')!r}")
        if m.get("target") not in TARGETS:
            raise ValueError(f"unknown audio target {m.get('target')!r}")
        try:
            depth = max(0.0, min(MAX_DEPTH, float(m.get("depth", 0))))
            attack = int(max(5, min(5000, float(m.get("attack_ms", 100)))))
            release = int(max(20, min(10000, float(m.get("release_ms", 500)))))
            confidence = max(0.0, min(1.0, float(m.get("minimum_confidence", 0.3))))
        except (TypeError, ValueError):
            raise ValueError("audio mapping numbers must be numbers") from None
        if not all(math.isfinite(v) for v in (depth, confidence)):
            raise ValueError("audio mapping numbers must be finite")
        clean.append(_mapping(m["feature"], m["target"], depth, attack, release, confidence))
    level = raw.get("default_level", "off")
    if level not in AUDIO_LEVELS:
        raise ValueError(f"default_level is one of {', '.join(AUDIO_LEVELS)}")
    return {"supported": True, "default_level": level, "mappings": clean}


def custom_scenes(raw):
    """{id: scene} of the person's own scenes. One that no longer validates is dropped, as profiles are."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"room_custom is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("room_custom must be an object")
    clean = {}
    for key, scene in list(raw.items())[:MAX_CUSTOM_SCENES]:
        try:
            scene = validate_scene(scene)
        except ValueError:
            continue
        if scene["id"] == key and key not in SCENES:
            clean[key] = scene
    return clean


def all_scenes(custom):
    return {**SCENES, **custom}


def scene_for(cfg):
    """The active scene, or Neon Coast when the stored id no longer exists."""
    return all_scenes(cfg.get("room_custom", {})).get(cfg.get("room_scene"), SCENES["neon_coast"])


def free_id(name, taken):
    """A scene id from a name, unique among `taken`."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "scene"
    if not base[0].isalpha():
        base = "my_" + base
    base = base[:56]
    candidate, n = base, 2
    while candidate in taken or not SCENE_ID.match(candidate):
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def copy_scene(scene, name, taken):
    """A custom copy of `scene` under `name`, remembering the built-in it came from for Reset."""
    clone = copy.deepcopy(scene)
    clone["id"] = free_id(name, taken)
    clone["name"] = name
    clone["based_on"] = scene.get("based_on") or (scene["id"] if scene["id"] in SCENES else None)
    if clone["based_on"] is None:
        del clone["based_on"]
    return validate_scene(clone)


def preview_css(scene):
    """A CSS gradient of the scene's palette, for its card."""
    stops = ", ".join(f"{s['hex']} {round(s['position'] * 100)}%" for s in scene["palette"])
    return f"linear-gradient(90deg, {stops})"


def catalogue(custom):
    return [{"id": s["id"], "name": s["name"], "description": s["description"], "custom": s["id"] not in SCENES,
             "based_on": s.get("based_on"), "preview": preview_css(s), "parameters": s["parameters"],
             "palette": s["palette"]} for s in all_scenes(custom).values()]
