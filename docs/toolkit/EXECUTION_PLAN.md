# Compact End-to-End Execution Plan

**Goal:** one coherent delivery, executed sequentially by the repository agent. These are work steps in one task, not six separate releases or a request for the user to approve every stage.

## Step 1 — Audit and lock integration points

Inspect actual repo, capture/mode/effect/settings/safety/UI/transport modules. Confirm the supplied handoff's build and pipeline claims. Produce a short claim/discrepancy table. Identify the smallest integration points. Do not refactor working components without evidence.

**Exit:** exact implementation plan tied to current files; no unknown safety bypass or settings migration risk.

## Step 2 — Define the experience model and state

Implement/adjust the state model for:
- Monitor Sync destination + selected style.
- Optional Audio Reactivity level within compatible monitor styles.
- Room Studio destination + selected scene.
- Optional Audio Reactivity within room scenes.

Preserve existing mode IDs and stored settings. Add schema versioning and migration. Validate import/export. Audio off remains a fully functional monitor-sync state.

**Exit:** unit tests for state transitions, defaults, migration and invalid config.

## Step 3 — Stabilize shared audio features

Reuse existing capture and beat detector where sound. Add only missing feature extraction and status fields. Compare current analysis against a dual-resolution/filter-bank approach offline. Select the simplest method that measurably improves bass/transient separation within measured latency/CPU budget.

**Exit:** feature frame contract, synthetic tests, no raw audio persistence/logging, audio-loss fallback.

## Step 4 — Implement shared visual primitives and Monitor Trippy

Implement a small deterministic 1D procedural toolkit: palette field, multi-wave interference, smooth noise warp, bounded transient impulse, attack/release envelopes. Use it to create Trippy while maintaining monitor-derived colours. Add Audio Reactivity Off/Subtle/Balanced/Expressive and prove its additive nature.

**Exit:** Trippy is distinctive with audio off; audio on modulates rather than replaces monitor sync; low-cost performance test.

## Step 5 — Implement Cinematic/Pulse integration

Add Cinematic with restrained optional audio modulation. Improve existing Pulse using the measured baseline in the handoff, preserve existing configuration mapping and correct dark-scene/uniform-colour behaviour without lifting true black.

**Exit:** before/after baseline table, old settings migration test, no change to existing Natural/Vivid defaults.

## Step 6 — Build Room Studio vertical slice

Add separate UI destination, scene gallery, scene state, procedural scene renderer and five initial presets: Neon Coast, Aurora, Deep Ocean, Golden Hour, Hearth. Ensure Room Studio works with monitor capture stopped. Add optional audio modulation using the same feature bus. Provide save/duplicate/reset/import/export if compatible with existing settings infrastructure.

**Exit:** test monitor capture not started by Room Studio; scenes run with audio off; optional audio on/off fallback tested.

## Step 7 — Integrate safety and finish

Check all generated output through the single existing central guard. Run full tests, baseline measurements, config validation and CPU timing. Inspect settings migration and logs. Add minimal docs and screenshots where possible.

**Exit:** acceptance criteria in `ACCEPTANCE_TESTS.md` pass or are explicitly reported as blocked by unavailable hardware/environment. Do not claim hardware validation without physical testing.

## Scope guard

Do now: Monitor Sync + optional audio, Trippy, Cinematic, Pulse improvement, Room Studio and the five scenes, config migration, safety regression.

Defer: surround/5.1, ML genre/speech recognition, predictive BPM, plugin marketplace, public scene sharing, complex node editor, 2D-only visuals, cloud sync.
