# Mode / Scene Authoring Guide

## Shared fields

- `id`: stable lower_snake_case identifier; do not reuse or silently rename.
- `name`: user-visible title.
- `schema_version`: versioned config format.
- `renderer`: allowlisted renderer ID implemented in code.
- `parameters`: bounded declarative values only.
- `audio_reactivity`: optional mapping; never a raw capture config.

## Audio mapping principles

Each mapping should declare:
- feature (e.g. `bass_envelope`, `onset_strength`, `mid_energy`)
- target parameter (e.g. `warp_strength`, `motion_speed`, `impact_amount`)
- depth (bounded)
- smoothing/attack/release
- confidence/freshness requirements

Never map raw audio amplitude directly to global brightness without normalization and bounds.

## 1D renderer primitives

Allowed first-release primitives:
- `palette_gradient`
- `wave_interference_1d`
- `noise_warp_1d`
- `transient_spotlight_1d`
- `breath_envelope`
- `warm_noise_flicker`

All renderer IDs must be allowlisted in code. Config must not contain scripts, expressions, imports or filesystem/network references.

## Scene author checklist

- Is it recognizably different from existing scenes?
- Does it look good with audio off?
- Does its loop wrap smoothly?
- Are colour transitions intentional?
- Does it work on the actual LED topology?
- Does it stay within output/safety budgets?
- Are defaults good without user tuning?
