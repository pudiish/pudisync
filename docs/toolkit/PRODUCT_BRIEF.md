# Product Brief — PudiSync Experience Engine

## Product thesis

PudiSync should feel like a carefully designed audiovisual atmosphere system, not a generic RGB controller or a conventional equalizer.

The user chooses an experience. Monitor content supplies the primary colour and visual context in Monitor Sync. Audio optionally enriches movement and impact. Room Studio creates independent ambient environments.

## The mental model

**Destination** answers: where am I controlling lighting?
- Monitor Sync
- Room Studio

**Style / Scene** answers: what visual personality do I want?
- Monitor Sync styles: Natural, Vivid, Cinematic, Trippy, Pulse.
- Room scenes: Neon Coast, Aurora, Deep Ocean, Golden Hour, Hearth.

**Audio Reactivity** answers: should sound modify the chosen experience?
- Off
- Subtle
- Balanced
- Expressive

Audio is not a destination and is not a standalone style category.

## Desired experience qualities

- Memorable: Trippy and Neon Coast have distinctive identities.
- Credible: effects move continuously and react to meaningful features, not noise.
- Screen-faithful: monitor colour remains dominant in Monitor Sync.
- Calm when appropriate: Cinematic, Golden Hour and Hearth should not pump or flicker.
- Customizable without clutter: common controls first, advanced controls tucked away.
- Extensible: modes/scenes are versioned configuration, not duplicated code paths.
- Reliable: loss of audio never breaks monitor sync; stopping Room Studio never starts screen capture.

## MVP scope

### Monitor Sync
- Preserve current Natural/Immersive and Vivid behaviours.
- Preserve legacy Pulse behavior/mappings.
- Add Cinematic style.
- Add Trippy style, designed for a 1D strip and using screen-derived colour as the visual source.
- Provide optional audio reactivity within compatible styles.

### Room Studio
- Separate route/navigation item.
- Scene gallery with five initial scene definitions.
- Scene-only operation independent of monitor capture.
- Optional audio reaction.
- Save/duplicate/import/export custom scene.

### Shared engine
- Reuse current audio capture and analysis where possible.
- Normalized feature frame with timestamps/confidence.
- Small procedural primitive set: palette gradient, noise field, wave field, spotlight/event impulse, envelope follower.
- One central output safety path.

## Non-goals for first implementation

- Full surround decoding or mandatory 5.1/7.1.
- ML-based genre recognition or full speech separation.
- BPM prediction/anticipatory firing as a default.
- A public marketplace/plugin SDK.
- A complex visual node editor.
- A true 2D fractal renderer on a 1D LED strip.
- Multi-room/cloud synchronization.
- Replacement of WLED transport or screen capture stack.

## UX rules

- Audio controls appear within the chosen style/scene settings.
- If audio capture is unsupported or fails, show a small status and continue with the base experience.
- The screen-sync destination never requires audio permissions to provide its core value.
- The Room Studio destination never requires monitor capture.
- Presets should sound/feel good at defaults; do not expose every DSP coefficient in the main UI.
- Every style/scene includes a clear “Reset to default” and “Save as custom” where persistence permits.
