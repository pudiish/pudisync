# Agent Completion Report: Experience Engine toolkit

Progress against the R&D toolkit in `docs/toolkit/` (brief `AGENT_MASTER_PROMPT.md`, template
`decisions/AGENT_OUTPUT_TEMPLATE.md`, checklist `ACCEPTANCE_TESTS.md`), as of 2026-11-21 on
`feature/color-engine`: PRs #19–#23, plus the final refinement (Trippy as a treatment, Neon
Coast, the capture probe, full validation) on `feat/trippy-treatment`. The design detail lives
in `docs/EXPERIENCE.md`; this report records status.

**Where the build departs from the toolkit, by the owner's decision.** After PR #19 shipped
Cinematic and Trippy and kept Pulse, the owner kept only **Vivid** and **Immersive** and asked
for the other three looks to be removed (PR #20). Everything in the toolkit about Cinematic,
Trippy and Pulse as looks is therefore *removed on purpose*, not missing. Audio stays an add-on
(*Follow the sound*) on Vivid and Immersive, and Beat bounce and Beat shake stay in Expert
settings with the Phase A changes. In the final refinement the owner brought **Trippy back as a
treatment**: a switch on top of Vivid or Immersive, never a look of its own or an audio mode.

## 1. Repository audit

| Claim | Confirmed? | Evidence / file path | Discrepancy / action |
|---|---|---|---|
| Output order `to_led → beat → zones → brightness → guard → quantize → transport` | Yes | `engine.OutputStage` | Kept. Sound modulation follows `to_led`; room frames join at zones. |
| One capture, one detector (`BassMeter`, 2048/512 at 48 kHz, 40–160 Hz) | Yes | `ambilight/beat.py` | Kept. The same FFT now also gives mid/high bands and a shared feature frame. |
| Audio captured only while something uses it | Yes | `engine.wants_sound` | Extended to *Follow the sound* and Room audio; never under Calm. |
| Pulse wave ±6 LEDs, 0.28 s; shake 3 LEDs sideways | Yes | `beat.py` | Phase A: ±18 LEDs, 0.45 s, dark-scene floor, brightness ripple. |
| Guard: 10% swing, 3/s, power 3800 mA, heat 3000 mA | Yes | `ambilight/safety.py` | Unchanged; all new output goes through it. |
| 240-LED 1D strip | Yes | `config.DEFAULTS` | All renderers are 1D; scene files must declare `strip_1d`. |
| Stereo / 5.1 capture | Stereo yes, 5.1 no | `plugins/capture_sck.py`; probe in `docs/EXPERIENCE.md` | `channelCount` 2 gives 2 non-interleaved channels; the hub keeps 1 and now downmixes if it ever gets 2. No surround claimed; stereo pan not offered. |
| Own sound left out | Yes | `setExcludesCurrentProcessAudio_(True)`; probe | A tone from the capturing process was silent with it on, heard with it off. |
| A plugin framework for modes | No | | None added; renderers are an allow-list. |
| Versioned settings and migrations | Partly: unknown keys dropped, values validated | `config.Config.load` | New keys default safely; scene files carry `schema_version: 1`. |
| Sound can be captured without the screen | Not directly (ScreenCaptureKit) | `capture_sck.py` | `open_sound`: a display stream with no screen output attached. |

## 2. Implementation summary

- **Monitor Sync:**
  - Looks are Vivid and Immersive, with screen colour always the base.
  - **Trippy treatment** (`trippy`, off by default): the screen's own colours bent along the strip
    by a bounded domain warp (at most 18% of its length) and rippled by interference waves, from
    seeded noise and phase accumulators. Black stays black and the dominant colours lead. With
    *Follow the sound*: bass → warp, mids → speed, highs → shimmer, kicks → impulses travelling out
    from the middle, all scaled by confidence and easing back when the sound goes stale. Tune ›
    Motion holds speed, warp, colour intensity and texture.
  - *Follow the sound* (`audio_react`: Off / Subtle / Balanced / Expressive, default Off) lets
    the bass make the brightness breathe by at most 6%.
  - Calm (`calm`) turns sound motion off and stops the capture.
  - Cinematic, Trippy and Pulse were built (PR #19), then removed at the owner's request (PR #20).
- **Audio add-on:**
  - One `BassMeter` publishes a normalised feature frame: low, mid and high energy, bass
    envelope, onset, confidence and freshness. When the data goes stale it eases back to neutral.
  - The beat effects got the Phase A changes (table in §5).
  - *Test beat* fires one beat without opening any sound.
  - There is no separate Audio mode.
- **Room Studio:**
  - It has its own tab and section with five original scenes: Neon Coast, Aurora, Deep Ocean,
    Golden Hour and Hearth. Neon Coast's 1980s-Miami palette uses no game art, marks or music.
  - Neon Coast, redrawn: midnight indigo, electric cyan, neon pink and a coral sunset, with
    crossing ripples and soft neon glints, its palette closing its own loop.
  - Scenes are drawn by deterministic 1D noise and waves using phase accumulators.
  - Quick controls: brightness, motion speed, colour intensity, sound and Reset.
  - My scenes: duplicate, edit colours and motion, rename, reset, delete, and JSON import/export.
  - It never starts screen capture.
- **Safety / privacy:**
  - Screen frames, beats and scenes all leave through `OutputStage.finish`, which applies zones,
    brightness, `safety.Guard` and quantize.
  - Effects are bounded: a beat adds at most 8% of full white, and audio mappings are capped at
    their depth × level.
  - Sound stays in memory only. Room Studio reads no screen pixels.
  - Imports cannot set safety, power, transport or network settings, or carry code.
  - See `docs/PRIVACY.md`.
- **Configuration / migration:**
  - New keys: `audio_react`, `calm`, the `room_*` settings, and `room_custom`, which is
    validated as a scene file.
  - A config left on Pulse, Cinematic or Trippy moves to Vivid. Backups carry the new keys.
- **Interface (owner requests beyond the toolkit, PRs #21–#23):**
  - A GTA 6-mood neon-sunset theme in soft rounded panels, using original art only: palm
    silhouettes drawn for this page, no Rockstar marks or fonts.
  - The page's ambience follows the live strip colours.
  - The LED preview strip and the hero image were removed, and the controls made more compact.
  - Wide screens show the panels in masonry columns.
  - The Mac app uses the same palette.

## 3. Files changed

`git diff --stat 9479069 be17965`: 44 files, +3698 / −219. 22 files outside the toolkit copy.

| Path | Purpose |
|---|---|
| `ambilight/experience.py` (new) | Noise and waves, audio envelopes, sound stage, `TrippyStage`, Room Studio scenes, renderers and scene-file validation |
| `ambilight/beat.py` | Feature frame, Phase A beat changes, `fire()`, `tempo()`, `status()` |
| `ambilight/engine.py` | `wants_sound`, room mode, `OutputStage.finish`, enter/leave room, status `source`/`audio` |
| `ambilight/config.py` | Two built-in looks, migration from removed looks, new keys and their validation |
| `ambilight/plugins/capture_sck.py` | `open_sound`: sound without screen output; `downmix`: reads the buffer's format, mono fallback |
| `ambilight/routes.py` | `/api/room/*`, `/api/beat/test`, import limited to 16 kB |
| `ambilight/backup.py`, `server.py` | New keys in backups; serves `room.js` |
| `ambilight/web/index.html`, `app.js`, `app.css`, `manifest.webmanifest` | Room tab, *Follow the sound*, live ambience, neon-sunset theme |
| `ambilight/web/room.js` (new, lazy) | Room Studio controls and the scene editor |
| `macapp/Sources/Hub.swift`, `Theme.swift` | Two built-in looks; the page's palette |
| `tests/test_hub.py` | `test_experience`, `test_trippy`, `test_room_api`, and updated page expectations |
| `tools/beat_visibility.py` (new) | Bright/mid/dark before-and-after table |
| `docs/EXPERIENCE.md` (new), `PRIVACY.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG.md`, `docs/toolkit/` | Design record, privacy, file map, toolkit copy |

## 4. Tests

All runs use port base 8850, never the user's 8790 or the live hub's 8080.

| Command | Result | Notes |
|---|---|---|
| `AMBILIGHT_TEST_PORT_BASE=8850 make test` (PR #19) | 1214 passed, 0 failed | Full suite, last full run. |
| `test_sync_modes`, `test_experience`, `test_bass_beats` (PR #20) | 89 passed, 0 failed | Targeted run after removing the three looks. |
| `test_one_interface` + layout review (PR #23) | 45 passed, 0 failed | Contrast in both themes, Mac palette parity, every control wired, page budget 47 kB gz, no overflow at phone and iPad sizes. |
| `macapp/build.sh test` and `macapp/build.sh` (PR #23) | 53 passed, 0 failed; builds | |
| `AMBILIGHT_TEST_PORT_BASE=8850 make test` (refinement, 2026-11-21) | 1224 passed, 3 failed | First full run since PR #19. The 3 failures came from PRs #21–#23: Settings ran 17 px off a 320 px phone (fixed: the column now shrinks below a long dropdown option), and one theme test still expected the retired porcelain background (fixed). |
| `test_trippy` (new), `test_experience` | 22 + 50 passed, 0 failed | Trippy, the sound, guard, Room Studio independence, downmix, Neon Coast. |
| `test_one_interface`, `test_backup`, `test_sync_modes` | 101 passed, 0 failed | Every new setting reachable from the page; backups carry them. |
| Phone and iPad layout review in Chrome (sandbox hub, Trippy on) | No overflow, no page errors | Home, Tune, Room, Settings at 390 px; Settings also at 320 px after the fix. |
| `macapp/build.sh test` and `macapp/build.sh` (refinement) | 53 passed, 0 failed; builds | |

## 5. Measurements

All on this Apple M3 Mac, synthetic signals through the real code (no device, no network).

- **Analysis CPU:** 0.023 → 0.030 ms per 10 ms block (PR #19). Re-measured 2026-11-21 with
  ScreenCaptureKit's real 20 ms buffers: 0.056 ms per buffer (0.028 ms per 10 ms), unchanged by
  the refinement.
- **Render CPU** (median per output frame, 2026-11-21, before → after the refinement):

  | Path | Before | After |
  |---|---|---|
  | Vivid / Immersive | 0.149 / 0.150 ms | 0.150 / 0.151 ms |
  | Vivid + *Follow the sound* | 0.150 ms | 0.151 ms |
  | Vivid + Trippy, sound off | n/a | 0.251 ms (p99 0.275) |
  | Vivid + Trippy, Expressive | n/a | 0.256 ms (p99 0.263) |
  | Room scenes (PR #19) | 0.16–0.25 ms | unchanged code path |

- **Bright/mid/dark output comparison** (`tools/beat_visibility.py`, Pulse values, one beat
  through the real output stage and guard):

  | Scene | LEDs changed >10% | Biggest change (of 255) | Max swing of strip mean |
  |---|---|---|---|
  | Bright | 32 → 194 | 112 → 112 | 0.2% → 0.4% |
  | Mid | 36 → 169 | 101 → 95 | 0.2% → 1.4% |
  | Dark | 35 → 136 | 3 → 27 | 0.0% → 0.5% |

  Re-run 2026-11-21 after the refinement: identical (Bright 194 / 112 / 0.4%, Mid 169 / 95 /
  1.4%, Dark 136 / 27 / 0.5%). Trippy's own bright/mid/dark table is in `docs/EXPERIENCE.md`:
  its strip mean stays within 7% of the plain look, frame-to-frame swing at most 0.13%, no
  flashes counted, and a dark scene is untouched.

- **Audio-to-output latency, software part only:** a kick is detected at the end of the 20 ms
  buffer it lands in: median 8.7 ms after its onset (2.7–14.7 ms, 58 of 58 kicks over 30 s,
  plus one false beat). The next output frame follows within 16.7 ms at 60 fps (8.3 ms on
  average), and computing it takes 0.25 ms. So about 17 ms median, 32 ms at most, from the
  sound reaching the hub to the frame leaving it. **Not measured:** macOS's delay delivering the
  sound, Wi-Fi, the board and the LEDs. The end-to-end sound-to-light figure needs camera day.
- **Hardware tested:** none. No change has been seen on the real strip.

## 6. Compatibility

- **Existing settings preserved:**
  - Vivid and Immersive render exactly as before with the sound off (tested frame for frame).
  - Your own saved looks still apply.
  - New keys default to off or idle.
- **Migrations:**
  - A config on Pulse, Cinematic or Trippy moves to Vivid whole.
  - For former Pulse users, this sets Beat bounce and Beat shake to 0. To get the beat back,
    turn them up in Expert settings.
  - This departs from the toolkit's "Pulse keeps its behaviour", by the owner's choice.
- **Rollback:** revert the merges. Older builds drop the extra `config.json` keys on load.

## 7. Limitations / not verified

- Nothing has been seen on the real LEDs: not *Follow the sound*, not the beat effects, not the
  room scenes.
- Room Studio's sound-only stream (`open_sound`) runs only against a stand-in in tests. It has
  not been run live.
- The new page look has been checked only in Chrome at phone and iPad sizes, not on an iPhone.
- The Mac app has the new palette but not Room Studio, the panel shapes or the glow.
- Trippy has not been seen on the LEDs, and its kick impulses were tuned on numbers alone.
- Not started:
  - stereo pan (Phase B: stereo capture works, see §1, but no effect uses it)
  - per-band effects (Phase C)
  - latency measurement (Phase D)
- The page download budget rose from 43 to 47 kB gz for the theme.

### Toolkit acceptance checklist (`ACCEPTANCE_TESTS.md`)

| Area | Met (software tests) | Removed by owner | Open |
|---|---|---|---|
| A. Product | Monitor Sync works with audio off; audio is in the look, with no Audio mode; stale audio eases to neutral; Vivid and Immersive unchanged; Trippy distinct with audio off; audio modifies Trippy without replacing the screen's colours | Cinematic items; Pulse keeping its behaviour | Real-LED look |
| B. Room Studio | Own destination; no screen capture; every scene runs without audio; audio optional; original Neon Coast; no loop seams; no multi-zone controls shown; switching destinations releases capture | | Live sound-only stream |
| C. Audio analysis | Kicks found, including 30 dB quieter; steady bass, hi-hats and silence don't trigger beats; CPU measured; stereo probed, not used, no surround claimed; audio kept in memory only | | |
| D. Rendering and safety | Every effect goes through the one guard; flashes stay under 3/s with every effect on; black stays black; even colour shows the ripple; bright/mid/dark table recorded | | Physical power and heat check |
| E. Compatibility | Old configs load; removed looks migrate; strict import; duplicate, rename, reset and export; audio Off by default | | |
| F. Evidence | Commands, CPU, tables, page screenshots, software latency | | End-to-end latency; real-LED proof |

## 8. Manual validation checklist (real strip)

1. Home › Vivid, *Follow the sound* Off: the strip matches the screen as before.
2. Set it to Balanced and play music: the brightness breathes with the bass. Pause the music:
   it settles within about a second.
3. Expert › Beat bounce and Beat shake, with music:
   - A wide wave runs on each kick.
   - A dark film scene glows on the beat.
   - Letterbox bars stay dark.
   - Test beat works with no music.
4. Calm on: no motion from the sound, and Home says Not listening.
5. Trippy on, sound Off, on Vivid then Immersive: the screen's colours flow and ripple, a black
   screen stays dark, letterbox bars stay dark. Turn *Follow the sound* to Expressive with music:
   it bends further on the bass and a pulse of light runs out from the middle on each kick. Calm:
   the motion carries on, the sound stops being heard. Trippy off: exactly the plain look.
6. Room tab › each scene for a minute:
   - Lights within a second, with no flicker and no jumps.
   - Diagnostics shows capture `none`.
   - Neon Coast reads as indigo water with cyan, pink and coral neon gliding through it.
   - With Room audio on, the scene follows music and macOS shows its recording indicator.
7. Duplicate a scene, change a colour, then export it and import it back.
8. Throughout, Home's *Power limited* and *Flashes damped* flags stay off.
9. On the iPhone, open the page from the Home Screen in both light and dark mode: nothing runs
   off the side and the text is readable.
