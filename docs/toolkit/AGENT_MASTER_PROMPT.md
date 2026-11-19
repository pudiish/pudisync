# MASTER AGENT TASK — PudiSync Experience Engine + Room Studio

You are acting as a principal macOS application engineer, real-time DSP engineer, LED rendering engineer, interaction designer and pragmatic technical lead. Work inside the existing PudiSync repository. This is an implementation task, not a greenfield rewrite.

## Mission

Deliver a cohesive PudiSync experience enhancement with two distinct product destinations:

### A. Monitor Sync (primary existing experience)
The monitor/screen-derived image remains the primary source of the LED frame and colours. Existing monitor capture, screen sampling, colour grading and mapping remain the baseline.

Add an optional **Audio Reactivity layer** inside the selected Monitor Sync style. It supplements the monitor-derived result by modulating selected effect parameters (motion, intensity, distortion, spatial accents, etc.). It must never replace monitor sync or require audio to be enabled. If audio capture is unavailable, disabled, interrupted or low-confidence, the mode continues in monitor-only form.

There is no separate top-level Audio Mode. Do not introduce Rhythm as a separate destination. Audio is a feature toggle/intensity control within compatible modes.

### B. Room Studio / Room Glow-up (separate destination)
Build a separate UI destination for independent ambient scenes. It must work when monitor capture is off. A room scene produces its own base lighting frame from a procedural scene/palette/timeline. It may optionally consume the shared audio feature stream as an add-on, but audio is not mandatory.

For the current hardware, assume the known output may be a 240-LED one-dimensional strip until repository/hardware inspection proves otherwise. Do not pretend it is a 2D matrix or a whole-room multi-zone system. Design a scalable zone/device abstraction if the code can support it, but keep the first implementation correct for the actual hardware.

## Product experience to build

### Monitor Sync visual styles
1. **Natural / existing Immersive** — preserve current behaviour and persisted values.
2. **Vivid** — preserve current behaviour and persisted values.
3. **Cinematic** — monitor-derived colour remains dominant; restrained optional audio enhancement.
4. **Trippy** — distinctive psychedelic procedural motion driven primarily by monitor-derived colour/palette and spatial content. Audio optionally modulates speed, distortion amount, transient impulses and texture. With audio off it must still look intentional and complete.
5. **Pulse** — evolve the existing Pulse/beat experience; retain legacy mappings and user expectations. Audio enhances the screen-synced frame rather than becoming a replacement visualizer.

### Room Studio scenes
- **Neon Coast** — original 1980s Miami / Vice City-inspired neon palette: midnight indigo, electric cyan, hot pink, coral/orange. Continuous, elegant colour transitions. Do not use GTA logos, game assets, artwork, fonts, music or imply affiliation with Rockstar. Treat this as an aesthetic reference only.
- **Aurora** — slow aurora-like gradient/noise drift.
- **Deep Ocean** — deep navy/cyan/aqua, soft bioluminescent accents.
- **Golden Hour** — warm peach/amber/coral/violet transition.
- **Hearth** — restrained warm candle/fire-like ambience, no harsh flicker.
- Optional Trippy scene only if it reuses shared, tested procedural primitives without expanding scope excessively.

Each Room Studio scene should work without audio; optional audio reactivity is a modifier, not the scene's identity.

## Non-negotiable architecture rules

1. Preserve working features and backward compatibility. Do not delete/replace current modes or saved settings.
2. Inspect the real repository before proposing file changes. The included handoff describes one baseline build; confirm it against code.
3. Keep monitor-derived colour/composition as the primary source in Monitor Sync.
4. Audio is optional and additive. No audio dependency for monitor-only mode operation.
5. Room Studio is independent of monitor capture.
6. Use one audio capture/analysis service and publish normalized feature data. Effects must not open their own capture streams or implement duplicate detectors.
7. Use reusable effect primitives and declarative, versioned mode/scene configurations. Avoid a plugin framework unless the current project already has one or a clear requirement demands it.
8. Use deterministic procedural motion (seeded noise / continuous phases); never use per-frame random colour changes as the main animation engine.
9. Ensure all output passes through the existing central safety, power, thermal and anti-flash controls. Never bypass, duplicate or weaken the guard.
10. Keep audio samples in memory only. No sample persistence, logging, telemetry, upload or network streaming.
11. Preserve WLED transport and screen capture implementation unless the repository audit demonstrates a necessary fix.
12. Avoid ML by default. Only propose a model for speech/music classification if lightweight deterministic DSP is demonstrably insufficient and the product benefit justifies its cost/privacy implications.
13. Avoid speculative BPM prediction in the first delivery. Reactive events and smoothed envelopes are sufficient; stable tempo tracking can be a later optional enhancement.
14. A 1D strip cannot render genuine 2D fractals/kaleidoscopes. Design the best 1D adaptation and explicitly gate 2D-only effects on matrix hardware.
15. Do not copy third-party source code into PudiSync. Study algorithms and architecture; implement independently and respect each repository's licence.

## Mandatory repository reconnaissance

Before editing, inspect:
- Repository tree, language/runtime, application entry points and UI navigation.
- Existing monitor capture and screen-to-LED colour pipeline.
- Current mode/looks definitions, settings persistence, migrations and defaults.
- Audio capture, `BassMeter`, beat detection, FFT/window/hop, buffer timing and status payload.
- Effect application order, LED geometry/zones and output scheduler.
- `safety.py` or equivalent guard: flash counting, power, thermal, brightness, black-level and protected-video behaviour.
- WLED transport and frame format.
- Tests, fixtures, benchmarks and current baseline measurements.

Use the supplied `baseline/PudiSync audio engine requirements handoff.md` as the product owner's current requirements and measured baseline. Do not assume every statement remains true without code verification.

Produce a concise audit table: `claim / confirmed in code / discrepancy / action`. Then proceed with the execution plan unless a destructive or genuinely blocking decision is encountered.

## Target composition model

Use the existing pipeline as the source of truth. Conceptually, the system should support:

- Monitor Sync: `screen-derived base frame -> selected visual style -> optional audio modulation/effects -> existing zones/brightness/output policy -> existing safety guard -> transport`.
- Room Studio: `selected procedural room scene -> optional audio modulation -> room/device mapping if supported -> existing brightness/output policy -> existing safety guard -> transport`.

These are conceptual ownership boundaries, not permission to reorder the actual code blindly. Compare with the current pipeline and preserve established semantics. If the existing pipeline applies effects at a different stage, propose the smallest safe integration point and test it.

Audio reactivity should provide features such as normalized low/mid/high energy, transient/onset strength, bass envelope, confidence and optional stereo pan when capture genuinely provides stereo. Do not claim true 5.1/7.1 unless verified.

## Mode interaction requirements

- Every compatible Monitor Sync style has an `Audio Reactivity` control: Off / Subtle / Balanced / Expressive (or an equivalent accessible design).
- Default should be Off for existing looks unless preserving an existing user's explicit enabled setting. Existing Pulse should preserve its current configured behaviour.
- Trippy exposes carefully chosen user controls: motion speed, distortion/deformation, colour intensity, texture/detail and optional audio response. Keep expert DSP parameters under Advanced.
- Cinematic audio reaction should be subtle and dialogue/noise-resistant; do not block the first release on ML speech separation.
- Pulse should show visible response on bright, mid and dark scenes without lifting true black/letterbox pixels indiscriminately.
- Monitor-only behavior must remain visually correct when audio is absent.
- Calm/disable audio reactivity must stop audio capture when no audio-dependent consumer remains.

## Room Studio interaction requirements

- Separate navigation/page from Monitor Sync.
- Scene cards with a clear visual preview, scene name and short description.
- Quick controls: master brightness, motion speed, palette/intensity and optional audio reaction.
- Advanced scene editor: palette stops, animation speed, motion scale, zone/segment mapping only where hardware supports it.
- Save as custom scene, duplicate, reset, import/export JSON.
- Scene transitions must be continuous and loop-safe.
- Room Studio should not start monitor capture as a side effect.

## Implementation approach

Follow `EXECUTION_PLAN.md` in order. Keep the work compact: consolidate overlapping phases and deliver a coherent end-to-end result rather than leaving a six-stage roadmap with disconnected partial work.

Write code in the repository's existing style. Prefer small modules and explicit data models. Add tests alongside code.

Do not stop after documentation. Once the audit is complete, implement the highest-value complete vertical slice: mode/add-on state model, shared audio feature integration, Trippy visual style, first Room Studio scenes/UI, configuration persistence and safety regression tests. Then complete the remaining listed MVP items as far as the current repository and environment allow.

If a UI preview, screenshot, or hardware validation cannot be performed in the environment, clearly mark it as not verified and provide exact manual test steps. Never claim physical LED performance was measured unless it was.

## Required deliverables

At completion, provide:
1. Repository audit and confirmed architecture.
2. Short implementation summary with files changed.
3. Final mode and Room Studio configuration examples.
4. Tests run and results (exact commands and pass/fail).
5. Performance/CPU impact if measured.
6. Safety/privacy review.
7. Backward-compatibility and migration explanation.
8. Screenshots or UI evidence where available.
9. Remaining limitations and explicit follow-up items.
10. A concise manual validation checklist for real LEDs and audio.

## Acceptance gates

Do not declare done unless:
- Existing monitor sync still works with audio off.
- At least one new style (Trippy) is visibly distinct with audio off.
- Enabling audio modifies the same monitor-synced experience without replacing screen-derived colour.
- Disabling audio returns smoothly to monitor-only output and stops capture when unused.
- Room Studio works with monitor capture stopped.
- A Room Studio scene can optionally react to audio without becoming an audio-only mode.
- All generated frames pass through the existing safety guard.
- Existing settings survive upgrade/migration.
- Automated tests cover configuration validation, mode switching, audio fallback, scene independence and safety integration.

Start by reading this toolkit and the original handoff. Then inspect the repository. Be pragmatic, evidence-led and finish a coherent implementation rather than overengineering.
