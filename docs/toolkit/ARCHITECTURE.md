# Architecture Proposal — Shared Engine, Two Experiences

## 1. Separation of concerns

Do not implement two independent LED engines. Build on the existing output and safety infrastructure with two frame producers and optional audio modulation.

### Monitor Sync path

`Screen capture / sampling -> current screen-derived colour frame -> selected monitor style -> optional audio modulation -> existing zone/brightness/output processing -> central safety guard -> WLED transport`

The monitor-derived colour frame remains the visual anchor. Trippy can transform its spatial distribution and temporal movement, but it must continue using screen context. Audio should modulate parameters and add bounded event accents, not replace the screen frame with a free-running audio visualizer.

### Room Studio path

`Selected room scene -> procedural base frame -> optional audio modulation -> supported segment/device mapping -> existing brightness/output processing -> central safety guard -> WLED transport`

No monitor capture dependency. If the actual output supports only one strip, render a single-strip scene and design the abstraction so additional zones can be introduced later.

## 2. Shared audio feature service

One capture stream, one analysis service, multiple subscribers.

Suggested feature-frame contract (adapt names to existing code):

```json
{
  "schema_version": 1,
  "timestamp_monotonic_ns": 0,
  "valid": true,
  "confidence": 0.0,
  "rms": 0.0,
  "low_energy": 0.0,
  "mid_energy": 0.0,
  "high_energy": 0.0,
  "sub_energy": 0.0,
  "bass_envelope": 0.0,
  "onset_strength": 0.0,
  "kick_event": false,
  "snare_event": false,
  "stereo_pan": null
}
```

All normalized continuous values should have documented bounds (normally 0..1); pan, if available, should be -1..1. Include freshness/staleness handling. `confidence` should be lower for silence, speech-like/ambiguous or unstable signal as appropriate; do not pretend a heuristic is a reliable speech classifier.

The feature bus should not retain raw audio beyond the required rolling analysis window. It should not log sample data.

## 3. Optional modulation interface

A style/scene consumes a frame plus optional audio features:

```text
render(base_context, audio_features | none, style_config, dt) -> frame
```

If audio is missing, invalid, stale, disabled or confidence is insufficient, use the exact non-audio path with a smooth return rather than freezing a previous audio offset.

Prefer effect parameter modulation:
- `motion_speed`
- `warp_strength`
- `impact_strength`
- `brightness_modulation`
- `texture_amount`
- `palette_phase`

Avoid letting every band independently modify global brightness. Give each feature a defined purpose and bounded range.

## 4. Composition and render ownership

- Each effect has private state and a deterministic update step.
- Events can overlap without resetting unrelated effects.
- Define an explicit compositing strategy (e.g. additive-light in linear space, screen/maximum for selected event masks, or parameter modulation). Choose the strategy per effect and test it.
- Do not accumulate unbounded brightness.
- Clamp/normalize intermediate effect values.
- Apply the existing global safety/power/thermal logic after the complete combined frame.
- Do not implement independent per-effect safety guards as a substitute for the global guard.

The supplied baseline handoff documents the existing order as `to_led -> beat effects -> zones -> brightness -> safety guard -> quantize/dither -> transport`. Confirm this in code and keep all new work compatible with the actual ordering. Do not move `to_led`, zones, calibration or safety to suit the new design without proof and tests.

## 5. Configuration and persistence

Use declarative, versioned mode and scene configurations. Runtime-only state (FFT buffers, phase accumulators, noise buffers, effect envelopes) must not be serialized as user configuration.

Validate all imported config against a strict schema:
- Reject unknown effect IDs or unsupported schema versions with a useful message.
- Clamp or reject out-of-range values according to existing project conventions.
- Never allow imported config to disable/modify safety controls.
- Do not import network endpoints or code execution hooks.
- Preserve current stored settings and migrate only what is necessary.

## 6. Suggested logical modules (names are illustrative)

- `audio/capture` — existing capture lifecycle (reuse).
- `audio/analyzer` — existing/new DSP and normalization.
- `audio/features` — immutable normalized feature frame.
- `effects/primitives` — palette interpolation, noise field, wave field, transient impulse, envelope.
- `experiences/monitor` — monitor styles and audio modifiers.
- `experiences/room` — room scenes and audio modifiers.
- `config/experience_schema` — validation and migrations.
- UI routes/views for Monitor Sync and Room Studio.

Do not force these exact filenames if they conflict with the established project layout. Keep the conceptual boundaries.

## 7. State transitions

- Monitor Sync -> Room Studio: stop only what is exclusive to monitor capture if no longer used; retain safe output service.
- Room Studio -> Monitor Sync: start monitor capture only when required by Monitor Sync.
- Audio off: release audio capture when no active consumer requests it.
- Audio device loss: mark features invalid, fade audio modulation to neutral, preserve base frame.
- Safety/Calm: immediately suppress all audio-driven motion and close capture when no consumer remains.

## 8. Performance

Use the actual target strip pixel count and frame rate. For 240 LEDs, CPU and memory budgets should be measured on supported Macs. Precompute coordinate arrays and lookup tables where appropriate. Avoid per-frame object allocation and full image-sized simulations when the output is only 1D.
