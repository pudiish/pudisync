# Mode Catalog — Monitor Sync Styles

## Shared rule

Monitor Sync styles always start from the monitor-derived frame. Audio Reactivity is an optional add-on within that selected style. With audio off, the style must still function and remain visually intentional.

Expose a simple control: **Audio Reactivity: Off / Subtle / Balanced / Expressive**. Internally map these choices to safe per-style limits; don't expose raw gain coefficients in the main UI.

## 1. Natural / existing Immersive

**Purpose:** faithful screen ambience.

- Preserve existing rendering and stored settings.
- Audio off by default unless the existing user explicitly enabled audio.
- If enabled, use very low depth; avoid continuous pumping.
- No forced palette changes.

## 2. Vivid

**Purpose:** stronger colour presence while remaining screen-led.

- Preserve current Vivid behaviour.
- Audio remains an optional low-depth enhancer.
- Do not silently change saturation, contrast, hue balance, black level, gamma or calibration.

## 3. Cinematic

**Purpose:** subtle atmospheric response for film/TV.

Base behaviour:
- Screen-derived colours remain dominant.
- Smooth temporal response and low movement.
- Preserve black bars/true black and protected-video behaviour.

Optional audio add-on:
- Bass envelope can very gently modulate the movement/brightness envelope.
- Transient events can create a broad, slow accent only above a confidence/energy gate.
- Do not use raw dialogue peaks as strong effects.
- Avoid requiring ML speech separation in the first release.

Controls:
- Audio reactivity level.
- Motion amount.
- Transition softness (within safe bounds).

## 4. Trippy

**Purpose:** a distinctive psychedelic monitor-synced style, not an audio-only visualizer.

Visual foundation:
- Derive a palette from screen-derived colours (or a controlled palette blend that preserves a relationship to screen colour).
- Apply deterministic multi-wave interference and low-frequency procedural noise warping.
- Create slow kaleidoscopic/phase-like movement along the 1D strip, adapted to the physical LED topology.
- Keep continuous motion; no per-frame random hue changes.
- Avoid full-strip high-contrast flashes.

Optional audio add-on:
- Sub/bass envelope -> bounded distortion/warp amplitude.
- Kick/onset -> short, smooth impulse into wave phase or local spatial ripple.
- Mid energy -> movement density or phase velocity.
- High energy -> restrained texture/detail, not random sparkle spam.
- Overall level -> bounded animation speed.
- Confidence/freshness -> blend toward neutral values when poor.

Main controls:
- Motion speed.
- Psychedelic intensity / warp amount.
- Colour intensity.
- Texture detail.
- Audio Reactivity level.

Advanced controls (optional, not required in the first UI):
- Noise scale, wave count, phase coupling, onset sensitivity, band mapping.

1D adaptation:
- Use one-dimensional warped palette fields, travelling interference waves, overlapping phase oscillators and smooth noise modulation.
- Do not claim to render 2D fractal tunnels or a true kaleidoscope on a single strip.
- If future matrix hardware exists, add a separate 2D renderer using the same feature bus and palette system.

## 5. Pulse

**Purpose:** energetic version of the existing Pulse look.

- Preserve old `bass_bounce` / `bass_shake` values and map them during migration.
- Fix insufficient spatial coverage, very short decay and invisible shake on uniform colours.
- Keep event-driven beat impact as the defining characteristic.
- Use overlapping bounded transient events rather than restarting the entire animation on every beat.
- Dark-scene response must not lift true black/letterbox/protected video indiscriminately.
- Audio is naturally enabled only where the legacy setting already enabled it; do not surprise existing users.

## Mode quality bar

Each style must have:
- A useful default configuration.
- Audio-off and audio-on comparison.
- Bright, mid, dark, uniform-colour and black-frame tests.
- Smooth entry/exit transitions.
- Measurable CPU cost.
- No safety bypass.
