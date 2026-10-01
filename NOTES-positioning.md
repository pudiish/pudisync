# Ambilight Hub — where it sits in the market

Researched 2 Oct 2026. Measurements are from this Mac (M-series, macOS 26.5.2).

## What already exists

| Project | Stars | Licence | macOS | WLED out | Phone control |
|---|---|---|---|---|---|
| [hyperion.ng](https://github.com/hyperion-project/hyperion.ng) | 3,874 | MIT | yes | yes | web UI (desktop-first) |
| [HyperHDR](https://github.com/awawa-dev/HyperHDR) | 2,163 | MIT | yes (x64 + arm64) | yes | web UI (desktop-first) |
| [maslight](https://github.com/asermas/maslight) | 0 | MIT | **no** (Win/Linux only) | yes | — |
| Firefly Luciferin | — | GPL | no (Win/Linux) | own firmware | — |
| Prismatik / Lightpack | — | GPL | aging | no | — |

Both mature options are C++ projects that do far more than this one: HDR tone
mapping, USB grabbers, TV/Raspberry Pi capture, LED calibration wizards, dozens
of output protocols. If the goal were "most capable ambilight", installing
HyperHDR is the rational answer and this project should not exist.

## Why this one still earns its place

Three things the big projects do not do well for this specific setup:

1. **Phone-first control.** Hyperion and HyperHDR ship dense desktop
   configuration UIs. This is a mobile web page a non-technical person can use:
   one big switch, two mood cards, three plain-language sliders. No app, no
   cloud, no account.
2. **A strip longer than the screen.** The desk strip is ~240 LEDs but only a
   slice sits behind the monitor. The `left_led` / `right_led` calibration plus
   the fade-out beyond the screen is purpose-built for that; the general tools
   assume LEDs bound the display.
3. **Every action is a plain URL.** `/api/sync/on`, `/api/profile/game` — so iOS
   Shortcuts, Home Assistant or a desk button can drive it with no integration
   layer. Hyperion has a JSON-RPC API, but not one you can paste into Shortcuts.

Honest limits: no HDR handling, one capture source, one output protocol, and
no TV/console input. This is a desk-sized tool, not a Hyperion replacement.

## The performance ceiling (measured, not estimated)

```
capture only (mss.grab)       15.70 ms   ->  63.7 fps ceiling
processing only                0.05 ms   -> 20842 fps
FULL frame path (no UDP)      15.25 ms   ->  65.6 fps ceiling
```

Live measurements: **58–66 fps at 240 LEDs**, sustained over 1,186 frames of
fullscreen animation. Published figures put `mss` at ~50 fps on an M1 Air, so
there is little left on the table at this layer.

**Capture is 100% of the cost. The numpy pipeline is ~300x faster than the
capture it feeds.** Optimising the processing further would buy nothing.

The only way past ~65 fps is replacing `mss` with macOS ScreenCaptureKit
(needs PyObjC, not currently installed) — which is exactly what the capture
plugin interface exists to allow. Worth doing only if 60 fps proves
insufficient in real use; it is not a prerequisite for shipping.

## What to reuse rather than rebuild

- **WLED UDP DNRGB** — already implemented here, verified against the docs and
  packet-tested. No library needed; it is 20 lines of stdlib `socket`.
- **ScreenCaptureKit via PyObjC** — the one genuine upgrade path. Reuse Apple's
  framework, do not write capture code.
- **Do not** vendor Hyperion/HyperHDR code. They are C++/Qt; the integration
  cost exceeds writing the small amount of Python this needs.
