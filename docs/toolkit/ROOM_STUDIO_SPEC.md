# Room Studio / Room Glow-up — Product and UI Specification

## Purpose

Room Studio creates ambient lighting scenes independent of monitor capture. It is a separate navigation destination, not another Monitor Sync mode.

The same renderer primitives and safety/output path may be reused. Room Studio must not start screen capture merely because the user opens it.

## Initial scenes

### 1. Neon Coast (working title; Vice City-inspired)

A new, original 1980s Miami-inspired neon environment. This is an aesthetic direction only; do not use GTA VI assets, logos, game screenshots, Rockstar marks, fonts, soundtrack or imply affiliation.

Palette anchors:
- Midnight indigo `#10142D`
- Electric cyan `#24D9E8`
- Hot pink `#FF4F9A`
- Coral `#FF865C`
- Indigo-violet `#5545A9`

Motion:
- Smooth palette drift and crossfade.
- Gentle reflected-light waves.
- Optional very slow neon pulse on selected accents.
- No abrupt palette reset at loop boundary.
- Audio optional: bass modifies wave depth; onset creates a restrained neon pulse; high band adds limited texture.

### 2. Aurora

- Teal/green/violet palette.
- Smooth procedural noise drift and broad wave movement.
- Very slow transitions; no strobe.
- Optional audio modulates flow speed, not hue randomly per frame.

### 3. Deep Ocean

- Navy/cyan/aqua base.
- Slow moving caustic-like wave modulation adapted for one-dimensional output.
- Occasional restrained bioluminescent accents.
- Optional audio modulates wave amplitude and density.

### 4. Golden Hour

- Peach/amber/coral/violet palette.
- Long seamless crossfade / palette drift.
- Minimal motion; audio optional and subtle.

### 5. Hearth

- Warm amber/orange palette.
- Use filtered deterministic noise for a controlled candle/fire impression.
- Avoid literal rapid flame flicker and full-strip flashes.
- Audio should be off by default; optional bass may slightly influence warmth/intensity.

### Optional 6. Trippy Room

Only add if shared Trippy primitives are already implemented. It must be less screen-dependent than Monitor Trippy and remain tasteful at low brightness.

## UI information architecture

Top-level navigation:
- Monitor Sync
- Room Studio
- Settings (existing)

Room Studio sections:
1. **Scenes** — curated scene cards with preview thumbnail, name and one-line personality.
2. **Controls** — brightness, motion speed, colour intensity, optional Audio Reactivity.
3. **My Scenes** — saved user scenes, duplicate, rename, delete, reset.
4. **Advanced** — palette stops, noise scale, motion type, supported zone/segment assignment, import/export.

Keep common controls visible and technical controls behind Advanced. A full node editor is out of scope.

## First-run behaviour

- Entering Room Studio loads a safe default scene preview.
- Explicit Apply/Enable starts output; if existing UX expects immediate switching, follow that established convention.
- Screen capture remains stopped unless another active feature requires it.
- If audio is unavailable, scene runs without audio.
- Switching away gracefully stops the scene renderer and releases unneeded resources.

## Scene data

A scene should define:
- stable ID, name, description, schema version
- palette stops
- procedural renderer ID
- speed / intensity / scale parameters
- optional audio modulation mapping
- compatible output topology (1D strip initially)

It must not define arbitrary executable code, network destinations, safety values or raw audio storage.

## Hardware honesty

The original handoff reports 240 LEDs. A single 1D strip cannot independently light the desk, ceiling and all room surfaces. Implement correct 1D scenes now. Add multi-zone/device support only if current application/physical hardware supports it; otherwise document it as a future capability, not a delivered feature.
