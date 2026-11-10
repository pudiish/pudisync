# Connecting phones, tablets and the Mac

How each device finds and talks to the lights, what happens when something
drops out, and the few steps only you can do.

## The two pages and the app

| What | Where | Works when |
|---|---|---|
| Hub page (full controls) | `http://<your-mac>.local:8080` | The Mac is awake and the hub is running |
| Strip's own page (sync, effects, colour) | The board's address, e.g. `http://wled-6b0bc6.local` | The strip has power. The Mac can be off |
| Mac app | `/Applications/Ambilight Hub.app`, talks to `127.0.0.1:8080` | The hub is running on this Mac |

## Pair a phone or tablet

- **On the Mac:** open Lighting ▸ Pair a Phone or iPad… (or the button in
  Settings).
- **On a device that is already paired:** open Advanced ▸ Access ▸ Add a phone
  or tablet.

Point the new device's camera at the code and open the link. The picker offers:

- the hub by its `.local` name (the default: it survives the Mac's address
  changing);
- the hub by IP address (for routers that block `.local` names);
- the strip's own page.

The codes hold addresses only. If the hub has a PIN, the new device asks for it
once.

## Scenario matrix

"Before" is the behaviour on `feature/color-engine` before this work.

| Situation | Before | Now | How it is checked |
|---|---|---|---|
| First pairing on an iPhone | Type `http://<mac>.local:8080` by hand. iOS can't browse the Bonjour advert, and the startup URLs go to a log file | Scan a QR code shown by the Mac app or any paired device | Test 43 serves `/api/pair` and decodes it with CoreImage's QR detector. Test 40 decodes what the page draws |
| An iPad as a second client | Works: threaded server, shared 2 s cache of the board's state, a PIN per device | Unchanged. Pair it from the iPhone's code | Test 43: a change from one device shows on another's next poll |
| Mac asleep or lid closed | The request hung and the page showed stale "SYNCING" for a minute or more | "Can't reach the hub · last seen 12 s ago" within 4 s, with a link to the strip's own page. Polls again on wake | Test 40: offline in about 4 s against a port that never answers. **Manual:** see below |
| Display asleep, Mac awake | The banner said "Pick another display in Advanced" | "Your Mac's screen is asleep… Sync resumes on its own when it wakes." | Test 40 (`friendly`) |
| Hub stopped | Connection refused, then "Hub offline". The Mac app said "Can't reach the hub" | Same speed, a clearer banner, and the link to the strip's own page | Test 40; measured below. **Manual:** start the hub |
| Board offline or rebooting | No banner while sync was off. While syncing, "SYNCING" (NO LINK could never show) | "The LED board isn't answering…" while sync is off. While syncing, NO LINK plus "Can't reach the LED controller" | Tests 40 and 41. **Manual:** boot preset |
| Wi-Fi drop and recovery | Retried every 5 s, but could hang when iOS sent the request over mobile data | 4 s timeout, and an immediate retry when Wi-Fi returns. The strip's own page does the same | Tests 25 and 40. **Manual:** DHCP reservations |
| The hub's or board's IP changes | A `.local` URL survives. A bookmark or Home Screen app made from an IP breaks | The code offers the `.local` name first. A page opened by IP says to use the name. The board link uses the address it last answered on | Tests 40, 42 and 43 |
| PIN set | The phone asks once. The Mac app said "Can't reach the hub". A lockout (429) left the page silently stale | The Mac app says "Enter it in Settings". A lockout says "Wait a minute" | Test 40 and `macapp/build.sh test` |
| PIN forgotten | No way back in the UI | A device that still has it can remove it under Advanced ▸ Access | **Manual:** see below |
| Phone away from home | Hung, then "Hub offline" | "The hub only answers on your home Wi-Fi", within 4 s | Test 40 |
| Added to the Home Screen | The icon was a screenshot (iOS ignores SVG icons) | A 180 px PNG icon | Test 43. **Manual:** add the strip's page too |

## Manual steps

- **Mac asleep or lid closed.** Nothing on the network can reach a sleeping
  Mac. While sync runs, the hub holds off idle sleep, but closing the lid
  sleeps a laptop. Exception: with power and an external display attached, the
  Mac stays awake in clamshell mode. An Apple TV or HomePod acting as a Bonjour
  sleep proxy may wake the Mac when a phone connects. That needs System
  Settings ▸ Battery ▸ Options ▸ Wake for network access, and has not been
  tested here. The strip's own page always works.
- **Hub stopped.** `launchctl kickstart -k gui/$(id -u)/com.ambilight.hub`, or
  `make run` in the repo.
- **Board reboots or loses power.** WLED comes back without the Monitor sync
  preset, so the hub stops syncing (the board decides). To make sync resume
  after a power cut, open WLED ▸ Config ▸ LED Preferences and apply preset 1 at
  boot. Note that the strip then glows a dim warm white at every power-up.
- **Update the strip's own page** after pulling this change: `python3 hub.py
  --deploy-board`. Turn sync off first; it writes to the board's flash.
- **Addresses that change.** In the router, give the Mac and the board DHCP
  reservations. Or set the board by its mDNS name (WLED ▸ Config ▸ WiFi Setup)
  in the hub's Setup tab.
- **PIN forgotten and no device remembers it.** Stop the hub, set `"pin": ""` in
  `config.json` next to `hub.py`, then start the hub.
- **Home Screen.** Add both pages: the hub page for full control, and the
  strip's own page as the icon that works with the Mac off. Each Home Screen app
  keeps its own storage, so it asks for the PIN once more. Opened while the hub
  is unreachable, the hub icon shows Safari's error page. An offline copy would
  need a service worker, which iOS allows only over HTTPS.
- **The strip's own page has no PIN.** WLED's API is open to anyone on the
  Wi-Fi, whatever the hub's PIN.

## Numbers

Measured with node running the phone page's own request code against the test
sandbox (port base 8820):

| | Before | After |
|---|---|---|
| Stopped hub (connection refused) shown offline | 52 ms | 69 ms |
| Hung hub (accepts, never answers) shown offline | still waiting after 30 s | 4005 ms |
| Board that never answers, strip's own page | no timeout | 4008 ms |
| Back online after the hub returns | up to 5 s (next retry) | up to 5 s, or at once when Wi-Fi itself returns |

- **QR code generation:** about 1 ms for each new address once CoreImage is
  warm, 286 ms for the first one in a process, then cached. Pyobjc's Quartz is
  already loaded for capture.
- **Strip's own page:** 20,119 → 20,678 bytes, and 6,863 → 7,071 bytes gzipped
  as uploaded.

## Remote access (written option only; nothing is built)

The hub speaks plain HTTP, every action is a GET, and the PIN is the only lock.
It is built for the home LAN.

| Option | What you get | Security trade-offs | Verdict |
|---|---|---|---|
| **Tailscale** on the Mac, iPhone and iPad | The full hub page from anywhere, at the Mac's 100.x address | WireGuard encryption, and only your logged-in devices can connect. Nothing is opened on the router. Tailscale's coordination servers learn your device list. The 100.x address already passes the Host check; a MagicDNS name (`*.ts.net`) would need adding to `allowed_host`. Keep the PIN on. A sleeping Mac is still unreachable | **Recommended** if remote control is wanted |
| **HomeKit bridge** (e.g. Homebridge, or a small HAP server in the hub) | On/off, brightness and profiles in the Home app and Siri, remotely through an Apple TV or HomePod | End-to-end encrypted by Apple, no port opened. Needs a new dependency and pairing. Exposes only a few controls | Good for on/off away from home |
| **Cloudflare Tunnel** plus Cloudflare Access | A public HTTPS URL behind single sign-on | Everything rests on the Access policy: one mistake puts GET actions on the internet. The Host check would need loosening, and requests arrive from the Mac's loopback | Only with care |
| **Port forwarding** | The hub on your public IP | Plaintext HTTP and the PIN cross the internet. The per-device lockout is beaten by rotating IPs | **Never** |
