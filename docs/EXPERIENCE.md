# Experience engine: Monitor Sync styles, audio as an add-on, Room Studio

> **2026-11-20, later:** the owner kept Vivid and Immersive and removed the Pulse, Cinematic
> and Trippy looks, with the code only they used. *Follow the sound* is now one setting
> (`audio_react`: off/subtle/balanced/expressive) that breathes Vivid or Immersive; Beat bounce
> and Beat shake stay in Expert settings with the Phase A changes below. Sections on Cinematic,
> Trippy and per-style levels describe the first version and no longer apply.

> **2026-11-21:** Trippy is back as a **treatment** on top of Vivid or Immersive, not a look
> (section below). Neon Coast was redrawn, and ScreenCaptureKit's sound was probed on this Mac
> (section below). The audit and measurements further down are from 2026-11-20 unless marked.

Built 2026-11-20 from the R&D toolkit in `docs/toolkit/` (brief: `AGENT_MASTER_PROMPT.md`,
plan: `EXECUTION_PLAN.md`, requirements and baseline: `baseline/`). Code:
`ambilight/experience.py`, `ambilight/beat.py`, the output stage in `ambilight/engine.py`.

## The model

Two destinations, one output path:

```
Monitor Sync: screen -> grade -> smoothing -> to_led -> style (Natural/Cinematic/Trippy) -> beat (Pulse)
Room Studio:  scene palette + 1D noise/waves -> to_led (calibration only) -> × scene brightness
both:         -> zones -> × brightness -> safety guard (power, heat, anti-strobe) -> quantize -> WLED
```

- **Looks** (Home › Style): Vivid, Immersive, Pulse, **Cinematic**, **Trippy**. Each built-in
  look now names its `style`; Vivid, Immersive and Pulse are `none` and render exactly as before.
- **Follow the sound** (Home, under the looks): Off, Subtle, Balanced or Expressive, stored per
  style in `audio_react`, Off for everyone until chosen. There is no separate audio mode.
- **Calm** (`calm`): no motion from the sound and no sound captured, one tap.
- **Room Studio** (its own tab and section): five scenes, quick controls, your own scenes.
  It never starts screen capture.

## Repository audit: toolkit claims against the code

| Claim (toolkit / handoff) | In code? | Where | Action |
|---|---|---|---|
| Output order `to_led -> beat -> zones -> brightness -> guard -> quantize -> transport` | Yes | `engine.OutputStage.frame` | Kept; styles slot in after `to_led`, before beat. Room frames join at zones. |
| One capture, one detector (`BassMeter`, 2048/512 at 48 kHz, 40–160 Hz) | Yes | `beat.py` | Kept; the same FFT now also yields mid/high bands and the feature frame. |
| Audio captured only while `bass_bounce`/`bass_shake` > 0 (`engine.wants_sound`) | Yes | `engine.py` | Extended: also a style's sound level; never under Calm. Room has `room_wants_sound`. |
| Wave ±6 LEDs, 0.28 s; shake 3 LEDs sideways | Yes | `beat.py` | Phase A: ±18 LEDs, 0.45 s, dark floor, brightness ripple (below). |
| Guard: 10% swing, 3/s, 15% damping; power 3800 mA; heat 3000 mA | Yes | `safety.py` | Unchanged. Every new frame goes through it. |
| Pulse is the only look with audio (0.8 / 0.5) | Yes | `config.PROFILES` | Kept; values unchanged. |
| 240-LED 1D strip | Yes (default `led_count` 240) | `config.DEFAULTS` | All renderers are 1D; scene files must say `strip_1d`. |
| Stereo / 5.1 available | No: stream is set to 1 channel | `capture_sck.py` | Not claimed. `stereo_pan` not offered (Phase B, gated on the B5 spike). |
| A plugin framework for modes | No | | None added: renderers are an allow-list in `experience.RENDERERS`. |
| Settings are versioned and migrated | Partly: unknown keys dropped, values validated on load | `config.Config.load` | New keys default safely; old configs and looks load unchanged (tests). Scene files carry `schema_version: 1`. |
| Room Studio can capture sound without the screen | Not directly: ScreenCaptureKit hears only through a display stream | `capture_sck.py` | `open_sound`: a display stream with no screen output attached, 2x2 at 1 fps. |

## Trippy, a treatment (2026-11-21)

`trippy` (Home, under the style; off by default) works on the LED levels after `to_led`, in place
of *Follow the sound*'s breathing, and before the beat effects (`experience.TrippyStage`):

- **Domain warp, bounded.** Each LED reads the screen's colour from a nearby position:
  `x + 0.18 × warp × noise(2.5x + 0.6 × waves(x) + drift)`. No colour moves more than 18% of the
  strip, so the screen's composition and dominant colours stay (tested: a screen two thirds blue
  stays 163 of 240 LEDs blue; a red screen stays red on every LED; black stays exactly as dark).
- **Interference waves:** three incommensurate travelling sines brighten and dim the colours by at
  most ±22%, plus a fine shimmer (Texture). Colour intensity pushes saturation, keeping hue.
- **Deterministic:** seeded value noise and phase accumulators; the same moment gives the same frame.
- **With *Follow the sound*** (the same Off/Subtle/Balanced/Expressive setting, the same one
  `BassMeter`): bass envelope → warp (+0.25), mids → speed (up to +70%), highs → texture (+0.12),
  and each detected kick sends an impulse out from the middle of the screen (±4% of the strip
  wide, 0.9 strip lengths a second, gone in 0.6 s, at most +25% light). Each is scaled by the
  detector's confidence, from 0 at the mapping's minimum to full at 1, and eases back to neutral
  when the sound goes stale. Calm gives exactly Trippy without the sound.
- **Off:** Vivid and Immersive are frame for frame as before (tested). Room Studio ignores it.
- Tune › Motion: speed, warp, colour intensity, texture. Backups carry them.

| Even scene | Trippy | LEDs >10% off plain | Strip mean vs plain | Max frame swing of mean | Flashes |
|---|---|---|---|---|---|
| Bright | sound off | 155 | 100% | 0.02% | 0 |
| Bright | Expressive, kicks 2/s | 152 | 100% | 0.03% | 0 |
| Mid | sound off | 223 | 105% | 0.02% | 0 |
| Mid | Expressive, kicks 2/s | 223 | 107% | 0.13% | 0 |
| Dark | either | 0 | 100% | 0.00% | 0 |

The guard counts a 10% swing as a flash. On a bright scene the power limit holds the mean at 100%.

## ScreenCaptureKit's sound, probed (2026-11-21, macOS 27.2.1, M3)

An audio-only display stream (no screen output, 2x2 at 1 fps), as `open_sound` opens it:

| Asked | Got | Layout | Per buffer |
|---|---|---|---|
| `channelCount` 1 | 1 channel, 48 kHz float32 | non-interleaved | 960 frames (20 ms) |
| `channelCount` 2 | 2 channels, 48 kHz float32 | non-interleaved: all of L, then all of R | 960 frames, 1920 floats |

- **Stereo is configurable** and honoured; 5.1 is not something the API offers, and none is claimed.
- **Silence still arrives** as buffers of zeros, so freshness is not lost in a quiet room.
- **Own-process exclusion works:** a tone played by the capturing process was absent with
  `excludesCurrentProcessAudio` on (RMS 0.00000) and present with it off (0.034); a tone from
  another process was heard (0.034).
- **Mono fallback:** the hub still asks for one channel. `mono_samples` now reads the buffer's
  own format and mixes two channels down (`capture_sck.downmix`), since reading a stereo buffer as
  one run of samples would garble time and pitch.
- **Lifecycle and permission:** sound is captured only while `wants_sound` or `room_wants_sound`
  is true; Calm and turning the effect off close the stream. Without Screen Recording permission
  the stream fails to start, the error names the permission (`_explain`), Room Studio says it
  carries on without the sound, and it tries again every `RETRY_DISPLAY_S`, not in a tight loop.
  Not exercised here: revoking permission mid-stream.
- Stereo pan stays unoffered: no effect uses it yet (toolkit Phase B).

Probe: a throwaway script, not shipped; it played a 0.05-amplitude 440 Hz tone for 2 s per case.

## Monitor Sync styles

- **Natural (Vivid, Immersive, Pulse)**: unchanged with the sound off (tested frame for frame).
  With the sound on, the bass breathes the brightness by at most 6% × level.
- **Cinematic**: Immersive's flood with a slower glide (0.22), contrast 1.4, 4000 K, and a faint
  drift of light along the strip (`cinematic_motion`). Sound: bass breathes it (≤5%), onsets gated
  at confidence 0.65 add a slow swell (≤8%). No speech detection is claimed.
- **Trippy**: the screen's own colours sampled at warped positions (waves + noise), folded three
  times along the strip like a 1D kaleidoscope, saturated and shimmering. Moves on its own with
  the sound off; the sound adds warp (bass), a phase kick (onsets), speed (mids) and texture
  (highs). Every colour comes from the screen frame, so a red screen stays red and black stays black.
  Controls: speed, warp, colour intensity, texture (Tune › Motion).
- **Pulse**: unchanged settings; the effects themselves were made visible (Phase A).

## Pulse, Phase A: before and after

`python3 tools/beat_visibility.py [checkout]`, Pulse settings, 240 LEDs, one full beat through the
real output stage and guard at 60 fps, on an even warm colour:

| Scene | LEDs changed >10% | Biggest change (of 255) | Lasts | Max frame swing of the strip's mean |
|---|---|---|---|---|
| Bright (143) | 32 → **194** | 112 → 112 | 267 → **400 ms** | 0.2% → 0.4% |
| Mid (58) | 36 → **169** | 101 → 95 | 283 → **433 ms** | 0.2% → 1.4% |
| Dark (2) | 35 → **136** | 3 → **27** | dominated by dithering in both | 0.0% → 0.5% |

What changed: wave ±18 LEDs (was 6), fade 0.45 s with a 30 ms rise (was 0.28 s, instant), a floor
of the scene's own hue at 36/255 so dark scenes glow on the beat (a frame with no light adds
nothing), a whole-strip budget of 8% of full white per beat (under the guard's 10% flash swing),
and Shake as a brightness ripple along the strip (it showed nothing on an even colour before).

## Room Studio

Scenes (all original; Neon Coast is a 1980s-Miami palette, no game art, marks or music):
Neon Coast, Aurora, Deep Ocean, Golden Hour, Hearth. Each is a declarative file (palette stops,
an allow-listed renderer, bounded parameters, audio mappings) drawn by deterministic noise and
waves, advanced by phase accumulators, so speed changes never jump and nothing loops with a seam.

- Starting a scene loads the board's sync preset (as screen sync does) and runs the output thread
  only. Screen sync and a scene never run together: each start stops the other's threads.
- Colours go through `to_led` with the person's own LED calibration (strip balance, LED gamma,
  black level, hue balance) but without the screen-only stages (contrast curve, adaptive
  brightness, dark lift, warmth), then × scene brightness × Room brightness, then zones, master
  brightness and the guard.
- Quick controls: Room brightness, motion speed, colour intensity, Follow the sound, Reset.
- Your scenes: duplicate any scene, edit a copy's colours and motion, rename, delete, reset a copy
  to the built-in it came from, export and import JSON (`/api/room/import`, 16 kB).

Scene file, as exported (`/api/room/export?id=aurora`):

```json
{"schema_version": 1, "id": "aurora", "name": "Aurora", "experience_type": "room_scene",
 "renderer": "aurora_1d",
 "parameters": {"motion_speed": 0.12, "colour_intensity": 0.62, "warp_strength": 0.28, "texture_amount": 0.12,
                "impact_amount": 0.0, "noise_scale": 0.6, "wave_count": 3, "brightness": 0.65},
 "palette": [{"position": 0.0, "hex": "#071B2D"}, {"position": 0.3, "hex": "#087F75"}, "…"],
 "audio_reactivity": {"supported": true, "default_level": "off", "mappings": [
   {"feature": "bass_envelope", "target": "motion_speed", "depth": 0.06, "attack_ms": 700,
    "release_ms": 2000, "minimum_confidence": 0.35}]},
 "compatibility": {"topology": "strip_1d", "requires_monitor_frame": false}}
```

Import is strict: unknown fields, renderers, features, targets or schema versions are refused;
numbers are clamped; nothing can name an address, code, or a safety, power or transport setting.

## The shared sound features

`BassMeter.features(now)`: `valid`, `confidence` (0 in silence), `low_energy` (40–160 Hz),
`mid_energy` (250–2000 Hz), `high_energy` (4–12 kHz), `bass_envelope` (30 ms attack, 350 ms
release), `onset_strength`, each 0..1 against the band's recent peak (25 dB range, falling
0.25 log10/s). Older than 0.25 s means stale: consumers ease back to neutral through their own
release times. Status shows `audio.bass_level`, `beats`, `bpm` (median gap of the last 8
beats, shown only; nothing fires early) and whether the sound is wanted.

## Measurements (Apple M3, this Mac)

| | Before | After |
|---|---|---|
| Analysis per 10 ms block | 0.023 ms | 0.030 ms (three bands + features) |
| Output frame, Vivid | 0.156 ms | 0.162 ms |
| Output frame, Pulse | 0.157 ms | 0.159 ms |
| Output frame, Cinematic / Trippy | n/a | 0.18 / 0.27 ms |
| Output frame, room scenes | n/a | 0.16–0.25 ms |

Audio-to-light latency was not measured (needs camera day); the analysis path is unchanged, so the
handoff's 50–70 ms estimate stands.

## Safety and privacy

- One guard: styles, beats and scenes all leave through `OutputStage.finish` (zones, brightness,
  `safety.Guard`, quantize). Tested: every effect on, Expressive sound, beats three a second on a
  bright scene: the guard never counts more than three flashes a second.
- Effects are bounded: audio mappings cap at their depth × level; a beat adds at most 8% of full
  white; room swells cap at 8%.
- Sound stays in memory (the same 43 ms window) and is captured only while something uses it;
  Calm closes it. Room Studio never reads screen pixels. See `docs/PRIVACY.md`.
- Imported scenes cannot carry safety, power, transport or network settings, or code.

## Compatibility

- Configs from before: new keys take their defaults (style `none`, every sound level Off, Calm
  off, Room idle). Pulse keeps `bass_bounce` 0.8 and `bass_shake` 0.5. Tested.
- Looks saved before styles existed apply with style `none`.
- Backups carry the new settings in the Looks group; nesting limit raised to 8 for scene files.
- Rollback: revert the merge; the extra keys in `config.json` are dropped on load by older builds.

## Not verified, and next steps

- **Not seen on the real strip.** All of this is tested in software against the output stage and
  a fake board. Look at each style and scene on the LEDs, with and without music.
- **Room Studio's sound-only stream** (`SCKCapture.open_sound`) is not exercised live by the tests
  (they use a stand-in); check that a scene follows music, and that macOS's indicator shows.
- Latency (handoff G6, Phase D), stereo (Phase B), per-band effects (Phase C) are not started.
- The Mac app does not yet show Room Studio or the new looks' controls (the phone page does).
- Cinematic's dialogue resistance is a high confidence gate, not speech detection.

### Manual check on the real strip

1. Home › Style › Trippy, sound Off: the strip flows with the screen's colours; a black screen is
   dark. Set Follow the sound to Balanced, play music: motion speeds and ripples on kicks; pause
   the music: it settles back within about a second.
2. Pulse with music: each kick sends a wide wave from the middle; a dark film scene glows on the
   beat; letterbox bars stay dark. Press Test beat with no music.
3. Calm on: no motion from the sound; Home says Not listening.
4. Room tab › Neon Coast: lights within a second, with nothing captured (Diagnostics shows capture
   `none`). Try each scene for a minute: no flicker, no jumps. Duplicate one, change a colour, export
   it, import it back.
5. While any of this runs, watch Home's flags: Power limited and Flashes damped should stay off.
