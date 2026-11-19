# PudiSync Experience Engine — Agent Toolkit

**Purpose:** a ready-to-use product, R&D and implementation brief for an AI coding agent working inside the existing PudiSync repository.

## The central product decision

PudiSync has two top-level experiences:

1. **Monitor Sync** — screen-derived colour and composition remain the primary visual source. Each supported visual mode can optionally enable **Audio Reactivity as an add-on layer**. Audio supplements monitor sync; it does not replace it, become a separate destination, or turn the product into an audio-only visualizer.
2. **Room Studio / Room Glow-up** — an independent ambient-scene experience that does not require monitor capture. Room scenes can optionally use the same audio-reactivity layer, but work beautifully with audio disabled.

A Monitor Sync mode therefore has these states:
- Monitor sync only (default / always available).
- Monitor sync + audio reactivity (optional per mode).
- Audio unavailable or disabled: gracefully fall back to monitor-only rendering.

Room Studio has:
- Scene-only ambient rendering.
- Scene + optional audio reactivity.

**Do not create a separate top-level “Audio Mode” or “Rhythm Mode”.** Audio is an optional modifier/control within the selected experience.

## Recommended first experience set

### Monitor Sync
- Natural / existing Immersive: preserve the current behaviour and user settings.
- Vivid: preserve current behaviour and user settings.
- Cinematic: conservative screen-aware styling; optional subtle audio enhancement.
- Trippy: screen-colour-led psychedelic procedural motion; optional audio modulation.
- Pulse: evolve the existing beat look without breaking compatibility; optional audio enhancement is its defining behaviour.

Do not create a large catalogue before these are genuinely polished. A style can work without audio.

### Room Studio
- Neon Coast (80s Miami / Vice City-inspired original scene; do not use GTA logos, artwork, game assets or soundtrack).
- Aurora.
- Deep Ocean.
- Golden Hour.
- Hearth.
- Optional Trippy Room scene, sharing the same procedural effect primitives as Monitor Sync Trippy.

Room Studio is a separate UI destination and independent source of lighting frames. It should not require a monitor capture session.

## How to use this toolkit

1. Open the existing PudiSync repository in the coding agent.
2. Add this toolkit folder to the repository workspace or point the agent at this directory.
3. Give the agent `AGENT_MASTER_PROMPT.md` as the task.
4. The agent must inspect the repository first, reconcile this proposal with the existing code, and then execute the compact plan sequentially.
5. The agent should not blindly rewrite the colour pipeline, capture stack, WLED transport, safety guard, or settings storage.

## Included

- `AGENT_MASTER_PROMPT.md` — copy/paste execution contract for Cursor, Claude Code, Copilot or another repository-aware agent.
- `PRODUCT_BRIEF.md` — user experience and product rules.
- `ARCHITECTURE.md` — shared engine, rendering and separation principles.
- `MODE_CATALOG.md` — detailed experience definitions and behaviour.
- `ROOM_STUDIO_SPEC.md` — independent room-scene UI and renderer brief.
- `ALGORITHM_PLAYBOOK.md` — practical algorithms, implementation guidance and caveats.
- `EXECUTION_PLAN.md` — one short, sequential implementation plan with explicit stop/go checks.
- `ACCEPTANCE_TESTS.md` — observable product and engineering acceptance criteria.
- `schemas/` and `examples/` — versioned declarative configuration starting points.
- `research/` — inspected open-source implementation references and design takeaways.
- `decisions/` — initial architecture decision records.
- `baseline/` — original requirements handoff supplied by the product owner.

## Important limitation

This toolkit is a specification and execution brief, not a verified patch against the PudiSync source repository. The agent must inspect actual code, tests and settings before changing implementation. It must report which assumptions match the code and which do not.
