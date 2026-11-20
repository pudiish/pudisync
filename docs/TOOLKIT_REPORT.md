# Agent Completion Report: Experience Engine toolkit

Progress against the R&D toolkit in `docs/toolkit/` (brief `AGENT_MASTER_PROMPT.md`, template
`decisions/AGENT_OUTPUT_TEMPLATE.md`, checklist `ACCEPTANCE_TESTS.md`), as of 2026-11-21 on
`feature/color-engine` at `be17965` (PRs #19–#23). The design detail lives in
`docs/EXPERIENCE.md`; this report records status.

**Where the build departs from the toolkit, by the owner's decision.** After PR #19 shipped
Cinematic and Trippy and kept Pulse, the owner kept only **Vivid** and **Immersive** and asked
for the other three looks to be removed (PR #20). Everything in the toolkit about Cinematic,
Trippy and Pulse as looks is therefore *removed on purpose*, not missing. Audio stays an add-on
(*Follow the sound*) on Vivid and Immersive, and Beat bounce and Beat shake stay in Expert
settings with the Phase A changes.

## 1. Repository audit

| Claim | Confirmed? | Evidence / file path | Discrepancy / action |
|---|---|---|---|
| Output order `to_led → beat → zones → brightness → guard → quantize → transport` | Yes | `engine.OutputStage` | Kept. Sound modulation follows `to_led`; room frames join at zones. |
| One capture, one detector (`BassMeter`, 2048/512 at 48 kHz, 40–160 Hz) | Yes | `ambilight/beat.py` | Kept. The same FFT now also gives mid/high bands and a shared feature frame. |
| Audio captured only while something uses it | Yes | `engine.wants_sound` | Extended to *Follow the sound* and Room audio; never under Calm. |
| Pulse wave ±6 LEDs, 0.28 s; shake 3 LEDs sideways | Yes | `beat.py` | Phase A: ±18 LEDs, 0.45 s, dark-scene floor, brightness ripple. |
| Guard: 10% swing, 3/s, power 3800 mA, heat 3000 mA | Yes | `ambilight/safety.py` | Unchanged; all new output goes through it. |
| 240-LED 1D strip | Yes | `config.DEFAULTS` | All renderers are 1D; scene files must declare `strip_1d`. |
| Stereo / 5.1 capture | No: stream is mono | `plugins/capture_sck.py` | Not claimed; stereo pan not offered. |
| A plugin framework for modes | No | | None added; renderers are an allow-list. |
| Versioned settings and migrations | Partly: unknown keys dropped, values validated | `config.Config.load` | New keys default safely; scene files carry `schema_version: 1`. |
| Sound can be captured without the screen | Not directly (ScreenCaptureKit) | `capture_sck.py` | `open_sound`: a display stream with no screen output attached. |

## 2. Implementation summary

- **Monitor Sync:**
  - Looks are Vivid and Immersive, with screen colour always the base.
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
| `ambilight/experience.py` (new) | Noise and waves, audio envelopes, sound stage, Room Studio scenes, renderers and scene-file validation |
| `ambilight/beat.py` | Feature frame, Phase A beat changes, `fire()`, `tempo()`, `status()` |
| `ambilight/engine.py` | `wants_sound`, room mode, `OutputStage.finish`, enter/leave room, status `source`/`audio` |
| `ambilight/config.py` | Two built-in looks, migration from removed looks, new keys and their validation |
| `ambilight/plugins/capture_sck.py` | `open_sound`: sound without screen output |
| `ambilight/routes.py` | `/api/room/*`, `/api/beat/test`, import limited to 16 kB |
| `ambilight/backup.py`, `server.py` | New keys in backups; serves `room.js` |
| `ambilight/web/index.html`, `app.js`, `app.css`, `manifest.webmanifest` | Room tab, *Follow the sound*, live ambience, neon-sunset theme |
| `ambilight/web/room.js` (new, lazy) | Room Studio controls and the scene editor |
| `macapp/Sources/Hub.swift`, `Theme.swift` | Two built-in looks; the page's palette |
| `tests/test_hub.py` | `test_experience`, `test_room_api`, and updated page expectations |
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
| Full suite after PRs #20–#23 | **Not run** | The owner asked to build and push without full runs. |

## 5. Measurements

- **Analysis CPU:** 0.023 → 0.030 ms per 10 ms block, measured on an Apple M3 Mac.
- **Render CPU:** Vivid 0.156 → 0.162 ms per frame; room scenes 0.16–0.25 ms.
- **Bright/mid/dark output comparison** (`tools/beat_visibility.py`, Pulse values, one beat
  through the real output stage and guard):

  | Scene | LEDs changed >10% | Biggest change (of 255) | Max swing of strip mean |
  |---|---|---|---|
  | Bright | 32 → 194 | 112 → 112 | 0.2% → 0.4% |
  | Mid | 36 → 169 | 101 → 95 | 0.2% → 1.4% |
  | Dark | 35 → 136 | 3 → 27 | 0.0% → 0.5% |

- **Audio-to-output latency:** not measured. It needs camera day; the handoff's 50–70 ms
  estimate stands.
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
- No full test suite since PR #19 (see §4).
- The new page look has been checked only in Chrome at phone and iPad sizes, not on an iPhone.
- The Mac app has the new palette but not Room Studio, the panel shapes or the glow.
- Not started:
  - stereo pan (Phase B, the capture is mono)
  - per-band effects (Phase C)
  - latency measurement (Phase D)
- The page download budget rose from 43 to 47 kB gz for the theme.

### Toolkit acceptance checklist (`ACCEPTANCE_TESTS.md`)

| Area | Met (software tests) | Removed by owner | Open |
|---|---|---|---|
| A. Product | Monitor Sync works with audio off; audio is in the look, with no Audio mode; stale audio eases to neutral; Vivid and Immersive unchanged | Trippy and Cinematic items; Pulse keeping its behaviour | Real-LED look |
| B. Room Studio | Own destination; no screen capture; every scene runs without audio; audio optional; original Neon Coast; no loop seams; no multi-zone controls shown; switching destinations releases capture | | Live sound-only stream |
| C. Audio analysis | Kicks found, including 30 dB quieter; steady bass, hi-hats and silence don't trigger beats; CPU measured; no stereo claimed; audio kept in memory only | | |
| D. Rendering and safety | Every effect goes through the one guard; flashes stay under 3/s with every effect on; black stays black; even colour shows the ripple; bright/mid/dark table recorded | | Physical power and heat check |
| E. Compatibility | Old configs load; removed looks migrate; strict import; duplicate, rename, reset and export; audio Off by default | | |
| F. Evidence | Commands, CPU, table, page screenshots | | Latency; real-LED proof |

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
5. Room tab › each scene for a minute:
   - Lights within a second, with no flicker and no jumps.
   - Diagnostics shows capture `none`.
   - With Room audio on, the scene follows music and macOS shows its recording indicator.
6. Duplicate a scene, change a colour, then export it and import it back.
7. Throughout, Home's *Power limited* and *Flashes damped* flags stay off.
8. On the iPhone, open the page from the Home Screen in both light and dark mode: nothing runs
   off the side and the text is readable.
