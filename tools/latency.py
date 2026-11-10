#!/usr/bin/env python3
"""Glass-to-LED latency: how long after the screen changes the wall light follows.

    python3 tools/latency.py --camera Lenovo     # camera day: the hub syncing on this Mac
    python3 tools/latency.py --dry-run           # a simulated room; no camera, no hub

The webcam sees the monitor and the strip's glow on the wall in the same
picture. The screen flashes from black to white and the syncing strip follows.
Latency is when the wall's glow is halfway to its new level minus when the
screen's border was, both read from the same camera frames, so the camera's own
delay and the display's scan-out cancel out. Crossings are interpolated
between frames, so a 30 fps camera still resolves a few milliseconds over many
trials. Smoothing (the hub's blend setting) is part of what is measured,
because it is part of what you see.

The webcam is picked by name and never by number; the built-in camera is never
used. The screen and wall regions come from the wall-colour calibration when it
has run, and are found the same way otherwise.
"""
import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ambilight import camrun  # noqa: E402
from ambilight.engine import KeepAwake, resolve_display  # noqa: E402
from ambilight.plugins.capture_quartz import QuartzCapture  # noqa: E402
from ambilight.smoothing import settle_seconds  # noqa: E402
from ambilight.telemetry import percentile  # noqa: E402

TRIALS = 20
SETTLE_S = 1.5          # dark before each flash, plus the hub's own settle time
LEAD_S = 0.3            # frames recorded before the flash, to know the dark level
WATCH_S = 1.0           # frames recorded after it, plus the hub's own settle time
MIN_STEP = 0.02         # a change smaller than this (0..1) is not trusted as seen
DRY_RUN_TOLERANCE_MS = 10.0


# --- reading one flash ----------------------------------------------------------
def crossing(times, levels, start):
    """When `levels` first cross halfway from their level before `start` to their
    final level and stay there, interpolated between samples; None if they barely change."""
    before = [v for t, v in zip(times, levels) if t < start]
    if not before or len(levels) < 6:
        return None
    base, final = statistics.fmean(before), statistics.fmean(levels[-len(levels) // 3:])
    if final - base < MIN_STEP:
        return None
    half = base + (final - base) / 2
    for i in range(1, len(levels)):
        rising = levels[i - 1] < half <= levels[i] and times[i] >= start
        if rising and all(v >= half for v in levels[i + 1:i + 3]):
            share = (half - levels[i - 1]) / (levels[i] - levels[i - 1])
            return times[i - 1] + share * (times[i] - times[i - 1])
    return None


def trial_latency(samples, start):
    """Seconds from the screen's half-way point to the wall's, or None when either is unreadable."""
    times = [s[0] for s in samples]
    screen = crossing(times, [s[1] for s in samples], start)
    wall = crossing(times, [s[2] for s in samples], start)
    return None if screen is None or wall is None else wall - screen


def measure(rig, trials, settle_s, lead_s, watch_s):
    """Flash the screen `trials` times; the latency of each flash, None where unreadable."""
    found = []
    for _ in range(trials):
        rig.show(0.0)
        rig.wait(settle_s)
        rig.begin()
        rig.wait(lead_s)
        start = rig.now()
        rig.show(1.0)
        rig.wait(watch_s)
        found.append(trial_latency(rig.end(), start))
    rig.show(0.0)
    return found


def report(latencies):
    usable = [s * 1000 for s in latencies if s is not None]
    result = {"trials": len(latencies), "usable": len(usable)}
    if usable:
        result.update(median_ms=round(statistics.median(usable), 1), p95_ms=round(percentile(usable, 0.95), 1),
                      min_ms=round(min(usable), 1), max_ms=round(max(usable), 1))
    return result


# --- the simulated room (--dry-run) ----------------------------------------------
CAMERA_FPS = 30.0
EXPOSURE_S = 0.012
CAMERA_DELAY_S = 0.060  # the webcam's own pipeline; it cancels out
DISPLAY_S = 0.012       # scan-out; it cancels out too
JITTER_S = 0.008
SCREEN_GAIN, WALL_GAIN, SPILL, AMBIENT, NOISE = 0.8, 0.5, 0.04, 0.02, 0.004


def eased(level, target, seconds, tau):
    return target if tau <= 0 else target + (level - target) * math.exp(-seconds / tau)


class SimulatedRoom:
    """A monitor, a syncing strip and a webcam on a virtual clock: nothing sleeps, nothing opens.

    The strip starts following each screen change `delay_s` later (with jitter)
    and eases in like the hub's smoothing. The camera averages each frame over
    its exposure, adds sensor noise and stamps frames late by its own delay,
    and the screen's light spills onto the wall. `truth` holds each flash's
    exact latency between the same two lights the camera sees, for checking
    what the camera recovers.
    """

    def __init__(self, delay_s, settle_s, seed=7):
        self.rng = np.random.default_rng(seed)
        self.delay_s = delay_s
        self.tau = settle_s / 3
        self.t = 0.0
        self.screen_steps = [(-10.0, 0.0)]
        self.strip_steps = [(-10.0, 0.0)]
        self.truth = []
        self.flashes = []
        self.frame_at = float(self.rng.uniform(0, 1 / CAMERA_FPS))
        self.recording = None

    def now(self):
        return self.t

    def show(self, level):
        shown = self.t + DISPLAY_S
        delay = max(0.0, self.delay_s + float(self.rng.normal(0, JITTER_S)))
        if level > self.screen_steps[-1][1]:
            self.flashes.append(self.t)
        self.screen_steps.append((shown, level))
        self.strip_steps.append((shown + delay, level))

    def screen(self, t):
        return [level for when, level in self.screen_steps if when <= t][-1]

    def strip(self, t):
        level, target, since = 0.0, 0.0, None
        for start, new in self.strip_steps:
            if start > t:
                break
            if since is not None:
                level = eased(level, target, start - since, self.tau)
            target, since = new, start
        return eased(level, target, t - since, self.tau)

    def screen_light(self, t):
        return SCREEN_GAIN * self.screen(t) + AMBIENT

    def wall_light(self, t):
        return WALL_GAIN * self.strip(t) + SPILL * self.screen(t) + AMBIENT

    def half_way(self, light, flash):
        """When `light` is exactly half-way from before `flash` to now; it only rises in between."""
        low, high = flash, self.t
        half = (light(flash) + light(self.t)) / 2
        for _ in range(40):
            mid = (low + high) / 2
            low, high = (low, mid) if light(mid) >= half else (mid, high)
        return high

    def expose(self, light):
        moments = np.linspace(self.frame_at - EXPOSURE_S, self.frame_at, 12)
        return float(np.mean([light(m) for m in moments]) + self.rng.normal(0, NOISE))

    def wait(self, seconds):
        end = self.t + seconds
        while self.frame_at <= end:
            if self.recording is not None:
                self.recording.append((
                    self.frame_at + CAMERA_DELAY_S, self.expose(self.screen_light), self.expose(self.wall_light)))
            self.frame_at += 1 / CAMERA_FPS
        self.t = end

    def begin(self):
        self.recording = []

    def end(self):
        recorded, self.recording = self.recording, None
        flash = self.flashes[-1]
        self.truth.append(self.half_way(self.wall_light, flash) - self.half_way(self.screen_light, flash))
        return recorded


def dry_run(delay_ms, trials=TRIALS, blend=0.3, seed=7):
    """(what the camera measured, the true median ms) in a simulated room."""
    settle = settle_seconds(blend)
    room = SimulatedRoom(delay_ms / 1000, settle, seed)
    found = measure(room, trials, SETTLE_S + settle, LEAD_S, WATCH_S + settle)
    return report(found), round(statistics.median(room.truth) * 1000, 1)


# --- the real room (camera day) ---------------------------------------------------
class PinnedHub(camrun.HubClient):
    """camrun's hub client, sending the PIN when the hub has one."""

    def __init__(self, port, pin=None):
        super().__init__(port)
        self.pin = pin

    def get(self, path, **params):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(url, headers={"X-Ambilight-Pin": self.pin} if self.pin else {})
        with urllib.request.urlopen(request, timeout=10) as resp:
            return json.loads(resp.read())


def level(image, mask):
    """Mean brightness (0..1) of the masked part of a camera picture."""
    return float(image[mask].mean()) / 255.0


class RecordingCamera(camrun.Camera):
    """camrun's webcam reader that also keeps each frame's time and the screen
    and wall levels while recording. Its own delay is the same for both regions."""

    def __init__(self, index):
        self.regions = None
        self.samples = None
        super().__init__(index)

    def read_loop(self):
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                continue
            seen = time.monotonic()
            scale = camrun.WORK_WIDTH / frame.shape[1]
            small = self.cv2.resize(frame, None, fx=scale, fy=scale, interpolation=self.cv2.INTER_AREA)[..., ::-1]
            with self.lock:
                self.frame, self.count = small.copy(), self.count + 1
                if self.samples is not None:
                    screen, wall = self.regions
                    self.samples.append((seen, level(small, screen), level(small, wall)))


class CameraRig:
    """The test window on the synced display, and the webcam watching it and the wall."""

    def __init__(self, window, camera):
        self.window, self.camera = window, camera

    def now(self):
        return time.monotonic()

    def show(self, brightness):
        self.window.show((round(255 * brightness),) * 3)

    def wait(self, seconds):
        self.window.wait(seconds)       # keeps the window's run loop turning

    def begin(self):
        with self.camera.lock:
            self.camera.samples = []

    def end(self):
        with self.camera.lock:
            recorded, self.camera.samples = self.camera.samples, None
        return recorded


def find_regions(window, hub, camera, log):
    """Where the screen border and the wall glow are in the camera picture."""
    saved = camrun.RECORDINGS_DIR / "calibration-regions.npz"
    if saved.exists():
        log(f"Using the screen and wall found by the last calibration ({saved.name})")
        data = np.load(saved)
        return data["screen"], data["wall"]
    log("Finding the screen and the wall glow…")
    try:
        return camrun.locate(window, hub, camera)
    finally:
        hub.get("/api/calibrate/done")


def run_camera(port, camera_name, pin, trials, log=print):
    """Measure on the real hardware. Returns (latencies, status before, hub telemetry after)."""
    index, name = camrun.pick_camera(camrun.list_cameras(), camera_name)    # refuses built-in cameras
    log(f"Using {name}")
    hub = PinnedHub(port, pin)
    status = hub.get("/api/status")
    display = resolve_display(status["monitor_index"], QuartzCapture.display_count())
    camera = RecordingCamera(index)
    awake = KeepAwake.start(display=True)
    try:
        window = camrun.PatchWindow(camrun.display_id(display))
        try:
            camera.regions = find_regions(window, hub, camera, log)
            hub.get("/api/sync/on")
            settle = settle_seconds(status["blend"])
            log(f"Flashing the screen {trials} times…")
            found = measure(CameraRig(window, camera), trials, SETTLE_S + settle, LEAD_S, WATCH_S + settle)
        finally:
            window.close()
    finally:
        camera.close()
        if awake is not None:
            awake.stop()
    return found, status, hub.get("/api/status")["telemetry"]


def describe(result):
    if not result["usable"]:
        return f"no usable flashes out of {result['trials']}"
    return (f"median {result['median_ms']} ms, p95 {result['p95_ms']} ms "
            f"(min {result['min_ms']}, max {result['max_ms']}; {result['usable']} of {result['trials']} flashes)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure glass-to-LED latency with a webcam.")
    ap.add_argument("--camera", metavar="NAME",
                    help="part of the external webcam's name, e.g. Lenovo (required; never the built-in camera)")
    ap.add_argument("--port", type=int, default=8080, help="the running hub's web port (default %(default)s)")
    ap.add_argument("--pin", default=os.environ.get("AMBILIGHT_PIN"), help="the hub's PIN, if it has one")
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--dry-run", action="store_true", help="a simulated room: no camera, no hub")
    ap.add_argument("--inject-ms", type=float, default=80.0,
                    help="dry run: the simulated hub, Wi-Fi and board delay (default %(default)s)")
    args = ap.parse_args(argv)

    if args.dry_run:
        result, truth = dry_run(args.inject_ms, args.trials)
        print(f"Simulated room: {args.inject_ms:.0f} ms delay plus smoothing at blend 0.3, "
              f"true half-way latency {truth} ms")
        print(f"Measured through the simulated camera: {describe(result)}")
        ok = result["usable"] >= args.trials / 2 and abs(result["median_ms"] - truth) <= DRY_RUN_TOLERANCE_MS
        print("PASS" if ok else f"FAIL: off by more than {DRY_RUN_TOLERANCE_MS:.0f} ms, or too few usable flashes")
        return 0 if ok else 1

    if not args.camera:
        ap.error("--camera is required: name the external webcam, e.g. --camera Lenovo "
                 "(the built-in camera is never used)")
    try:
        found, status, telemetry = run_camera(args.port, args.camera, args.pin, args.trials)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Latency measurement stopped: {exc}")
        return 1
    result = report(found)
    mac = telemetry["latency_ms"]
    print(f"Glass to LED: {describe(result)}")
    print(f"  of which on the Mac, screen to send: p50 {mac['p50']} ms, p95 {mac['p95']} ms")
    print(f"  settings: blend {status['blend']}, {status['fps']} fps, {status['layout']} layout")
    return 0 if result["usable"] >= args.trials / 2 else 1


if __name__ == "__main__":
    sys.exit(main())
