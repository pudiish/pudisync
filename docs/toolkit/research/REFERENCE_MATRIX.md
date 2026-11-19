# Reference Matrix

| Reference | Best thing to study | PudiSync use | Caution |
|---|---|---|---|
| [LedFx audio.py](https://github.com/LedFx/LedFx/blob/main/ledfx/effects/audio.py) | Shared audio source, subscribers, normalized analysis integration | Shared feature service | Python/GPL project; adapt concept, do not copy code blindly |
| [LedFx melbank.py](https://github.com/LedFx/LedFx/blob/main/ledfx/effects/melbank.py) | 4096 FFT, 30 kHz analysis, mel/filter bank options | Evaluate frequency resolution | Latency and platform differ |
| [LedFx Spotlight](https://github.com/LedFx/LedFx/blob/main/ledfx/effects/spotlight.py) | Bounded simultaneous spots, fades, adaptive spawn, presets | Pulse / concert-style accents | Confirm strip topology and guard interaction |
| [LedFx Soap](https://github.com/LedFx/LedFx/blob/main/ledfx/effects/soap2d.py) | Persistent OpenSimplex2 noise field | Trippy / Aurora | 2D and CPU cost; implement 1D equivalent first |
| [LedFx Plasma2D](https://github.com/LedFx/LedFx/blob/main/ledfx/effects/plasma2d.py) | Multi-sine procedural field and audio feature modulation | Trippy | Matrix-specific visual mapping |
| [LedFx Concentric](https://github.com/LedFx/LedFx/blob/main/docs/effects/matrix/concentric.md) | Expanding/contracting rings driven by selectable band | Future matrix experience | Not a native 1D effect |
| [Hyperion mood-blobs](https://github.com/hyperion-project/hyperion.ng/blob/master/effects/mood-blobs.py) | Smooth phase-driven blobs and palette evolution | Room scenes | Old scripting model; don't adopt wholesale |
| [Hyperion effect presets](https://github.com/hyperion-project/hyperion.ng/tree/master/effects) | Config JSON associated with reusable effects | Declarative scene presets | Inspect schema/licence if reuse is proposed |
| [WLED AudioReactive V2](https://github.com/MoonModules/WLED-AudioReactive-Usermod) | Audio feature provider separated from visual effects | Shared DSP boundary | Different hardware/runtime assumptions |
| [HyperHDR](https://github.com/awawa-dev/HyperHDR) | Floating-point/linear colour pipeline, smoothing and dithering | Output quality research | Avoid duplicating existing PudiSync colour processing |
