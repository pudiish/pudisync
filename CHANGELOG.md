# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions: [SemVer](https://semver.org); see `docs/ENGINEERING.md`.

## [Unreleased]

### Added
- Experience engine (`docs/EXPERIENCE.md`, from the R&D toolkit in `docs/toolkit/`). Two destinations: Monitor Sync and Room Studio.
- Monitor Sync looks Cinematic (slow glide, faint drift of light, for films) and Trippy (the screen's own colours folded, warped and flowing along the strip; moves with the sound off).
- *Follow the sound* on Home: Off, Subtle, Balanced or Expressive, remembered per style and off for everyone until chosen. The sound only modulates bounded parameters and eases back to neutral when it stops or goes stale; a black screen stays black.
- Calm switch (`calm`): no motion from the sound and no sound captured, in one tap.
- Live bass meter, beat count and tempo on Home, and a Test beat button that runs a synthetic beat through the normal output path and guard with no sound opened.
- Room Studio, its own tab and section: Neon Coast, Aurora, Deep Ocean, Golden Hour and Hearth, drawn from palettes with no screen capture, through the same calibration, zones, brightness and safety guard. Brightness, speed, colour intensity and its own *Follow the sound*; duplicate, rename, delete, reset, export and import scenes (strict JSON: no code, addresses or safety settings).

### Changed
- Pulse made visible (audio handoff Phase A): the bounce wave is ±18 LEDs (was 6) and fades over 0.45 s (was 0.28), dark scenes glow in their own hue on the beat while true black stays black, and Shake ripples brightness along the strip so it shows on even colours. A beat adds at most 8% of full white to the whole strip, under the guard's 10% flash swing. Measured with `tools/beat_visibility.py`: bright 32 to 194 LEDs changed, dark scene's biggest change 3 to 27 of 255.

### Changed
- New PudiSync logo: a glowing ribbon P. `ambilight/web/icon.svg` is the only drawing of it; the phone page header, browser tab, manifest, README, the iPhone Home Screen PNG (`make icons`) and the Mac app icon (`make app`) are all rendered from that file.
- Colour match off by default: on the owner's strip the datasheet model cut blue to 44% at white, turning pale colours lime and white yellow-green. Without it the strip is driven in the screen's own linear light, and gamma, contrast and dark lift act on brightness only, so hue no longer shifts (it used to curve each channel apart).
- Two sync modes instead of four: Vivid and Immersive. Immersive folds Cinema, Gaming and Enriched into one: low contrast (1.2) and a strong flood so dark scenes light the whole room, punchier colour, a wider wash, a glide that adaptive pace speeds up in action. Modes now set contrast and dark lift too (Vivid 2.0 / 0.4, Immersive 1.2 / 0.8); a config in a retired mode keeps its look without the name.

### Added
- Vivid sync mode, now the fresh-install default: Enriched's tone (deep shadows, full highlights), a light colour flood so dark scenes still light the room, saturation 1.2.
- Adaptive pace (`adaptive_pace`, default 1): the light glides up to 1.8x slower in calm scenes and follows up to 1.7x faster in action, from the picture's rate of colour change. Cuts ease in over several frames, and drops in brightness are limited like rises.
- Hue balance (`hue_balance`, default 0.35): blue and deep red, which a WS2812B shows dimly, are lifted within the brightness headroom; nothing is dimmed. Slider on the phone page and in the Mac app.
- `supply_ma`: both strips' current limits together stay within 80% of the shared adapter (default 5000 mA; 0 = separate adapters).
- Build id (`VERSION` plus the git commit) in `/api/status` and the startup log.
- Settings journal: every settings change is appended to `config.journal.jsonl` with the build, old and new value.

### Changed
- TX (GPIO1) is offered first in the bed strip's pin picker, on the phone page and in the Mac app.
- Adaptive brightness judges a colour by its strongest channel, so saturated blue and red no longer fade beside bright yellow or green.

### Fixed
- Webcam calibration finds the monitor when the screen lights the desk below it, and dims the strip when the wall glow overexposes the camera.

## prototype-v1

The first prototype, frozen as a git tag: 874 hub tests, 53 Mac app tests.
