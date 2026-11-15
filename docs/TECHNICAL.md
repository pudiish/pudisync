# PudiSync technical architecture

What runs where, how a frame of the screen becomes light on the wall, every
library and system service PudiSync leans on, and everything it does itself.
`docs/ARCHITECTURE.md` is the short file map and reading order; this is the
full picture. Line counts are as of November 2026.

## 1. The system in one picture

```
                 ┌──────────────────────────── Mac ─────────────────────────────┐
  screen ──────▶ │ capture plugin ─▶ processing ─▶ colour ─▶ smoothing ─▶ zones │
 (Quartz / mss)  │      (engine "sync" thread)       (engine "emit" thread)     │
                 │                     ─▶ safety ─▶ quantize ─▶ output plugin   │──UDP DNRGB / DDP / serial──▶ WLED board
                 │                                                       │      │                               (ESP8266)
                 │ mouse.py ─────────── HID reports ─────────────────────┼──────│──▶ Logitech G102 mouse             │
                 │                                                       │      │                                    ├─▶ monitor strip
                 │ HTTP server (server.py + routes.py, port 8080) ◀──────┼──────│── phone page (web/index.html)      └─▶ bed strip
                 │   │      ▲                                            │      │── Mac app (macapp/, SwiftUI)
                 │   │      └── SyncIntent polls the board every second ─┼──────│──▶ WLED JSON API (/json/state)
                 │   └──▶ WLEDClient (wled.py) ── JSON over HTTP ────────┘      │
                 └──────────────────────────────────────────────────────────────┘
  board's own page (board/index.htm, served from the board's flash) ──▶ WLED JSON API, works with the Mac off
```

Three programs make up PudiSync:

| Program | Language | Runs on | Size |
|---|---|---|---|
| The hub (`hub.py`, `ambilight/`) | Python 3.10+ | the Mac, started at login by launchd | about 6,000 lines |
| The phone page (`ambilight/web/index.html`) | HTML, CSS, JavaScript in one file | any browser, served by the hub | 2,459 lines |
| The Mac app (`macapp/`) | Swift, SwiftUI | the Mac | about 2,550 lines |

Plus the board's own page (`board/index.htm`, 447 lines), which the hub
uploads to the WLED board so the lights can be run with the Mac switched off.

The LED board runs **stock WLED** firmware. PudiSync does not change it; it
speaks WLED's public interfaces.

## 2. What runs, and how the parts talk

### Processes and threads (the hub)

| Thread | Started by | Job |
|---|---|---|
| main | `hub.py main()` | parses arguments, loads `config.json`, starts everything below, waits for Ctrl-C or launchd's stop |
| HTTP workers | `server.serve()` (`ThreadingHTTPServer`) | one short-lived thread per request |
| `sync` | `Engine.start()` | grabs the screen as fast as it can and turns it into per-LED target colours |
| `emit` | `Engine.start()` | at a steady frame rate, smooths toward the targets and sends bytes to the board |
| `intent` | `SyncIntent.start()` | asks the board once a second whether "Monitor sync" is chosen, and starts or stops the engine to match |
| host refresh | `HostCache` | re-resolves the board's `.local` name in the background when it goes quiet |
| `camera`, `camera-job` | `camrun.py` | webcam calibration (only on camera day) |
| flash | `Provisioner` | flashing WLED onto a new board over USB |
| `caffeinate` (child process) | `KeepAwake` | holds off idle sleep while sync runs |
| `dns-sd` (child process) | `discovery.Advertiser` | advertises the hub on Bonjour as "PudiSync" |

Capture and output are separate threads on purpose: capture speed varies with
the screen, and the output must stay steady or motion on the wall stutters.

### Ports and protocols

| From → to | Protocol | Port | Where |
|---|---|---|---|
| hub → board, colours | WLED UDP realtime, DNRGB (default) | 21324 | `plugins/output_wled_udp.py` |
| hub → board, colours | DDP (Distributed Display Protocol), the alternative | 4048 | `plugins/output_wled_ddp.py` |
| hub → board, colours | Adalight over USB serial, the wired alternative | USB | `plugins/output_serial.py` |
| hub → board, control | WLED JSON API (`/json/state`, `/json/info`, `/json/eff`, `/json/pal`, `/json/cfg`, `/upload`) | 80 | `wled.py`, `board.py`, `bed.py` |
| phones, Mac app → hub | HTTP GET, JSON replies | 8080 | `server.py`, `routes.py` |
| hub → mouse | USB HID feature reports (LIGHTSYNC) | USB | `mouse.py` |
| hub → new board | Improv Wi-Fi over serial, then esptool to flash | USB | `provision.py` |
| hub → GitHub | HTTPS, the latest WLED release, only when flashing a board | 443 | `provision.py` |
| hub → LAN | Bonjour (`_http._tcp`) through macOS's `dns-sd` | 5353 | `discovery.py` |

Every action is a plain GET so an iOS Shortcut can call it. That would let any
web page fire them, so `access.py` refuses requests a browser marks as coming
from another site, and any whose Host is not an IP address, `localhost`, a
`.local` name or this Mac's name (which stops DNS-rebinding tricks); and `PinGate` checks an optional PIN in
constant time with a lockout after five wrong guesses.

## 3. A frame's journey from screen to wall

Each step names the module and function that does it.

**On the `sync` thread** (`engine.Engine._loop`):

1. **Grab** a band of the screen: `capture_quartz.QuartzCapture.grab` (macOS
   `CGDisplayCreateImage`), or `capture_mss.MSSCapture.grab` elsewhere. Only
   the edges are read, not the whole screen.
2. **Find the picture**: `processing.find_picture` measures letterbox bars so
   black bars around a film don't feed black to the strip;
   `processing.find_frame` does the same round all four edges.
3. **Average per LED**: `processing.build_mapping` works out, once per layout,
   which screen columns each LED faces; `processing.slice_edges` and
   `edge_colors` average them in *linear* light; `processing.spread` blends
   neighbours a little; `build_fade` and `extend_edges` let the light spill past
   the screen's ends.
4. **Grade**: `color.grade` turns screen light into the colour the LED should
   show:
   - saturation in HSV, with skin and greys protected (`smoothstep` masks);
   - `immerse`: deep colours lifted, dull LEDs pulled toward the scene's colour;
   - `adaptive_gain`: each LED's brightness from what it faces;
   - `dark_lift`, `contrast_curve`, the white point (`blackbody_xy`, `adaptation`);
   - `enrich.apply`: the Enriched look, in Oklab.
5. Hand the targets to the output stage (`OutputStage.set_target`).

**On the `emit` thread** (`Engine.emit_loop`, paced by `_Pacer`), every frame:

6. **Smooth**: `smoothing.Smoother.step`, a critically damped spring per
   channel. It is integrated exactly, so motion is the same at any frame rate,
   and scene cuts snap instead of smearing (`cutting`).
7. **Fade on stale**: if capture has stopped for 2 s, the light fades out over
   1.5 s rather than freezing on the last frame.
8. **LED levels**: `color.to_led`, with the LED's gamma, the colour-match
   matrix and the black level.
9. **Parts of the strip**: `zones.composite` lays any lamp, solid-colour or
   off parts over the synced frame.
10. **Safety**: `safety.Guard.apply`:
    - caps the estimated current (`estimate_ma`) at the power limit;
    - settles long bright scenes to a sustained limit, against heat;
    - damps flashing past 3 a second (WCAG 2.3.1).
11. **Quantize**: `smoothing.Quantizer.apply` turns floats into bytes and only
    moves an LED once it has really changed, all three channels together, so
    slow fades keep their hue.
12. **Bed strip**: `bed.BedStage.frame` appends the bed's LEDs when the bed
    matches the screen (blend, mood or one side).
13. **Send**: the output plugin packs bytes into packets
    (`output_wled_udp.send`: DNRGB, up to 400 LEDs a packet, with a 2 s timeout so
    WLED hands control back if the hub dies).
14. **Mouse**: `Engine.follow_mouse` sends three zone colours from the bottom
    edge (`mouse.zone_colours`).

`telemetry.Telemetry` times every stage; the Settings tab shows the
percentiles. `recorder.Recorder` can save a clip of linear averages and replay
it through steps 4–11 for repeatable tuning.

## 4. The control side

- **`server.py`**: a `ThreadingHTTPServer`. It does four jobs:
  - serves the page and its assets (compressed with gzip for browsers that
    accept it, cached once per file);
  - answers `/api/...` through `routes.ROUTES`;
  - builds `/api/status`;
  - caches the board's state for 2 s, refetched at once after any write.
- **`routes.py`**: 41 API routes plus `/api/profile/<name>`, one small function
  each. They fall into these groups:
  - sync on/off/toggle, and all lights off;
  - looks (profiles): save, delete;
  - settings: one generic `/api/set`, checked by `config.validate`;
  - WLED pass-throughs: power, brightness, preset, effect, palette;
  - effects;
  - the strip setup wizard;
  - colour calibration;
  - bed setup;
  - camera;
  - USB serial ports;
  - flashing a board;
  - pairing QR codes;
  - diagnostics.
- **`config.py`**: one JSON file, validated per key (ranges, regexes, enums)
  and saved on every change. It also holds the built-in looks (Cinema, Gaming,
  Enriched, Vivid) and the user's own.
- **`intent.py`**: who decides sync. Sync is on exactly while WLED preset 1
  ("Monitor sync") is active on the board, so the phone, the Mac app and the
  board's own page all agree without talking to each other.
- **`wled.py`**: the WLED JSON client. It counts writes so the status cache
  knows when to refetch.
- **`hostcache.py`**: remembers the board's IP address, because the ESP8266's
  `.local` name stops answering now and then.
- **`board.py`**: uploads `board/index.htm` to the board (gzipped, multipart)
  and saves the sync preset.

## 5. The clients

| Client | Built with | Notes |
|---|---|---|
| Phone page `ambilight/web/index.html` | plain HTML, CSS and JavaScript | No framework or build step, and nothing loaded from the internet. Home / Tune / Settings, the colour wheel (`buildWheel`), parts editor, setup wizard, diagnostics. 35 KB over the wire. |
| Board page `board/index.htm` | plain HTML and JavaScript | Lives in the board's flash (under 30 KB), talks to WLED directly, so it works with the Mac off. |
| Mac app `macapp/` | SwiftUI and AppKit (macOS 14+) | Built with `swiftc` from the Command Line Tools (`build.sh`), no Xcode project. `Scene.swift` holds the logic it shares with the page; `Hub.swift` the store; `HomePage`, `TunePage`, `SettingsPage`, `ColourWheel`, `LEDFrame` the screens; a menu-bar panel and a Lights menu. |
| iOS Shortcuts, scripts | any HTTP client | every action is a GET URL |

The scene, colour-name, wheel and parts logic is written twice, in JavaScript
(`index.html`) and Swift (`Scene.swift`). Each side has tests asserting the
same answers (the same 2200 K and 6500 K whites, the same names). Change one,
change the other.

## 6. What PudiSync uses from others

### Python packages (`requirements.txt`)

| Package | Pin | Needed? | Used in | What for |
|---|---|---|---|---|
| **numpy** | ≥2.0, <3 | required | 19 modules | all frame maths, vectorised; no per-LED Python loops |
| **mss** | ≥9.0, <11 | required (fallback) | `plugins/capture_mss.py` | portable screen capture where Quartz is unavailable |
| **colour-science** | 0.4.7 | required | `enrich.py` only | four conversions: sRGB ↔ XYZ ↔ Oklab, for the Enriched look |
| **pyobjc-framework-Quartz** | ≥10, macOS only | optional, used on the Mac | `capture_quartz.py`, `engine.py`, `qr.py`, `camrun.py` | native screen capture (about 30% faster than mss), detecting a locked screen, drawing QR codes, listing cameras |
| **pyserial** | ≥3.5, <4 | optional | `output_serial.py`, `provision.py` | USB serial output (Adalight), talking to a board being set up |
| **esptool** | ≥5, <6 | optional | `provision.py` (run as a command) | flashing WLED onto a new ESP board |
| **certifi** | ≥2024.2 | optional | `provision.py` | trusted certificates for downloading WLED from GitHub |
| **opencv-python** | 4.14.0.94 | optional, camera day only | `camrun.py`, `tools/latency.py` | reading the external webcam for calibration and latency tests |
| **hidapi** | 0.15.0 | optional | `mouse.py` | sending LIGHTSYNC reports to the Logitech mouse |

Optional packages are imported inside the function that needs them, so the hub
runs without them and the feature reports itself unavailable.

### Python's standard library

The hub is otherwise built on the standard library:

| Area | Modules |
|---|---|
| server | `http.server` |
| networking | `socket`, `urllib`, `ssl` |
| concurrency | `threading` |
| data and compression | `json`, `gzip` |
| security | `hmac` (constant-time PIN compare), `hashlib` (firmware checks) |
| platform | `ctypes` (IOKit's Input Monitoring check), `subprocess` (`dns-sd`, `caffeinate`, `esptool`) |
| plumbing | `ipaddress`, `logging`, `argparse` |

### macOS services

| Service | Reached through | For |
|---|---|---|
| CoreGraphics `CGDisplayCreateImage` | pyobjc Quartz | screen capture |
| `CGSessionCopyCurrentDictionary` | pyobjc Quartz | pausing sync while the Mac is locked |
| CoreImage `CIQRCodeGenerator` | pyobjc Quartz | pairing QR codes |
| AVFoundation `AVCaptureDevice` | pyobjc | camera names, so the built-in camera is never picked |
| IOKit `IOHIDCheckAccess` / `IOHIDRequestAccess` | ctypes | Input Monitoring permission for the mouse |
| `dns-sd` | subprocess | Bonjour advertisement |
| `caffeinate` | subprocess | holding off idle sleep while syncing |
| launchd | `launchd/com.ambilight.hub.plist` | starting the hub at login and restarting it |
| SwiftUI, AppKit, Foundation, CoreGraphics | Swift | the Mac app |

### Firmware, protocols and published work

- **WLED** (open-source firmware): its JSON API, UDP realtime and DDP
  receivers, effects, presets and power limiter. Field names were checked
  against kno.wled.ge and WLED 16.0.1's source.
- **DNRGB, DDP, Adalight**: the three ways colours reach the board.
- **Improv Wi-Fi** (serial): handing a fresh board its Wi-Fi details.
- **LIGHTSYNC HID reports**: taken from OpenRGB's driver for product 0xC092
  and checked on the real mouse.
- **HyperHDR**: the grading pipeline is modelled on its float pipeline (linear
  averages, damped-spring smoothing). No code is shared.
- **Oklab** (Björn Ottosson, 2020): the perceptual space behind Enriched.
- **Tanner Helland's blackbody fit**: kelvin to RGB for the Whites wheel, in
  both clients.
- **WCAG 2.3.1**: the three-flashes-a-second limit in the anti-strobe guard.

### On the clients

None. The phone page, the board page and the Mac app use no third-party code.
The colour wheel, the QR drawing, the flow layout and the sliders are all
written for PudiSync.

## 7. What PudiSync does itself

Everything below is PudiSync's own code. Libraries supply only numbers in
arrays (numpy), pixels (Quartz, mss), and the four Oklab conversions
(colour-science).

| Piece | Module · function | What it does |
|---|---|---|
| Letterbox and frame detection | `processing.find_picture`, `find_frame` | ignores black bars so a film's colour reaches the wall |
| Screen-to-LED mapping | `processing.build_mapping`, `box_coverage` | which pixels each LED faces, for a bottom strip or a full frame, wired either way |
| Colour grading | `color.grade` and helpers | saturation with skin protection, immersion, white point by chromatic adaptation, contrast, dark lift |
| Adaptive brightness | `color.adaptive_gain` | each LED's level from what it faces against the whole frame |
| Enriched look | `enrich.tone`, `apply` | blacks stay black and highlights reach full, with lightness reshaped in Oklab |
| Smoothing | `smoothing.Smoother` | exact critically damped spring, cut detection |
| Quantizer | `smoothing.Quantizer` | flicker-free bytes, all channels together |
| LED safety | `safety.Guard` | current budget, heat settle, anti-strobe |
| Parts of the strip | `zones.composite`, `normalize` | lamp, colour and off regions over the synced frame |
| Bed strip | `bed.py` | second WLED output: layout, blend, mood and side looks, keeper |
| Sync intent | `intent.SyncIntent` | one source of truth on the board, shared by every client |
| Wire protocols | `output_wled_udp`, `output_wled_ddp`, `output_serial`, `provision.build_packet` | DNRGB, DDP, Adalight and Improv packets packed by hand |
| Setup wizard | `wizard.Wizard` | finds the LED count by binary search, marks edges, paints test gradients |
| Webcam calibration | `camcal.py`, `camrun.py` | fits a colour-match matrix from the drives that matched white, red, green and blue |
| Mouse driver | `mouse.LightsyncMouse` | takes control, sets three zones, hands back |
| Access control | `access.allowed_host`, `PinGate` | LAN-only, same-site only, PIN with lockout |
| Board host cache | `hostcache.HostCache` | survives a flaky `.local` name |
| Telemetry | `telemetry.Telemetry` | per-stage timings and percentiles |
| Recorder | `recorder.Recorder` | record and replay for repeatable tuning |
| Both user interfaces | `web/index.html`, `macapp/` | colour wheel, scene logic, optimistic holds, newest-reply-wins |

## 8. Module reference

### Hub (`ambilight/`)

| Module | Lines | Holds |
|---|---|---|
| `engine.py` | 682 | `Engine` (start, stop, override, status), `OutputStage`, `_Pipeline`, `_Pacer`, `_BlackFrameWatchdog`, `KeepAwake`, `screen_locked` |
| `routes.py` | 452 | `ROUTES` and one handler per endpoint, `Query` |
| `config.py` | 457 | `Config` (load, validate, save, looks), defaults, limits |
| `camrun.py` | 491 | camera list (never the built-in), `CameraJob`, `PatchWindow`, calibration and check runs |
| `provision.py` | 439 | Improv packets, esptool wrapper, firmware download, `Provisioner` |
| `color.py` | 331 | grading, white point, LED matrix, `to_led` |
| `processing.py` | 282 | letterbox, edges, mapping, spread, fades |
| `server.py` | 268 | `Hub`, `Handler`, gzip, status, board state cache |
| `bed.py` | 206 | bed layout, `Keeper`, `BedStage` |
| `mouse.py` | 186 | LIGHTSYNC reports, Input Monitoring, `LightsyncMouse` |
| `wled.py` | 165 | `WLEDClient` |
| `camcal.py` | 143 | calibration maths |
| `zones.py` | 140 | parts of the strip |
| `wizard.py` | 133 | setup wizard |
| `smoothing.py` | 124 | `Smoother`, `Quantizer` |
| `safety.py` | 99 | `Guard`, `estimate_ma` |
| `telemetry.py` | 99 | `Telemetry` |
| `hostcache.py` | 90 | `HostCache` |
| `board.py` | 88 | upload the board page, save the sync preset |
| `intent.py` | 81 | `SyncIntent` |
| `discovery.py` | 74 | LAN addresses, Bonjour `Advertiser` |
| `recorder.py` | 70 | `Recorder` |
| `access.py` | 68 | host and site checks, `PinGate` |
| `enrich.py` | 63 | Oklab tone curve |
| `effects.py` | 44 | the 12 curated WLED effects, `state_for` |
| `qr.py` | 42 | QR module matrix |
| `plugins/__init__.py` | 88 | `CapturePlugin`, `OutputPlugin`, the `CAPTURES` and `OUTPUTS` registries |
| `plugins/capture_quartz.py`, `capture_mss.py` | 75, 99 | screen capture |
| `plugins/output_wled_udp.py`, `output_wled_ddp.py`, `output_serial.py` | 87, 108, 107 | transports |

`hub.py` (163) parses arguments, runs one-shot commands (`--deploy-board`,
`--camera-calibrate`) and starts everything.

### Mac app (`macapp/Sources/`)

| File | Holds |
|---|---|
| `Scene.swift` | `sceneOf`, `heroView`, `bedView`, colour maths and names, `Wheel`, `cutOut` and `withPart`. Pure, tested. |
| `Hub.swift` | `HubStatus`, `HubClient`, `HubStore`: polling, holds, newest-reply-wins, every action |
| `HomePage.swift` | hero and power button, master brightness, scene, effects, lights (bed, mouse), `FlowRow` |
| `TunePage.swift` | picture, looks, fine colour, parts of the strip |
| `SettingsPage.swift` | hub connection, bed wiring, webcam calibration, links |
| `ColourWheel.swift` | the wheel: any colour or whites, recent picks, keyboard |
| `LEDFrame.swift`, `Layout.swift` | drawing the strip, LED positions |
| `Theme.swift` | palette (light and dark), `Card`, `Segmented`, `Chip`, `Fader`, `SwitchRow` |
| `App.swift` | window, sidebar, menu-bar panel, Lights menu |
| `Connection.swift`, `PairSheet.swift` | link states and banners, pairing QR |

## 9. Tests and tools

| What | How to run | Covers |
|---|---|---|
| Acceptance suite `tests/test_hub.py` (849 checks) | `AMBILIGHT_TEST_PORT_BASE=<base> make test` | every module, the HTTP API against a sandbox hub and fake board, the page's logic under node, and Chrome driving the page (Playwright) |
| Mac app logic `macapp/Tests/main.swift` (53 checks) | `macapp/build.sh test` | layout, colours, link states, QR, scene, wheel, parts |
| `tools/fake_wled.py` | `make sim` | a WLED board with no hardware: JSON API, UDP and DDP listeners |
| `tools/hardware_sim.py` | `make sim` | failure scenarios: reboot, flaky Wi-Fi, short strip, power, reversed wiring |
| `tools/latency.py` | camera day only | glass-to-LED latency with the Lenovo webcam |
| `tools/soak.py` | `--hours 4` | memory and frame rate over hours |

## 10. Observations

- **colour-science is heavy for what it does.** It is a large scientific
  package, used for four conversions in one 63-line module. The two 3×3
  matrices and a cube root behind Oklab are published and short; writing them
  in numpy would drop the dependency and speed up install. Worth doing with a
  test that compares against colour-science's output first.
- **Two capture paths are deliberate.** Quartz is faster on the Mac. mss
  keeps the hub portable and is the fallback if pyobjc is missing.
  `CGDisplayCreateImage` is marked obsolete in the macOS 15 SDK;
  ScreenCaptureKit is the planned replacement (`docs/ROADMAP.md`, capture).
- **Logic written twice.** `Scene.swift` mirrors the page's JavaScript, and
  `Layout.swift` mirrors the hub's LED order. Tests on both sides pin the
  shared answers, but a change to one must be made to the other.
- **No client depends on outside code or a network.** The page and the app
  keep working on a LAN with no internet. The only outbound internet call is
  downloading WLED when flashing a new board.
- **The board stays stock.** Every protocol used is one WLED documents, so a
  WLED update or a replacement board needs no PudiSync change.
