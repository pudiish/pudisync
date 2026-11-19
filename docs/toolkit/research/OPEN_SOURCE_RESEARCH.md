# Open-Source R&D Notes — inspected 20 November 2026

These notes summarize repository metadata and source files inspected for design inspiration. They are not a claim that every feature is suitable for PudiSync or that upstream code is safe to copy. Inspect the upstream licence before reusing any code; the intended approach is to learn patterns and implement independently.

## 1. LedFx

Repository: https://github.com/LedFx/LedFx

Repository metadata inspected: Python project; GPL-3.0; repository showed recent activity in November 2026. It has a large effect catalogue and separation between audio input/analysis and effects.

Source files inspected:
- https://github.com/LedFx/LedFx/blob/main/ledfx/effects/audio.py
- https://github.com/LedFx/LedFx/blob/main/ledfx/effects/melbank.py
- https://github.com/LedFx/LedFx/blob/main/ledfx/effects/spotlight.py
- https://github.com/LedFx/LedFx/blob/main/ledfx/effects/plasma2d.py
- https://github.com/LedFx/LedFx/blob/main/ledfx/effects/soap2d.py
- https://github.com/LedFx/LedFx/blob/main/ledfx/effects/radial.py
- https://github.com/LedFx/LedFx/blob/main/docs/effects/simple/spotlight.md
- https://github.com/LedFx/LedFx/blob/main/docs/effects/matrix/soap.md
- https://github.com/LedFx/LedFx/blob/main/docs/effects/matrix/concentric.md
- https://github.com/LedFx/LedFx/blob/main/docs/effects/matrix/flame.md

Findings:
- `audio.py` owns the audio input source and shared audio-reactive infrastructure; effects subscribe to analyzed data instead of opening independent capture streams.
- `melbank.py` defines `FFT_SIZE = 4096` and `MIC_RATE = 30000`, and supports multiple filter-bank coefficient approaches/ranges. Treat this as a candidate comparison point, not a direct prescription.
- `plasma2d.py` uses multiple sinusoidal fields and allows a selected audio power feature to influence animation.
- `soap2d.py` uses FastNoiseLite OpenSimplex2, persistent buffers, bounded configuration and a selected audio band to inject motion. Its documentation warns that it is computationally expensive, a useful reminder to profile procedural effects.
- `Spotlight` was contributed in PR #1789 and merged April 24, 2026. It spawns multiple fading light cones, uses weighted low/mid/high activity and transient boosts, has a maximum active-spot count, and includes presets called Club Smooth, Club Drop Max and Small Spotlights. It is a strong design reference for bounded event pools and natural decay.
- The effect docs explicitly distinguish 1D strip effects from 2D matrix effects.

Community signal:
- PR #1789 is a concrete example of an effect emerging from a real viewing use case (concert viewed with TV Ambilight) and becoming a configurable, preset-backed effect.
- This is a useful pattern: solve a perceptible experience first, then expose a few meaningful controls.

## 2. Hyperion.NG

Repository: https://github.com/hyperion-project/hyperion.ng

Repository metadata inspected: C++ project, MIT licence, active repository metadata in November 2026.

Sources:
- https://github.com/hyperion-project/hyperion.ng/tree/master/effects
- https://github.com/hyperion-project/hyperion.ng/blob/master/effects/mood-blobs.py
- https://github.com/hyperion-project/hyperion.ng/blob/master/effects/plasma.py
- https://github.com/hyperion-project/hyperion.ng/blob/master/effects/Seawaves.json
- https://github.com/hyperion-project/hyperion.ng/blob/master/effects/double-swirl.json

Findings:
- Repository documentation describes a scriptable effect engine and built-in effects.
- Effect definitions are paired with configuration JSON for parameters and names.
- `mood-blobs.py` demonstrates continuous phase-driven amplitude patterns, controlled palette handling and configurable motion.
- `plasma.py` demonstrates a procedural multi-sine field and palette lookup.
- `Seawaves.json` and `double-swirl.json` illustrate parameterized scene presets.

Takeaway: Room Studio should be scene/configuration driven, not a separate hardcoded renderer for every scene.

## 3. WLED AudioReactive

Repository: https://github.com/wled/WLED

Important: the in-tree `usermods/audioreactive_v1` README explicitly marks V1 deprecated/historical and points to the out-of-tree AudioReactive V2 work at https://github.com/MoonModules/WLED-AudioReactive-Usermod.

Sources:
- https://github.com/wled/WLED/tree/main/usermods/audioreactive_v1
- https://github.com/MoonModules/WLED-AudioReactive-Usermod

Findings:
- Audio processing provides a data structure specially written effects can consume; it does not itself implement the effect visuals.
- The old code illustrates FFT/windowing choices, peak timing, dynamics limiting, AGC, audio source handling and hardware CPU trade-offs.
- The V2 repository's README describes it as an audio processing feature provider for custom effects.

Takeaway: reinforce the shared feature-provider / independent renderer separation. Do not use the deprecated V1 as the implementation baseline.

## 4. HyperHDR

Repository: https://github.com/awawa-dev/HyperHDR

Repository metadata inspected: C++ project, MIT licence, macOS listed as supported. README describes its Infinite Color Engine, floating-point pipeline, linear sRGB processing, smoothing/interpolation, temporal dithering and RGBW conversion.

Source:
- https://github.com/awawa-dev/HyperHDR

Takeaway: focus on colour stability, temporal smoothness and precise final output. Do not bolt on a second colour pipeline without inspecting PudiSync's existing one.

## Overall recommendation

- Borrow architecture patterns from LedFx: one audio analysis service, features consumed by effects, preset-backed parameters and bounded effect state.
- Borrow procedural scene ideas from Hyperion and LedFx: deterministic motion, palettes, blobs, waves, plasma/noise, independent effect parameters.
- Borrow rendering-quality research topics from HyperHDR: linear-light processing, smoothing, dithering and stable output.
- Use WLED as a reference for audio feature-provider patterns, but follow its maintained V2 direction, not deprecated V1.
- Avoid importing code directly. Check licences and implement compatible ideas independently.

## Not established by this research

- No claim that these are statistically the community's top-rated effects.
- No proof that all effects are suitable for a 240-LED 1D strip.
- No proof of PudiSync's actual screen capture channel count or latency; only its source audit and hardware tests can establish that.
- No claim of WCAG device certification.
