# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions: [SemVer](https://semver.org); see `docs/ENGINEERING.md`.

## [Unreleased]

### Changed
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
