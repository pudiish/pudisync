# PudiSync audio engine: requirements handoff

Nov 20, 2026 · @pudi

## Summary

PudiSync's beat effects (bounce wave, shake) are too local, too short and too dim in dark scenes to be felt: at Pulse settings only about 10% of the strip moves, for roughly 150 ms, and a dark scene changes by 2/255. This document specifies a six-phase rebuild of the audio engine. Each phase ships alone, behind its own setting, under the existing safety guard. Baseline build: `2.0.0-dev+9479069` (2026-11-20).

**Goal.** Audio-driven light that viewers can see and feel on a strip behind a screen, in music and in films, without breaching the flash limit (3 whole-strip flashes per second, WCAG 2.3.1).

**What each team needs to do**

- **Product management:** confirm phase order and the scope of the first release (we propose Phase A, then B). Decide Immersive/Vivid vs a separate Pulse look (question 5, section 7).
- **Development:** build Phase A first. Every change is measured with the baseline table in section 2, before and after.
- **R&D:** answer the five research questions in section 7. Question 3 gates Phase B and question 4 gates Phase D; questions 1, 2 and 5 shape Phases C and E.

**Out of scope:** changes to the colour pipeline (modes, grade, to\_led), capture, and the WLED transport. Audio stays in memory only and is never stored, logged or sent.

## Current state and measured gaps

The beat detector works; the visible effect does not. On synthetic tracks every kick of a 120 BPM track was heard within 12 ms, a track 30 dB quieter behaved the same, and steady bass, hi-hats and silence produced no false beats. Detection costs 0.02 ms of CPU per 10 ms block (`ambilight/beat.py`, `BassMeter`).

Audio is captured only while `bass_bounce` or `bass_shake` is above 0. Today only the Pulse look sets them (0.8 and 0.5). The effect is applied after `to_led` and before zones, brightness and the safety guard.

**Baseline: effect strength at the peak of a beat (Pulse settings, 240 LEDs, measured on the output stage).** Every Phase A change is compared against this table.

| Scene | LEDs changed >10% (of 240) | Biggest change (of 255) | Lasts |
| --- | --- | --- | --- |
| Bright (level 143) | 23–29 | 112 | \~150 ms |
| Mid (level 58) | 26–34 | 81 | \~150 ms |
| Dark (level 2) | ≤17 | 2 | \~80 ms |

**Gaps to close**

| ID | Gap | Evidence | Closed in |
| --- | --- | --- | --- |
| G1 | Too local | Wave is ±6 LEDs and fades in 0.28 s, so it travels about 45 LEDs: roughly a tenth of the strip moves | A |
| G2 | Too short | Most of the energy is gone in about 100 ms | A |
| G3 | Multiplicative only | Dark LEDs ×2 are still dark; a dark scene moves by 2/255, and dark film scenes are where music matters most | A |
| G4 | Shake invisible on even colour | Sliding a uniform colour changes nothing; `spread` also softens edges and gradients | A |
| G5 | Mono and kick-only | No left/right, no snare or hats, no tempo, so nothing anticipates the beat | B, C, D |
| G6 | Latency | About 50–70 ms from sound to light (10 ms hop, 21 ms window delay, up to 20 ms block, 16 ms output frame, network). Viewers notice light lagging sound beyond about 45 ms (ITU-R BT.1359) | D |
| G7 | Not switched on | The settings journal shows no Pulse or beat change after the build went live | A (visibility), PM |

## Roadmap

&#91;embedded content: roadmap · 6 phases, 3 gates\]

A has no dependencies and goes first. D uses the status fields from A1; E needs B (side-chase) and C (band choice). The three gates are the B5 channel-count spike, the question 4 go/no-go on early firing, and the E5 decision on which looks get audio motion.

## Functional requirements

There are 30 requirements across six phases. Each phase ships on its own, behind its own setting, under the same safety guard. Priority is MoSCoW (Must, Should, Could) within the phase. Where an acceptance criterion says "above baseline", the numeric target is set at kickoff from the table in section 2; this document does not invent one.

### Phase A: make it visible and measurable (small)

| ID | Requirement | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| A1 | Status reports `bass_level`, `beats` and `bpm`. | Must | All three appear in the status payload and update live. |
| A2 | The page shows a live bass meter that blinks on each beat. | Must | A beat in the audio produces a visible blink on the page. |
| A3 | A **Test beat** button fires a synthetic beat with no audio running. | Must | Pressing it triggers the effect through the normal output path and the guard; no audio capture is opened. |
| A4 | Widen and lengthen the bounce wave: ±15–25 LEDs (today ±6), fade 0.4–0.5 s (today 0.28 s). | Must | Re-run the section 2 table: LEDs changed >10% and duration are above baseline. |
| A5 | Add an additive floor so dark scenes glow: `+ scene colour × k`, never below a minimum. | Must | Dark scene (level 2): biggest change is above the 2/255 baseline. |
| A6 | Shake modulates brightness along the strip instead of shifting colour. | Must | Visible on an evenly coloured frame, where the current shake changes nothing. |
| A7 | Measure every A change before and after with the section 2 table, and record the results in the pull request. | Must | Table present for bright, mid and dark scenes in each PR. |

### Phase B: stereo, left/right (medium)

| ID | Requirement | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| B1 | Capture stereo (`setChannelCount_(2)`); fall back to mono if stereo is unavailable. | Must | Two channels arrive at 48 kHz; mono path still works. |
| B2 | One `BassMeter` per channel, plus a pan estimate `p = (L−R)/(L+R)` in −1..1. | Must | A hard-left test kick gives p near −1, hard-right near +1, centre near 0. |
| B3 | The wave origin moves with pan. | Must | A left-panned kick starts on the left of the strip, right-panned on the right, centre from the screen centre as today. |
| B4 | Frame layout: left channel drives the left edge, right the right edge, the top edge takes their mean. | Should | Verified on a frame layout with a hard-panned test track. |
| B5 | R&D spike: find out what channel counts ScreenCaptureKit really delivers. Check per-app capture, and what Netflix and Prime in Chrome deliver. | Must (before B is committed) | One-page finding: real 5.1/7.1 is available or not; stereo downmix confirmed or not. |

### Phase C: multi-band, a different motion per instrument (medium)

Per-band adaptive thresholds work as in the current detector (mean + 1.5 σ of the last second of rises). Use mel or third-octave bands; LedFx and WLED Sound Reactive both use banded FFTs.

| ID | Band | Cue | Effect | Priority |
| --- | --- | --- | --- | --- |
| C1 | Sub/kick, 30–90 Hz | Onset | Bounce wave | Must |
| C2 | Bass line, 90–250 Hz | Envelope | Slow "breathe" of saturation and brightness, at most 8%, under the strobe line | Should |
| C3 | Snare/clap, 1–4 kHz with broadband flux | Onset | Shake, or a sideways sweep | Should |
| C4 | Hats/air, 6–16 kHz | Envelope | Shimmer: fine per-LED brightness noise of ±5% | Could |
| C5 | All bands | Per-band adaptive threshold | Each band tuned and measured on its own, with no false triggers on steady tones or silence | Must |

### Phase D: tempo lock, the "locked in" feel (larger)

| ID | Requirement | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| D1 | Estimate BPM by autocorrelating the onset envelope over 4–8 s. Reference: Ellis 2007 dynamic-programming beat tracking; a phase-locked loop on the beat grid is the lighter real-time form. | Must | Estimated BPM matches the known tempo of the synthetic tracks and is shown in status (A1). |
| D2 | Predict the next beat and fire the effect about 50–70 ms early to cancel the latency in G6. | Must | Light-to-sound lag, measured on camera day, is lower than the reactive path. |
| D3 | Report a confidence value and fall back to reactive mode when it is low (speech, ambient music). | Must | No early firing on speech or beatless tracks. |
| D4 | Check that early firing does not read as "ahead" on tracks with a loose beat (research question 4). | Should | Written finding with a go/no-go for shipping D2 on by default. |

### Phase E: effect library and looks

| ID | Requirement | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| E1 | Effect library: bounce wave, shake, breathe, colour kick (a small hue step on the downbeat, within the guard), shimmer, side-chase (alternating left/right on a stereo beat). | Should | Each effect can be switched on alone and measured with the section 2 method. |
| E2 | Each effect has its own depth, attack, release and band choice. | Should | All four settable per effect. |
| E3 | Looks bundle effects. Pulse stays bounce + shake as today. | Should | Existing Pulse behaviour unchanged unless the PM decides otherwise (E5). |
| E4 | Music-only intensity: lower in films so dialogue does not pump the room. Options: speech detection, or a per-app filter (ScreenCaptureKit can include or exclude apps). | Could | Dialogue-only film clip produces little or no audio motion; music clip does. |
| E5 | Product decision: audio motion at low depth in Immersive and Vivid, or only in a separate look (Pulse). | Must (PM) | Decision recorded before E3 starts. |

### Phase F: safety and comfort (always)

| ID | Requirement | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| F1 | The guard stays authoritative: power, heat, and at most 3 whole-strip flashes a second. | Must | No effect bypasses `safety.py`; flash count never exceeds 3 per second in tests with every effect on. |
| F2 | A **Calm** switch turns all audio motion off with one tap, for photosensitive viewers. | Must | One tap; no audio motion afterwards, and audio capture stops. |
| F3 | Audio stays in memory only: never stored, logged or sent. | Must | Code review and a log check show no audio samples written anywhere. |
| F4 | Per-app capture (music apps only) is offered as an option to keep calls and other sound out. | Could | Selecting an app excludes all other sound from analysis. |

## Safety and non-functional requirements

Every audio effect must live under the existing output guard (`safety.py`); the anti-strobe rule is the hard ceiling for all motion. These limits apply to every phase.

| ID | Area | Requirement | Current value in code |
| --- | --- | --- | --- |
| N1 | Anti-strobe (WCAG 2.3.1) | A whole-strip swing of 10% or more counts as a flash. More than 3 per second makes output move only 15% per frame toward the new value until the flashing stops. No effect may bypass or weaken this. | 10% swing, 3 per second, 15% per frame |
| N2 | Power | Frame is scaled so estimated current stays within the limit. With a bed strip on the same adapter, both share 80% of supply. | `power_limit_ma` 3800 mA, 55 mA per white LED, recovery 0.8 s; `supply_ma` 5000 |
| N3 | Heat | A bright scene held for minutes settles to the sustained limit. | `sustained_ma` 3000 mA, time constant 120 s |
| N4 | Latency | Light follows sound within 45 ms where possible (the detectability threshold in ITU-R BT.1359). Today it is about 50–70 ms; Phase D closes the gap by prediction. | \~10 ms hop + \~21 ms window delay + up to 20 ms block + 16 ms output frame + network |
| N5 | CPU | Detection must stay cheap enough to run beside capture and output at 60 fps. Each phase reports its cost per 10 ms block; the ceiling is set at kickoff. | 0.02 ms per 10 ms block (single band) |
| N6 | Privacy | Audio is processed in memory only, in a rolling window, and never stored, logged or sent. The app's own sound is excluded. | 2048-sample (43 ms) window; own process excluded |
| N7 | Capture scope | Audio is captured only while an audio effect is above 0. Switching it off closes the stream. | `engine.wants_sound` |
| N8 | Separation | Each phase sits behind its own setting. The default state of each new setting is a product decision (section 8). | Pulse is the only look with audio effects |
| N9 | Colour pipeline | Audio changes must not alter the colour pipeline (grade, `to_led`) or the settings that modes do not set (brightness, adaptive, hue\_balance, black\_level, led\_gamma, strip balance, calibration). | n/a |

## Test and verification plan

Four methods cover the work, and none needs music to be playing in the room. Every phase is signed off with the first method; D also needs the fourth.

| # | Method | What it shows | Phases |
| --- | --- | --- | --- |
| 1 | Output-stage measurement (the section 2 table): LEDs changed >10%, biggest change, and duration, for a bright, mid and dark scene at Pulse settings, 240 LEDs. Run before and after each change. | How visible the effect is | A, C, E |
| 2 | Synthetic tracks: generated kicks, a steady bass note, hi-hats, silence, at 120 BPM and 30 dB quieter, plus hard-panned and centre kicks. | Detector correctness and no false beats; pan direction | A, B, C, D |
| 3 | Recorded bass envelopes: numbers per 10 ms of real songs and films, never audio, replayed through the detector. | Tuning on real material without playing sound | C, D, E |
| 4 | Camera day: measure light-to-sound lag with the webcam latency tool next to an audio click. | Latency before and after prediction | D |

Regression checks that run with every change: flash count never exceeds 3 per second with all effects on (N1), power and heat limits hold (N2, N3), and no audio is written to disk or logs (N6).

## Research questions for R&D

Five questions need an answer before the later phases are committed; question 3 gates Phase B, question 4 gates Phase D, and questions 1, 2 and 5 shape Phases C and E.

| # | Question | Informs | Deliverable |
| --- | --- | --- | --- |
| 1 | Does the effect feel better as motion (waves, sweeps) or as intensity (breathe, kick), at the limits WCAG and the guard allow? | A, C, E | Short viewing test across both styles; recommendation on the default effect mix |
| 2 | How do WLED Sound Reactive, LedFx, Hyperion/HyperHDR audio and Philips Ambilight's audio styles map energy to light? Which of their effects work on a strip behind a screen rather than a room wall? | C, E | Comparison of the four systems and a shortlist of effects that transfer |
| 3 | Stereo: is a left/right split worth it for films (dialogue is centre) or only for music? | B, E | Finding with a yes/no per content type; includes the B5 channel-count spike |
| 4 | Prediction: is firing 50–70 ms early noticeable as "ahead" on tracks with a loose beat? | D | Go/no-go for D2 on by default, per D4 |
| 5 | Should audio motion apply in Immersive and Vivid at low depth, or stay a separate look (Pulse)? | E | Recommendation to the PM, who makes the decision (E5) |

## Risks, dependencies and decisions needed

The largest risk is that real surround audio is not available from system capture, which limits Phase B to stereo. Items marked "to verify" are engineering judgements from this review, not measurements.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| ScreenCaptureKit documents only 1 or 2 channels; Netflix and Prime in Chrome often deliver a stereo downmix. | No true surround; B and E limited to stereo. | B5 spike before B is committed; design B for stereo only. |
| Several effects on at once trip the anti-strobe rule, which then flattens all motion to 15% per frame. | Effects feel weaker precisely when more are enabled. | Test every effect combination against N1 (F1); keep C2 and C4 within their stated limits (8%, 5%). |
| Dialogue in films drives the effect and pumps the room. | Looks wrong in films. | E4: music-only intensity; speech detection or per-app filter. |
| Early firing looks "ahead" on loose-beat tracks. | Worse than the lag it removes. | D3 confidence fallback; D4 go/no-go before D2 is on by default. |
| The additive floor (A5) raises the light in dark scenes. To verify against `black_level`, the black cut and the protected-video glow. | Unwanted glow on black or letterboxed frames. | A5 keeps a minimum and respects the black cut; check dark and black frames in method 1. |
| Wider, longer waves and the floor draw more current. To verify with a bed strip on the same adapter. | Guard scales the whole frame down. | Measure current in method 1; the guard already limits it (N2, N3). |
| The effect was never switched on in the field (G7). | Work ships and nobody sees it. | A2 and A3 make it visible; PM decides the default (N8). |

**Dependencies.** A has none and goes first. B waits for the B5 spike. D uses the status fields from A1. E needs B (side-chase) and C (band choice per effect).

**Decisions needed**

- [ ] PM: confirm phase order and the scope of the first release (proposed: A, then B).
- [ ] PM: default state of each new audio setting (N8).
- [ ] PM: audio motion in Immersive and Vivid, or Pulse only (E5, question 5).
- [ ] Dev and R&D: numeric targets for A4, A5 and A6 against the section 2 baseline, and the CPU ceiling for N5, set at kickoff.

## Appendix: tunable parameters and baseline settings

All values come from the PudiSync technical reference for build `2.0.0-dev+9479069` (2026-11-20): taken from the code, or measured on the development Mac. All knobs sit in `ambilight/beat.py`, are safe to change, and the guard still applies. `PULSE_S` is also tunable.

| Knob | Current value | Meaning | Likely phase |
| --- | --- | --- | --- |
| `HOP` | 512 samples (10.7 ms) | One analysis step | D |
| `WINDOW` | 2048 samples (43 ms), Hann | FFT size; 23.4 Hz per bin | C |
| `BASS_HZ` | 40–160 Hz (FFT bins 2–6) | Kick and bass line | C |
| `SENSITIVITY` | Mean + 1.5 σ of the last second of rises | Adaptive beat threshold | C |
| `MIN_RISE` | 0.08 | Floor under the threshold | C |
| `SILENCE` | −4.0 (log10 energy; a full-scale bass note is about +2.4) | Gate | A |
| `REFRACTORY_S` | 0.35 s | Minimum gap between beats (at most 3 per second) | D |
| `WAVE_SPEED` | 160 LEDs/s | Speed of the bounce wave from screen centre | A |
| `WAVE_WIDTH` | ±6 LEDs | Width of the wave front | A (G1) |
| `WAVE_GAIN` | 1.2 | LEDs multiplied by `1 + 1.2 × wave`, plus the frame's mean colour × wave | A (G3) |
| `SHAKE_LEDS` | 3 LEDs | Maximum shift | A (G4) |
| `SHAKE_HZ` | 9 Hz | Shake frequency | A |
| `SHAKE_S` | 0.16 s decay constant | Shake length | A |

**Pulse look today:** `bass_bounce` 0.8, `bass_shake` 0.5, Response 0.50 (0.22 s), saturation 1.40, immersion 0.15, spread 0.30, enrich 1.00, contrast 2.0. Vivid and Immersive set both audio values to 0. Brightness, adaptive (0.6), hue\_balance (0.35), black\_level (0.03), led\_gamma (1.5) and strip balance are the person's own and are kept across looks.

**Output order, for placing any new effect:** `to_led` → beat (bounce, shake) → zones → × brightness → safety guard (power, heat, anti-strobe) → quantize or dither → UDP (DNRGB) or DDP → WLED board.
