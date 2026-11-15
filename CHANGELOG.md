# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions: [SemVer](https://semver.org); see `docs/ENGINEERING.md`.

## [Unreleased]

### Added
- Vivid sync mode: deep shadows and full highlights that still light the room in dark scenes.
- `supply_ma`: both strips' current limits together stay within 80% of the shared adapter (default 5000 mA; 0 = separate adapters).
- Build id (`VERSION` plus the git commit) in `/api/status` and the startup log.
- Settings journal: every settings change is appended to `config.journal.jsonl` with the build, old and new value.

### Changed
- TX (GPIO1) is offered first in the bed strip's pin picker, on the phone page and in the Mac app.

## prototype-v1

The first prototype, frozen as a git tag: 874 hub tests, 53 Mac app tests.
