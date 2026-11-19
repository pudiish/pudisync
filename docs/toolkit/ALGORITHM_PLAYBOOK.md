# Algorithm Playbook — Practical, Bounded and Testable

This is a shortlist of algorithms to evaluate and use, not a mandate to implement every technique. Prefer existing project primitives when they are correct.

## 1. Audio analysis

### A. Shared feature extraction

Produce a compact timestamped feature frame. One capture stream and one analyzer serve all consumers.

Candidate features:
- RMS / overall energy.
- Low/sub, mid and high band energies.
- Smoothed bass envelope.
- Spectral-flux/onset strength.
- Kick-like transient event.
- Optional snare-like broadband transient.
- Optional stereo pan only when real stereo channels are available.
- Confidence and freshness.

Normalize to documented bounded ranges. Keep raw energy and visual intensity separate: detector sensitivity should not dictate output brightness.

### B. Filter banks and FFT

The handoff reports 2048 samples at 48 kHz, approximately 23.4 Hz bin spacing. That gives coarse low-frequency discrimination for a narrow 30–90 Hz band.

Research reference: LedFx's current melbank module uses a 4096-sample FFT at a 30 kHz analysis rate and configurable filter-bank families/ranges. This is evidence that longer windows and filter banks are used in a mature open-source project, not evidence that PudiSync must copy the same settings.

Recommended PudiSync experiment:
- Fast path for transient/onset detection with a shorter window/hop.
- Bass path for more stable low-frequency envelope using a longer window or filter-bank approach.
- Compare 2048 vs 4096 and a dual-resolution variant on the same offline synthetic/real envelope fixture.
- Measure frequency discrimination, event timing, CPU per analysis block and end-to-end latency.

Do not increase every window blindly. A larger FFT improves frequency resolution but increases temporal smearing/delay.

### C. Onset detection

A practical starting point is positive spectral flux (energy increase between adjacent spectra) with adaptive thresholding and a refractory period. Combine it with low-band energy for kick-like events. Do not label it a true kick classifier without validation against varied music.

Potential process:
1. Window samples.
2. Compute magnitude spectrum or filtered band energies.
3. Calculate positive spectral flux.
4. Maintain rolling statistics / noise floor.
5. Trigger event when flux and relevant low-band energy cross adaptive thresholds.
6. Apply refractory interval and confidence.

Use recorded feature/envelope fixtures; do not store raw user audio.

### D. Envelope followers

Use asymmetric attack/release smoothing. Fast attack catches impacts; slower release prevents chattering. Parameterize time constants in milliseconds and derive coefficients from actual `dt`, not a fixed frame count.

### E. Adaptive normalization

Maintain a slow noise floor and bounded gain control. Avoid pumping and avoid making silence appear active. Include clipping/quiet-source tests. Keep `raw`, `normalized`, `event_strength` and `effect_depth` separate.

### F. Speech / music

Do not promise dialogue suppression with a simple bass gate. For the first version, Cinematic should use conservative event thresholds, low maximum depth, slow envelopes and an optional user intensity setting. Add lightweight voice-activity heuristics only if evaluation demonstrates real value. ML is not an MVP dependency.

## 2. Procedural visuals

### A. Deterministic noise

Use a deterministic seeded noise field (e.g. OpenSimplex/Simplex/Perlin family) with continuous coordinates and phase progression. The same seed should reproduce a scene. Audio changes parameters gradually, not the seed every frame.

Use noise for:
- Slow liquid deformation.
- Aurora/organic drift.
- Soft texture variation.
- Hearth's restrained flicker envelope.

### B. Multi-wave interference

Combine a small number of bounded oscillators with different spatial frequencies, temporal speeds and phase offsets. Avoid obvious repetitive loops by selecting incommensurate but controlled speeds and a sufficiently long loop period.

For a 1D strip:
`x_i = i / max(1, N-1)`
`field_i(t) = sum_k a_k * sin(2*pi*(f_k*x_i + v_k*t) + phase_k)`

Map the normalized field through a smooth palette/warp function. Clamp and smooth modulation.

### C. Trippy mode composition

Suggested stack:
1. Monitor-derived frame / palette anchor.
2. Palette-aware gradient field.
3. Multi-wave interference field.
4. Low-amplitude noise warp.
5. Optional bass-driven warp amplitude.
6. Optional kick-driven transient impulse into phase/brightness mask.
7. Smooth colour/output stage and central safety guard.

Keep screen-derived content recognizable in the output. Add A/B tests proving audio-off retains Trippy styling and audio-on supplements it.

### D. Spotlight / transient pool

LedFx's merged Spotlight effect is a useful design reference: bounded active spot count, smooth per-spot fade, adaptive spawn rate from low/mid/high activity and transient boosts, optional gradient colouring. Implement a bounded ring buffer or fixed-capacity event array. Do not allocate a new unbounded event object on every frame.

### E. Colour processing

- Interpolate palette stops smoothly.
- Perform physically meaningful blending in linear-light space if the current pipeline supports it; inspect current colour semantics before adding conversions.
- Consider perceptual colour interpolation for palette transitions, then convert consistently into the existing pipeline.
- Avoid independent per-frame random HSV changes.
- Preserve monitor-derived colour relationships in monitor modes.

### F. 1D vs 2D

The current handoff specifies a 240-LED strip. A 1D algorithm can create waves, travelling gradients, interference, spotlight cones and noise modulation. It cannot reproduce true 2D radial kaleidoscopes/fractal tunnels with spatial detail. Keep 2D effects gated behind confirmed matrix topology.

## 3. Latency and prediction

Do not make early prediction part of the MVP. First measure:
- capture buffer delay
- analysis window delay
- processing time
- output frame scheduling
- network/device transport

Use monotonic timestamps. Add stable beat-grid tracking later only when confidence is high and user testing indicates a benefit. A BPM estimate alone is not enough to predict every upcoming beat correctly.

## 4. Safety / compositing

- Compose all effect layers first.
- Apply one authoritative global safety/output guard.
- Use a shared modulation budget to stop simultaneous effects from multiplying into excessive output.
- Ensure effects are bounded and decay to neutral.
- Test area, luminance change, red-flash behaviour, power and thermal caps.
- Preserve black cut, letterbox and protected-video behaviour.

The handoff's "3 whole-strip flashes per second" is a product safety limit, not by itself proof of WCAG 2.3.1 conformity. WCAG flash evaluation includes more than frequency. Do not claim certification from that single threshold.
