# Ambilight Hub

Screen-synced LED lighting for a WLED strip, driven from your phone's browser.
Runs as one Python process on a Mac. No app, no cloud, no account — your home
network only.

![tests](https://img.shields.io/badge/tests-360%20passing-brightgreen)
![license](https://img.shields.io/badge/license-MIT-blue)

- A phone page stored **on the WLED board**: monitor sync, effects and a colour
  picker, working even when the Mac is off
- Follows the screen with a strip **wrapped round the monitor** (or along the bottom
  only), and hugs the picture when a video has black bars
- A colour engine modelled on HyperHDR and Philips Ambilight: averaging in linear
  light, conversion into the WS2812B's own primaries, a warm white point, contrast,
  and spring-smoothed motion that settles faster on real scene cuts
- An **LED safety layer**: power budget, heat limit, anti-strobe, and a fade-out
  when capture freezes or the Mac locks
- Reachable from every device on your home network by name (`yourmac.local`),
  with protection against other websites driving it
- Every control is also a plain URL, so iOS Shortcuts can drive it
- Talks to WLED over **Wi-Fi (UDP)** or a **USB cable**; flashes and provisions a
  brand-new ESP board over USB

---

## 1. What you need

| | |
|---|---|
| Computer | A Mac (tested on Apple Silicon, macOS 26) |
| Controller | ESP8266 / ESP32 running [WLED](https://kno.wled.ge) |
| Strip | WS2812B or similar addressable strip |
| Python | 3.10 or newer (tested on 3.12) |
| Level shifter | **Recommended** — see below |
| Power supply | 5V, sized for your strip — see below |

### Do I need a level shifter?

**Strictly, yes.** The ESP8266 and ESP32 drive their GPIO pins at **3.3V**, but
the WS2812B datasheet wants at least **0.7 x VDD** on the data line — that is
**3.5V** when the strip runs at 5V. 3.3V is below spec.

It often *appears* to work anyway, which is why so many guides skip it. What you
get instead is intermittent trouble: the first LED flickering, random colour
glitches, or a strip that behaves until it warms up or the data wire gets longer.

[WLED recommends](https://kno.wled.ge/basics/compatible-hardware/) the
**SN74AHCT125** (the common choice) or **SN74HCT245**. The SN74HCT125N is a
cheaper, slower alternative that is fine for WS2812B.

Avoid: I2C shifters (too slow for addressable LEDs) and bidirectional types like
the TXS0102/TXS0108, which only work on data lines under ~50cm.

You can skip the shifter if you are only testing, or if your board already has
one built in — many commercial WLED controllers (QuinLED, Athom) include it.

### Power

A WS2812B draws roughly **60mA at full white**, so a 240-LED strip is:

| Scenario | Current at 5V | Power |
|---|---|---|
| Full white, every LED | ~14.4 A | ~72 W |
| Typical ambilight use | ~5 A | ~25 W |

Notes:

- **Do not power a strip of this size from the ESP's USB port.** Use a dedicated
  5V supply rated above your worst case, and join the grounds of the supply, the
  strip and the controller
- Over a 4m run the far end will look dim and tinted red as voltage drops along
  the copper. Fix it with **power injection**: run 5V and ground to the far end
  (and ideally the middle) as well as the start
- The hub keeps every frame inside a **power budget** (`power_limit_ma`, default
  3.8A for a 5A supply) and lets a bright scene held for minutes settle to
  `sustained_ma` (default 3A). Set both for your supply. Keep WLED's own current
  limiter (**Config ▸ LED Preferences**) set to the supply rating as a backstop

## 2. Install

```bash
git clone https://github.com/pudiish/pudisync.git
cd pudisync
python3 -m pip install -r requirements.txt
python3 ambilight.py
```

On start it prints the addresses to open from any phone, tablet or computer on
the same network. The `.local` name keeps working when the Mac's IP changes, and
the hub also advertises itself over Bonjour as **Ambilight Hub**:

```
  Open the control page from any device on this network:
    http://yourmac.local:8080
    http://192.168.1.24:8080
```

`--config other.json` and `--port 8790` run a second, independent hub (tests use
this); `--no-bonjour` skips the advertisement.

### Grant Screen Recording permission

macOS blocks screen capture until you allow it. If the lights stay dark and the
page reports it:

1. **System Settings ▸ Privacy & Security ▸ Screen Recording**
2. Switch on the app running the script (Terminal, iTerm, VS Code — or Python,
   when it starts at login)
3. Quit that app **completely** and start it again — a reload is not enough

### Firewall

The first run may raise "Do you want the application python3 to accept incoming
network connections?" — choose **Allow**, or your phone cannot reach the page.

## 3. One-click controller setup

If your board is brand new, the **Setup** tab can do the whole thing over the USB
cable — install WLED and join it to your Wi-Fi, with no hotspot step.

```bash
python3 -m pip install esptool pyserial
```

1. Plug the controller into the Mac over USB
2. Open **Setup ▸ Set up a new controller**
3. Pick the board and your Wi-Fi network from the dropdowns, enter the password,
   and press the button

It detects the chip, downloads the matching official WLED build, flashes it, then
sends your credentials using **Improv Serial** — the protocol WLED supports for
exactly this. When the board reports its address, the hub adopts it automatically.

**Requirements and limits**

- The network must be **2.4GHz**. ESP8266 cannot join 5GHz at all — the network
  dropdown lists what this Mac remembers and marks the ones that look like 5GHz
- Some boards need the **BOOT/FLASH** button held while plugging in to enter
  flash mode. The error message tells you if that's the case
- You may need a USB driver for the board's bridge chip (CP2102 or CH340)
- **"Install WLED firmware" is off by default, and for good reason.** Ticking it
  runs a full chip erase: the firmware, saved Wi-Fi, LED count, segments and
  presets are all destroyed and cannot be recovered. The UI asks you to confirm
- With it unticked, nothing is erased — the board keeps its firmware and settings
  and only receives the new Wi-Fi credentials. This is what you want for a board
  that already runs WLED

Prefer doing it by hand? [install.wled.me](https://install.wled.me) is the
official web installer and works just as well.

## 4. Connect the controller

### Option A — Wi-Fi (recommended)

1. Flash WLED and join it to your home Wi-Fi
2. Use the controller's Bonjour name as its address, e.g. `wled-6b0bc6.local`
   (WLED shows it under **Config ▸ WiFi Setup ▸ mDNS address**). The hub resolves
   it and follows the controller if your router hands it a new IP. A DHCP
   reservation in the router works too
3. Put that name or address into the controller field on the Setup tab

### Option B — USB cable

Stock WLED accepts the Adalight protocol on serial with **no firmware change**
([docs](https://kno.wled.ge/interfaces/serial/)). One-time setup:

1. Plug the controller into the Mac over USB
2. Install the USB driver for its chip if the port does not appear
   (CP2102 or CH340, depending on the board)
3. In WLED: **Config ▸ Sync Interfaces ▸ Serial baud rate** — raise it
4. In this page: **Settings ▸ Connection ▸ USB cable**, pick the device, and set
   the same baud rate

**Honest limit.** WLED's serial default of 115200 baud is documented as enough
for "about 50-100 LEDs". At 240 LEDs that is only ~16 fps. The page computes
and shows the baud rate your strip actually needs:

| LEDs | 30 fps | 60 fps |
|---|---|---|
| 100 | 115200 | 230400 |
| 240 | 460800 | 921600 |

Not every USB bridge chip reaches 921600. If yours does not, use Wi-Fi.
If the port disappears mid-session, the engine **falls back to Wi-Fi
automatically** and says so rather than going dark.

## 5. Set up the strip and calibrate colours

Open the page on your phone and go to **Setup**:

1. **Strip length** — the page lights part of the strip and narrows down the
   real length from your yes/no answers (about 8 taps for 240 lights)
2. **Layout** — *Around the monitor*: enter how many LEDs sit on each side. The
   strip starts at the bottom-left corner and runs bottom → right → top → left.
   If the strip's rectangle differs from the screen (stuck inside the bezel, or
   just outside it), enter its measured width and height in mm so each side
   samples the stretch of screen it really sits beside.
   *Bottom only*: mark the LEDs behind the screen's left and right corners
3. **Test pattern** — around the monitor, each side lights its own colour
   (bottom red, right green, top blue, left white) and brightens along the
   strip's direction, with the start LED amber. Bottom only: red at the left
   corner easing to blue at the right
4. **Calibrate colours** — open `http://yourmac.local:8080/calibrate` **on the
   synced monitor itself**, full screen. It fills the screen with test patches
   (white, greys, primaries, skin) so you can compare the wall with the screen and
   set warmth, contrast, colour intensity and the strip's red/green/blue balance

Every WS2812B batch differs a little; the strip balance is where you correct a
white that looks green or blue.

### Calibrate with a webcam

Instead of judging by eye, a webcam can measure the match. Set it up so it sees
the monitor **and** the wall glow around it, lock its position, and, with the
hub running:

```bash
python3 -m pip install opencv-python
python3 ambilight.py --camera-calibrate    # --camera 1 for a second webcam
```

The Mac asks for Camera permission the first time (allow it for the app that
runs the command, e.g. Terminal, then restart that app). For about two minutes
the monitor shows test colours while the strip shows other colours chosen
independently. Each picture shows the screen and the wall together, so the
camera's own auto-exposure and white balance cancel out. From these pictures
the hub fits how this strip, on this wall, reproduces each screen colour. It
then scores the match with sync on, before and after, and keeps the new
colours only if the wall got closer to the screen. Warmth, contrast and colour
intensity still apply on top. `recordings/calibration-view.jpg` shows what was
measured: the screen is tinted blue and the wall orange.

Later, with a video playing, `python3 ambilight.py --camera-check 60` scores
the match for a minute without changing anything (0 is a perfect match).

## 6. Zones

The strip is usually longer than the monitor. Zones give each stretch its own job:

| Mode | What it does |
|---|---|
| **Screen** | Mirrors the screen above it |
| **Lamp** | Steady warm white, ignores the screen |
| **Colour** | Any fixed colour you pick |
| **Off** | Dark |

Drag a zone's handles on the strip to resize it, or drag the dashed **screen
region** itself. With no zones at all, the whole strip follows the screen.

## 7. The phone page lives on the board

The page you use day to day is stored **on the WLED board itself**, so it loads
whenever the strip has power, with the Mac switched off. Open the board's address
on your phone (`http://wled-6b0bc6.local`, or its IP):

- **Monitor**: one button for screen sync
- **Effects**: twelve one-tap effects, with speed and intensity
- **Colour**: a colour wheel and quick swatches
- a power switch and a brightness bar

On an iPhone: Safari ▸ **Share** ▸ **Add to Home Screen**.

**How monitor sync works without the page knowing the Mac.** Monitor sync is a WLED
preset ("Monitor sync", id `sync_preset`). The hub on the Mac watches the board:
while that preset is active it streams your screen, and the moment you pick an
effect or colour on the page it stops. If the Mac is off, the page says "Waiting
for your Mac" and the strip glows a soft warm white until the hub is running.

Install or update the page (turn screen sync off first; it writes to the board's
flash):

```bash
python3 ambilight.py --deploy-board
```

The source is `board/index.htm`; it is uploaded compressed (~6 KB). WLED's own
interface stays at `/settings`. To go back to WLED's page entirely, delete
`index.htm.gz` in WLED's file editor at `/edit`.

The hub's own page at `http://yourmac.local:8080` is now the **setup and tuning**
page: flashing, strip layout and test pattern, zones, and colour calibration.

## 8. iOS Shortcuts

Every action is a plain URL. In Shortcuts, add **Get Contents of URL**:

```
http://yourmac.local:8080/api/sync/on
http://yourmac.local:8080/api/sync/off
http://yourmac.local:8080/api/profile/movie
http://yourmac.local:8080/api/profile/game
http://yourmac.local:8080/api/set?brightness=0.5
```

With a PIN set, send it as the `X-Ambilight-Pin` header (or append `&pin=1234`).

## 9. Start automatically at login

Keep the repo **outside** Downloads, Documents and Desktop first: macOS blocks
background agents from reading those folders.

```bash
cp launchd/com.ambilight.hub.plist ~/Library/LaunchAgents/
# edit every path inside first, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ambilight.hub.plist
```

To stop it starting:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.ambilight.hub.plist
```

Logs (timestamped) land in `~/Library/Logs/ambilight.log`.

**Note.** A launchd agent runs as a background process, which macOS treats as a
separate app for Screen Recording: grant permission to Python as well. While sync
runs, the hub holds off idle system sleep so phones can still reach it.

## 10. Testing without hardware

```bash
python3 tests.py           # ~360 checks; starts its own fake WLED and sandbox hub
```

The test run starts a fake WLED and a sandbox hub on ports 8790-8791 with a
throwaway config, so it never touches the hub driving your lights. Its HTTP part
captures the real screen, so run it with the display awake and unlocked.

`hardware_sim.py` simulates a real strip and puts a hub through the failures you
would otherwise only find on your desk. It rewrites settings, so point it at a
sandbox hub (it refuses port 8080):

```bash
python3 fake_wled.py &
python3 ambilight.py --config /tmp/sim-config.json --port 8790 --no-bonjour &
python3 hardware_sim.py --hub http://127.0.0.1:8790
```

| Scenario | What it proves |
|---|---|
| `steady` | The stream is continuous and well formed |
| `reboot` | A controller that loses power recovers with no restart |
| `flaky` | 30% packet loss does not stall the engine |
| `short` | A strip shorter than configured degrades harmlessly |
| `power` | Brightness really is a power budget; brownout is detected |
| `release` | Stopping hands the strip back to WLED |
| `reversed` | A right-to-left strip still maps correctly |

See [docs/wiring.md](docs/wiring.md) for diagrams and a pre-power checklist.

## 11. Performance

Measured on an Apple Silicon Mac with a 2560x1440 monitor, strip round all four
sides:

```
full-screen capture (mss)     ~23 ms
sampling + colour grading      ~2.5 ms
capture rate                  ~36-41 fps
output rate                    60 fps (separate thread, smoothing in between)
```

Screen capture is nearly the whole cost. The output thread runs at a steady rate
and the spring smoothing fills in between captures, so motion stays fluid.

## Security

This is built for a home LAN. It has no encryption and no user accounts.

- Requests that a browser marks as coming from **another website** are refused,
  and so are host names other than IPs, `localhost` and `.local` names (DNS
  rebinding). Shortcuts and curl are unaffected
- The optional PIN is checked in constant time, and a device that guesses wrong
  five times is locked out for a minute. The PIN is never returned by the API
- Flashing only targets a board that is actually plugged in, and firmware is
  checked against the release's size and SHA-256 before anything is erased

**Do not port-forward it or expose it to the internet.**

## Licence

MIT.
