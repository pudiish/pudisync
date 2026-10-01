# Ambilight Hub

Screen-synced LED lighting for a WLED strip, driven from your phone's browser.
Runs as one Python process on a Mac. No app, no cloud, no account — your home
network only.

![milestone](https://img.shields.io/badge/tests-78%20passing-brightgreen)
![license](https://img.shields.io/badge/license-MIT-blue)

- Captures a band across the bottom of your screen and streams it to the strip
- The strip can be **longer than the monitor** — zones give the overhang its own job
- Drag the screen region and each zone directly on an on-screen strip
- Every control is also a plain URL, so iOS Shortcuts can drive it
- Talks to WLED over **Wi-Fi (UDP)** or a **USB cable**

---

## 1. What you need

| | |
|---|---|
| Computer | A Mac (tested on Apple Silicon, macOS 26) |
| Controller | ESP8266 / ESP32 running [WLED](https://kno.wled.ge) |
| Strip | WS2812B or similar addressable strip |
| Python | 3.9 or newer |
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
- The **Brightness** slider is also your power budget. Capping it near 50% roughly
  halves the current draw. WLED has its own current limiter under
  **Config ▸ LED Preferences**; set the milliamp figure to match your supply

## 2. Install

```bash
git clone https://github.com/pudiish/pudisync.git
cd pudisync
python3 -m pip install -r requirements.txt
python3 ambilight.py
```

On start it prints the address to open on your phone:

```
  Phone control page:  http://192.168.1.24:8080
  WLED target:         192.168.1.50:21324
```

### Grant Screen Recording permission

macOS blocks screen capture until you allow it. If the lights stay dark and the
page reports it:

1. **System Settings ▸ Privacy & Security ▸ Screen Recording**
2. Switch on the app running the script (Terminal, iTerm, or VS Code)
3. Quit that app **completely** and start it again — a reload is not enough

### Firewall

The first run may raise "Do you want the application python3 to accept incoming
network connections?" — choose **Allow**, or your phone cannot reach the page.

## 3. Connect the controller

### Option A — Wi-Fi (recommended)

1. Flash WLED and join it to your home Wi-Fi
2. Give it a **fixed address** in your router (DHCP reservation), so it does not
   move after a reboot. Look for "DHCP reservation" or "static lease" in your
   router's admin page and bind the controller's MAC address
3. Put that address into **Settings ▸ Controller** in the web page

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

## 4. First-time setup

Open the page on your phone and go to **Setup**:

1. **Strip length** — the page lights part of the strip and narrows down the
   real length from your yes/no answers (about 8 taps for 240 lights)
2. **Screen corners** — an orange light walks the strip; put it behind each
   corner of your monitor and assign it
3. **Spill & check** — the test pattern should show **red at the left corner**
   easing to **blue at the right**. Swapped? Redo step 2

A strip wired right-to-left needs no special setting: assign the corners and the
engine works out the direction.

## 5. Zones

The strip is usually longer than the monitor. Zones give each stretch its own job:

| Mode | What it does |
|---|---|
| **Screen** | Mirrors the screen above it |
| **Lamp** | Steady warm white, ignores the screen |
| **Colour** | Any fixed colour you pick |
| **Off** | Dark |

Drag a zone's handles on the strip to resize it, or drag the dashed **screen
region** itself. With no zones at all, the whole strip follows the screen.

## 6. Add to your iPhone home screen

Safari ▸ **Share** ▸ **Add to Home Screen**. It then opens full-screen like an app.

## 7. iOS Shortcuts

Every action is a plain URL. In Shortcuts, add **Get Contents of URL**:

```
http://<mac-ip>:8080/api/sync/on
http://<mac-ip>:8080/api/sync/off
http://<mac-ip>:8080/api/profile/movie
http://<mac-ip>:8080/api/profile/game
http://<mac-ip>:8080/api/set?brightness=0.5
```

With a PIN set, append `&pin=1234` (or send the `X-Ambilight-Pin` header).

## 8. Start automatically at login

```bash
cp launchd/com.ambilight.hub.plist ~/Library/LaunchAgents/
# edit the paths inside first, then:
launchctl load ~/Library/LaunchAgents/com.ambilight.hub.plist
```

To stop it starting:

```bash
launchctl unload ~/Library/LaunchAgents/com.ambilight.hub.plist
```

Logs land in `~/Library/Logs/ambilight.log`.

**Note.** A launchd agent runs as a background process, which macOS treats as a
separate app for Screen Recording. Grant permission to the agent as well, or run
it from Terminal instead.

## 9. Testing without hardware

```bash
python3 fake_wled.py       # pretends to be a WLED controller
python3 ambilight.py       # point its config at 127.0.0.1
python3 tests.py           # 78 acceptance tests
```

`fake_wled.py` prints every packet it receives and flags protocol violations.

## 10. Performance

Measured on an Apple Silicon Mac, 240 LEDs:

```
capture only (mss.grab)       15.70 ms   ->  63.7 fps ceiling
processing only                0.05 ms   -> 20842 fps
FULL frame path               15.25 ms   ->  65.6 fps ceiling
```

Live: **58-66 fps sustained**. Screen capture is the whole cost — the colour
pipeline is ~300x faster than the capture feeding it. Setting the frame rate
above ~65 only burns CPU. See [NOTES-positioning.md](NOTES-positioning.md).

## Security

This is built for a home LAN. It has no encryption and no user accounts; the
optional PIN only deters casual access by others already on your Wi-Fi.
**Do not port-forward it or expose it to the internet.**

## Licence

MIT.
