# Architecture map

What this project is: a Mac program (the **hub**) that looks at your screen,
works out what colour each LED should be, and sends those colours over Wi-Fi to
a NodeMCU board running **WLED**, which drives the LED strip behind the monitor.
It also drives a second strip round a bed, a Logitech mouse's lights, and has a
phone web page and a native Mac app for controlling it all.

Line counts below are from the repo at the time of writing.

## 1. The one-line data flow

```
screen ──capture──▶ picture finder ──▶ edge colours ──▶ colour grading ──▶ smoothing ──▶ safety limits ──▶ UDP ──▶ WLED board ──▶ LEDs
 (plugins/capture_*)  (processing.py)   (processing.py)   (color.py)       (smoothing.py)   (safety.py)    (plugins/output_*)

phone page / Mac app ──HTTP GET──▶ server.py ──▶ routes.py ──▶ config.py (settings) + engine.py (start/stop/override)
```

`engine.py` is the conductor: it runs the capture thread and the output thread
and calls everything above in order. Everything else is either a step in that
pipeline, a way of controlling it, or a one-off setup tool.

## 2. Files by purpose

### A. Entry point
| File | Lines | What it is for |
|---|---|---|
| `hub.py` | 163 | Starts everything. Parses command-line flags (`--port`, `--camera-calibrate`, `--deploy-board`), builds the `Hub`, runs the web server. |

### B. The sync pipeline (the core, read these first)
| File | Lines | What it is for |
|---|---|---|
| `ambilight/engine.py` | 658 | **The conductor.** Capture thread + output thread, frame pacing, restart on failure, recording, override. The biggest file. |
| `ambilight/processing.py` | 282 | Frame maths: find the picture inside the screen (skip black bars), cut edge strips, map them to LEDs. |
| `ambilight/color.py` | 273 | Colour grading: sRGB to linear, white balance, saturation, skin tones, "immersion", final LED colour. Pure maths. |
| `ambilight/smoothing.py` | 124 | How the light moves between frames (no flicker, response speed, dithering). |
| `ambilight/zones.py` | 140 | Splits the strip into regions, each with its own mode (screen, colour, effect). |
| `ambilight/safety.py` | 99 | Brightness and current limits so the strip and 5V supply are not overdriven. |
| `ambilight/telemetry.py` | 99 | Live timing per stage (capture, process, colour, smooth, send), delivered fps, drops and picture age, for the status API. |

### C. Hardware plugins (swappable in and out)
| File | Lines | What it is for |
|---|---|---|
| `ambilight/plugins/__init__.py` | 87 | Plugin interfaces and the registry that picks one. |
| `plugins/capture_quartz.py` | 75 | Screen capture via macOS CoreGraphics (the fast, native one). |
| `plugins/capture_mss.py` | 99 | Baseline screen capture (portable, not Mac-specific). |
| `plugins/output_wled_udp.py` | 87 | Sends colours to WLED over UDP (the normal output). |
| `plugins/output_wled_ddp.py` | 108 | Sends colours to WLED over DDP: each frame drawn once, whole. |
| `plugins/output_serial.py` | 107 | Sends colours over a USB cable (Adalight protocol). |

### D. Extra lights
| File | Lines | What it is for |
|---|---|---|
| `ambilight/bed.py` | 207 | Second strip round the bed, on the board's second output. |
| `ambilight/mouse.py` | 186 | Logitech G102/G203 mouse lights following the screen. |
| `ambilight/effects.py` | 44 | Short list of built-in WLED effects offered in the UI. |

### E. Talking to the board
| File | Lines | What it is for |
|---|---|---|
| `ambilight/wled.py` | 164 | Client for WLED's JSON API (power, presets, segments). |
| `ambilight/board.py` | 88 | Puts the control page onto the board itself. |
| `ambilight/hostcache.py` | 90 | Remembers the board's IP because `.local` names are flaky. |
| `ambilight/intent.py` | 74 | Rule: the board decides whether sync is on; the Mac follows. |
| `board/index.htm` | 434 | The page that lives on the board. |

### F. Control surface: how you drive it
| File | Lines | What it is for |
|---|---|---|
| `ambilight/server.py` | 231 | HTTP server and the `Hub` object that owns the engine and config. |
| `ambilight/routes.py` | 425 | One small function per URL (the API). |
| `ambilight/access.py` | 68 | Only home-LAN devices may call it. |
| `ambilight/config.py` | 427 | All settings, their defaults and valid ranges, saved to `config.json`. |
| `ambilight/discovery.py` | 74 | Bonjour name, the hub's addresses, and where the board's own page is. |
| `ambilight/qr.py` | 42 | QR codes for pairing a phone, drawn by macOS's CoreImage. |
| `ambilight/web/index.html` | 1978 | The phone web page (6 tabs: Home, Effects, Override, Bed, Setup, Advanced). HTML, CSS and JS in one file. |
| `ambilight/web/calibrate.html` | 231 | Calibration page. |
| `macapp/` | about 1,550 Swift | Native Mac app. A client of the same HTTP API. `Connection.swift` holds its link states, `PairSheet.swift` the pairing QR. |

How each device connects, what happens when something drops out, and the
manual steps: `docs/CONNECT.md`.

### G. Setup and calibration (run rarely)
| File | Lines | What it is for |
|---|---|---|
| `ambilight/provision.py` | 439 | Flash WLED onto the ESP board over USB and give it Wi-Fi. |
| `ambilight/wizard.py` | 133 | LED-count search, edge marker, test gradient. |
| `ambilight/camcal.py` | 143 | Maths to match wall colour to screen colour using a webcam. |
| `ambilight/camrun.py` | 491 | Runs that calibration on real hardware (spawned by the hub). |
| `ambilight/recorder.py` | 70 | Record what the screen fed the lights and replay it, for tuning. |

### H. Dev tooling
| File | Lines | What it is for |
|---|---|---|
| `tests/test_hub.py` | 3603 | 54 test functions, 720 checks. Starts a fake board and a sandbox hub. |
| `tools/fake_wled.py` | 227 | Pretend WLED board for tests; checks DNRGB and DDP packets. |
| `tools/hardware_sim.py` | 356 | Puts a hub through failures (reboot, drops) against the fake board. |
| `tools/soak.py` | 256 | Runs a sandbox hub for hours; checks memory stays flat and fps steady. `--dry-run` for one minute. |
| `tools/latency.py` | 361 | Camera day: glass-to-LED latency through the webcam. `--dry-run` uses a simulated room. |
| `Makefile`, `launchd/`, `requirements.txt` | | Commands, start-at-login, dependencies. |

## 3. Plan to understand it (about 2 to 3 hours, in this order)

Do not read top to bottom. Follow one frame of video through the system.

1. **Run it before reading (15 min).** `make test`, then `make sim`. Watch what
   the fake board receives. You learn the shape of the system from outside.
2. **Read the contract, not the code (15 min).** `README.md` and
   `ambilight/plugins/__init__.py`. The latter is short and shows what "capture"
   and "output" mean.
3. **Follow one frame (60 min).** Open `engine.py` and read only `_loop`
   (line 447) and `_output` (line 533). Every time it calls out, jump to that
   function: `processing.edge_colors`, then `color.to_led`, then
   `smoothing`, then `safety`. Skip the helper classes the first time.
4. **Follow one button press (30 min).** In `web/index.html` find the Lights tab's
   power button, find the URL it calls, find that URL in `routes.py`, see what
   it changes in `config.py`. That is the whole control path.
5. **Then the add-ons (30 min).** `bed.py`, `mouse.py` and `zones.py` are all
   "extra outputs fed from the same frame". Once you know the main path they
   are small.
6. **Leave for last:** `provision.py`, `camrun.py`, `camcal.py`, `wizard.py`,
   `macapp/`. They are one-time setup or a second client, not the core.

Tip for any function you do not understand: find its test in
`tests/test_hub.py` (search for the function name). The tests show real inputs
and expected outputs, which is often clearer than the code.

## 4. Where it is hard to follow (candidates to clean up later)

These are observations from sizes and structure, not confirmed bugs.

- **`engine.py` (658 lines)** mixes capture, pacing, recording, mouse, bed and
  safety in one class. It is the place most likely to confuse you.
- **`web/index.html` (1836 lines)** has page, styles and logic in one file.
  Splitting into `.css` and `.js` files would make each tab findable.
- **`config.py` (427 lines)** holds every setting for every feature (`bed_*`,
  mouse, zones, colour). One flat namespace, easy to lose things in.
- **`tests/test_hub.py` (2842 lines)** is one file for everything. Splitting by
  area (colour, engine, routes, bed) would let you run just the part you are
  reading.
- **`camrun.py` (491 lines) and `provision.py` (439 lines)** are large for
  tools used a handful of times. Low priority.
- **LED order is written down twice.** `macapp/Sources/Layout.swift` redraws the
  strip in the order `processing.frame_colors` uses (bottom, right, top, left).
  If that order ever changes in Python, the Mac preview must be changed by hand.
