# Privacy: what PudiSync sees, keeps and sends

PudiSync has to look at your screen to light the room. This page says exactly what
happens to what it sees, so you can check each claim. The tests named here run on
every change (`make test`).

## What it sees

- **The screen**, through macOS's Screen Recording permission, which you grant and can
  revoke in System Settings › Privacy & Security. macOS shows its recording indicator
  while sync runs.
- **Only while sync is on.** Turning the lights off, or Colour or Effect, stops the capture.
- **A webcam only when you start a webcam calibration** (Settings › Colour calibration),
  and never the Mac's built-in camera.
- **The Mac's sound only while something uses it**: a beat effect (the Pulse look, or
  Beat bounce or Beat shake), a look's *Follow the sound* set above Off, or a Room
  Studio scene's *Follow the sound* set above Off. **Calm** turns all of these off in
  one tap and closes the sound. It comes through the same Screen Recording permission.
  Only the last 43 ms are held in memory, long enough to measure the bass, mids and
  highs, and each block is then overwritten: **sound is never recorded, stored or sent
  anywhere**, and with nothing using it, it is not captured at all.
- **Room Studio never captures the screen.** A scene is drawn from its own palette. When
  a scene follows the sound, macOS needs a display stream to deliver it, so one is
  opened with no picture output attached (2x2 pixels, once a second, never read); macOS
  still shows its recording indicator meanwhile.

## What happens to the picture

- Each captured frame stays **in memory**, is reduced to one colour per LED (a few
  hundred colours), and is replaced by the next frame. **The picture is never written
  to disk and never sent anywhere.**
- The per-LED colours go to **your LED board** on your home Wi-Fi, and to the hub's own
  page and Mac app as a small live preview of the strip.
- **Protected video** (Netflix, Prime Video, JioHotstar and others) is hidden from
  screen capture by the service itself. PudiSync sees black and glows warm instead;
  it never tries to get around the protection.

## What it keeps on disk

All in the PudiSync folder on your Mac, **readable by your account alone** (files
0600, folders 0700, set when the hub starts; test 49):

| File | What it holds | Kept |
|---|---|---|
| `config.json` | Settings and your PIN | Until changed |
| `config.journal.jsonl` | Which setting changed when (never the PIN) | About 1 MB, then rotated |
| `backups/` | Settings saved before each import or restore | Newest 10 |
| `recordings/rec-*.npz` | Per-LED colours, only when you ask for a recording | Newest 10 |
| `recordings/calibration-view.jpg` | The webcam's picture of the room from the last calibration, for checking the aim | Until the next calibration; delete it any time |
| `~/Library/Logs/ambilight.log` | Start-up, errors, warnings. No screen content | Until deleted |

## What it sends over the network

Only these, and a test fails if code that can reach the network appears anywhere
else (test 49):

| To | What | When |
|---|---|---|
| Your LED board | LED colours; light and preset commands | While the lights run |
| Phones, iPads, the Mac app on your Wi-Fi | The hub's page, status and the strip's preview | When they open it |
| Bonjour on your Wi-Fi | The hub's name, so devices can find it | While the hub runs |
| GitHub (WLED's releases) | A firmware download, HTTPS, checksum verified | Only when you set up a new board over USB |

**Nothing goes to the internet otherwise: no analytics, no accounts, no cloud.**

## Who can control it

- Any device on your Wi-Fi can use the everyday controls (lights, looks, brightness)
  unless you set a PIN; with a PIN, every device must know it.
- Some things **only this Mac, or a device with the PIN,** can do, because they could
  send your screen's colours elsewhere, open a camera, keep a recording, reveal your
  Wi-Fi networks or lock you out: changing the board's address, ports or output,
  setting or removing the PIN or protected mode, starting the webcam or a recording,
  flashing a board, and restoring connection settings from a backup (test 49).
- Requests from other websites, and DNS-rebinding tricks, are refused (test 18).
- **Never forward the hub's port to the internet.** It is built for your home network.

## Removing everything

Stop the hub, delete the PudiSync folder and `~/Library/Logs/ambilight.log`, and
remove the hub from System Settings › Privacy & Security › Screen Recording.
