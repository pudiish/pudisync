# ADR-001: Two Experiences, Optional Audio Add-on

- Status: Proposed baseline for implementation
- Date: 2026-11-20

## Context

The product includes monitor-derived ambient lighting and an audio-reactive enhancement. It also needs independent room ambience scenes.

## Decision

Create two top-level experiences: Monitor Sync and Room Studio. Audio reactivity is a capability/modifier within a selected Monitor Sync style or Room Studio scene. It is not a top-level mode or replacement for monitor synchronization.

## Consequences

Positive:
- Monitor Sync remains useful without audio capture.
- Audio can be selectively enabled per experience.
- Room Studio works independently of screen capture.
- Shared analysis/effect primitives avoid duplicated engines.

Trade-offs:
- UI and state model must represent destination/style/audio modifier separately.
- Audio capture lifecycle must follow active consumers.
- Renderer integration must preserve the existing output/safety path.

## Rejected alternatives

- A standalone Audio Mode: rejected because audio is an add-on, not the primary experience.
- One unified list combining Monitor Sync styles and Room Studio scenes: rejected because their input source and lifecycle differ.
- Separate audio engine per mode: rejected because it duplicates capture and DSP.
