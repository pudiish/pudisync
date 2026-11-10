# Ambilight Hub

`hub.py` runs on the Mac, captures the screen, grades the colour and streams it
to a WLED board (ESP8266) that drives the monitor strip and a bed strip. The
phone page, the Mac app and the board's own page control it over HTTP.

- File map and reading order: `docs/ARCHITECTURE.md`
- Workstreams, port bases, approval flow and merge order: `docs/ROADMAP.md`

## Hard rules
- **Camera:** never open a camera unless the user starts camera day
  (`docs/ROADMAP.md`). Then use only the external Lenovo webcam, picked by
  name (`--camera Lenovo`). Never the built-in camera, never by index.
- **Live hardware:** send nothing to the real WLED board or to the running hub
  on port 8080. Experiment with `tools/fake_wled.py` and a sandbox hub started
  with `--no-bonjour` on your own ports.
- **Secrets:** don't read or print `config.json`; it holds the user's PIN.
- **Tests:** `AMBILIGHT_TEST_PORT_BASE=<your base> make test`. Your base is in
  your workstream in `docs/ROADMAP.md`; the default 8790 is the user's.
- **Git:** work on your own branch in your own worktree. PRs target
  `feature/color-engine`, never `main`.
