# Acceptance and Verification Checklist

## A. Product behaviour

- [ ] Monitor Sync operates with audio disabled and no audio permission/capture.
- [ ] Each supported Monitor Sync style keeps screen-derived colour/composition as primary visual content.
- [ ] Trippy is visibly distinctive with audio disabled.
- [ ] Enabling audio in Trippy modifies speed/warp/impact/texture without replacing monitor sync with an audio-only visualizer.
- [ ] Audio loss/permission denial/stale feature data smoothly returns to neutral modulation; monitor sync continues.
- [ ] Audio controls are within the selected style; there is no separate top-level Audio Mode.
- [ ] Natural/Immersive and Vivid defaults are unchanged.
- [ ] Existing Pulse settings retain expected behaviour after migration.
- [ ] Cinematic audio response is restrained and does not pump excessively on dialogue-only test material.

## B. Room Studio

- [ ] Room Studio is a separate navigation destination.
- [ ] Opening Room Studio does not start monitor capture.
- [ ] Each scene runs with audio disabled.
- [ ] Optional audio adds modulation without becoming a prerequisite.
- [ ] Neon Coast palette/animation is smooth and original; no GTA/Rockstar assets or branding are bundled.
- [ ] Transitions and loops do not show jumps at wrap-around.
- [ ] Unsupported multi-zone controls are not presented as operational.
- [ ] Switching destinations releases unused capture/renderer resources.

## C. Audio analysis

- [ ] Synthetic kick track produces expected onset events.
- [ ] Quiet track (including 30 dB lower input) is tested for stable detection without identical unwanted visual intensity.
- [ ] Steady bass tone does not produce repeated false beats.
- [ ] Hi-hats and broadband noise do not falsely trigger kick events.
- [ ] Silence does not create events or rising AGC gain that lights the strip.
- [ ] Fast and bass analysis paths have measured timing and CPU cost.
- [ ] Stereo pan is only exposed when a real two-channel stream is confirmed.
- [ ] Audio buffers are not persisted, logged or sent over network.

## D. Rendering and safety

- [ ] All effect combinations pass through the existing central safety guard.
- [ ] Power and sustained thermal limits remain enforced.
- [ ] Flash/large-area modulation regression runs with every effect combination.
- [ ] No effect independently bypasses/reimplements safety.
- [ ] Black frames and letterbox bars remain black according to existing black-cut/protected-video semantics.
- [ ] Uniform-colour scene can show the intended shake/brightness modulation where appropriate.
- [ ] Bright, mid and dark scene baseline table is recorded before and after relevant changes.
- [ ] No unbounded event list, memory growth or per-frame heavy allocation.

## E. Compatibility and configuration

- [ ] Old settings load without user intervention.
- [ ] Existing IDs and meanings remain stable or have explicit migrations.
- [ ] Invalid/unknown import values are rejected safely.
- [ ] Imported config cannot set safety limits, arbitrary scripts or arbitrary network endpoints.
- [ ] Save, duplicate, rename, reset and export are covered where implemented.
- [ ] New feature defaults do not unexpectedly enable audio for users who had it off.

## F. Evidence to report

- Tests and commands run.
- CPU time per analysis block and render frame, measured on available test hardware.
- Output-stage before/after table (bright/mid/dark).
- Audio-to-output latency measurement method and result, if available.
- Screenshot/UI proof for Monitor Sync and Room Studio.
- Explicit statement of what could not be verified without physical LEDs or supported audio capture hardware.
