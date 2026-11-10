"""The sync engine: a capture thread and an output thread, restartable, never fatal."""
import logging
import os
import shutil
import subprocess
import threading
import time

import numpy as np

from . import bed, color, mouse, processing, zones as zonelib
from .plugins import CAPTURES, OUTPUTS
from .plugins.output_wled_udp import HOLD_FOREVER
from .recorder import Recorder, new_path
from .safety import Guard
from .smoothing import Quantizer, Smoother, settle_seconds

# Each packet tells WLED to hold this long; comfortably over one frame interval
# so a dropped packet does not flicker, short enough that a crash self-heals.
FRAME_TIMEOUT_S = 2

RETRY_DISPLAY_S = 2.0   # how often to look for a display that went to sleep
DISPLAY_CHECK_S = 2.0   # how often to notice a monitor being plugged in or removed

log = logging.getLogger(__name__)

# Letterbox search: a taller strip is grabbed only a few times a second, so the
# per-frame capture stays as thin (and as fast) as before.
SEARCH_ROWS = 500
SEARCH_EVERY_S = 0.25


class CaptureLost(Exception):
    """The screen we were capturing disappeared -- sleep, lid close, unplug."""


def resolve_display(configured, count):
    """The display number to capture. 0 means automatic: the last attached
    display, which is the external monitor when there is one and the built-in
    screen when there is not. A fixed number that is no longer attached falls
    back to the same choice rather than failing."""
    if count < 1:
        return 1
    return count if configured == 0 or configured > count else configured


def capture_hint(exc):
    """Turn a capture failure into something the user can act on."""
    text = str(exc)
    if "not found" in text or "0 attached" in text:
        return ("no display to capture -- the screen is asleep or the lid is "
                "closed. Sync resumes on its own when it wakes.")
    return f"capture failed: {text}"


class _PictureTracker:
    """Smooths picture-edge detections so a dark scene cannot make the lights jump.

    Like HyperHDR's border detector: a new position must be seen consistently
    for about a second before it is adopted, and frames too dark to judge are
    ignored outright instead of being read as "no bars".
    """

    TOLERANCE = 16      # pixels of jitter that still count as "the same edge"
    CONFIRM = 4         # matching detections (4 a second) before a change is adopted

    def __init__(self, detect=processing.find_picture,
                 fallback=lambda img: (0, 0, img.shape[1])):
        self._detect = detect
        self._fallback = fallback
        self.applied = None
        self._pending = None
        self.agreeing = 0

    @classmethod
    def _same(cls, a, b):
        return all(abs(x - y) <= cls.TOLERANCE for x, y in zip(a, b))

    def update(self, image):
        """Returns the edges to sample with."""
        if self.applied is None:
            self.applied = self._fallback(image)
        if processing.is_dark(image):
            return self.applied
        seen = self._detect(image)
        if self._same(seen, self.applied):
            self._pending, self.agreeing = None, 0
        elif self._pending is not None and self._same(seen, self._pending):
            self.agreeing += 1
            if self.agreeing >= self.CONFIRM:
                self.applied, self._pending, self.agreeing = seen, None, 0
        else:
            self._pending, self.agreeing = seen, 1
        return self.applied


class _FrameSource:
    """Grabs the four screen edges, following the picture rectangle."""

    def __init__(self):
        self._tracker = _PictureTracker(
            processing.find_frame, lambda img: (0, img.shape[0], 0, img.shape[1]))
        self._next = 0.0

    def grab(self, capture, cfg):
        frame = capture.grab_full()
        height, width = frame.shape[:2]
        rect = (0, height, 0, width)
        if cfg["letterbox"]:
            now = time.perf_counter()
            if now >= self._next:
                self._next = now + SEARCH_EVERY_S
                self._tracker.update(frame)
            rect = self._tracker.applied or rect
        return processing.slice_edges(frame, rect, cfg["band_height"], cfg["edge_skip"])


class OutputStage:
    """Target colours in, wire bytes out: smoothing, LED gamma, zones, anti-flicker.

    The capture thread sets targets as fast as the screen can be grabbed; the
    output thread asks for frames at a steady rate, so motion stays fluid even
    when capture is slower than the output.
    """

    STALE_S = 2.0       # no fresh capture for this long: the screen info is stale
    FADE_S = 1.5        # ...and the sync layer fades out over this long

    def __init__(self):
        self.lock = threading.Lock()
        self.smoother = Smoother()
        self.quantizer = Quantizer()
        self.guard = Guard()
        self.last_target = None
        self.bed = bed.BedStage()

    def set_target(self, target, now):
        with self.lock:
            self.smoother.set_target(target, now)
            self.last_target = now

    def frame(self, now, cfg):
        """Bytes to send now, or None before the first target."""
        with self.lock:
            shown = self.smoother.step(now, settle_seconds(cfg["blend"]))
            if shown is None:
                return None
            # A frozen or lost capture must not hold the last bright frame on
            # the wall indefinitely.
            age = now - self.last_target
            fade = max(0.0, 1.0 - (age - self.STALE_S) / self.FADE_S if age > self.STALE_S else 1.0)
            levels = color.to_led(shown, cfg) * fade
            # Zones paint over the sync layer after smoothing, so a solid zone
            # is not dragged toward screen colour.
            composed = zonelib.composite(levels, cfg["zones"], cfg["led_count"])
            safe = self.guard.apply(composed * cfg["brightness"], now, cfg)
            pixels = self.quantizer.apply(safe)
            if not bed.streams(cfg):
                return pixels
            # Board LEDs between the hub's and the bed's (a monitor strip longer than its frame) stay dark.
            gap = np.zeros((bed.first_led(cfg) - len(pixels), 3), dtype=np.uint8)
            return np.concatenate([pixels, gap, self.bed.frame(self.smoother.target, now, cfg, fade)])

    def safety(self):
        with self.lock:
            return self.guard.state()


class _Pipeline:
    """Screen in, per-LED target colours out. Owns the mapping cache."""

    def __init__(self, stage=None):
        self._key = None
        self._mapping = None
        self._fade = None
        self.stage = stage or OutputStage()

    def invalidate(self):
        self._key = None

    def _ensure(self, width, cfg):
        """Rebuild the LED-to-column mapping only when its inputs change."""
        key = (width, cfg["led_count"], cfg["left_led"], cfg["right_led"],
               cfg["fade_leds"])
        if key == self._key:
            return
        self._mapping = processing.build_mapping(
            cfg["led_count"], cfg["left_led"], cfg["right_led"], width)
        self._fade = processing.build_fade(self._mapping, cfg["fade_leds"])
        self._key = key

    def measure(self, band, cfg):
        """Bottom band -> (linear per-LED averages, graded target for every LED)."""
        # Retina scaling makes the captured width differ from the reported
        # monitor width, so map against what we actually got.
        self._ensure(band.shape[1], cfg)
        linear = processing.spread(
            processing.zone_colors(band, self._mapping, cfg["colour_weight"]), cfg["spread"])
        target = processing.extend_edges(color.grade(linear, cfg), *self._fade)
        return linear, target

    def measure_edges(self, edges, cfg):
        """Four screen edges -> (linear averages, target), for a strip round the monitor."""
        counts = (cfg["edge_bottom"], cfg["edge_right"], cfg["edge_top"], cfg["edge_left"])
        share = processing.box_coverage(
            counts, cfg["led_pitch_mm"], cfg["screen_mm_w"], cfg["screen_mm_h"],
            (cfg["box_mm_w"], cfg["box_mm_h"]))
        linear = processing.spread(
            processing.frame_colors(edges, counts, share, cfg["colour_weight"]),
            cfg["spread"], ring=True)
        target = np.zeros((cfg["led_count"], 3), dtype=np.float32)
        used = min(len(linear), len(target))
        target[:used] = color.grade(linear[:used], cfg)
        return linear, target

    def render(self, band, cfg, now=None):
        """One bottom band straight through to wire bytes, for one-shot use."""
        now = time.perf_counter() if now is None else now
        self.stage.set_target(self.measure(band, cfg)[1], now)
        return self.stage.frame(now, cfg)


class _Pacer:
    """Time-based frame pacing and a rolling frame-rate figure."""

    REPORT_EVERY = 0.5
    RESYNC_AFTER = 0.25     # drop the schedule rather than sprint to catch up

    def __init__(self):
        now = time.perf_counter()
        self._next = now
        self._window_start = now
        self._frames = 0
        self.fps = 0.0

    def tick(self, target_fps, stop):
        self._frames += 1
        now = time.perf_counter()
        elapsed = now - self._window_start
        if elapsed >= self.REPORT_EVERY:
            self.fps = self._frames / elapsed
            self._frames = 0
            self._window_start = now

        # Schedule from the target grid, not from "now", so one slow frame does
        # not permanently shift the cadence.
        self._next += 1.0 / max(1, target_fps)
        sleep = self._next - time.perf_counter()
        if sleep > 0:
            stop.wait(sleep)
        elif sleep < -self.RESYNC_AFTER:
            self._next = time.perf_counter()
        return self.fps


class _BlackFrameWatchdog:
    """Tells missing Screen Recording permission apart from a dark scene.

    A permission failure is black from the very first frame and never recovers.
    A dark scene follows frames that did have light, so it must not warn.
    """

    LIMIT = 30
    MESSAGE = ("captured frames are all black -- grant Screen Recording "
               "permission to the app that runs the hub (Terminal, or Python "
               "when it starts at login) in System Settings > Privacy & "
               "Security > Screen Recording, then restart it.")

    def __init__(self):
        self._dark = 0
        self._ever_lit = False

    def check(self, band):
        """Returns the warning, "" to clear it, or None to leave it alone."""
        # Sub-sampled rows answer this far more cheaply than scanning every
        # pixel. Only when they look black -- and we are about to accuse the
        # user of a permissions problem -- is the full band worth checking.
        if band[::processing.ROW_SAMPLES].max() == 0:
            self._dark += 1
            if self._dark == self.LIMIT and not self._ever_lit:
                if band.max() == 0:
                    return self.MESSAGE
                self._dark = 0       # genuinely lit, just not on a sampled row
                self._ever_lit = True
                return ""
            return None
        was_dark = self._dark
        self._dark = 0
        self._ever_lit = True
        return "" if was_dark else None


class Engine:
    def __init__(self, config):
        self.cfg = config
        self._thread = None
        self.lifecycle = threading.Lock()
        self.output_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.running = False
        # One slot per source, so an output hiccup cannot hide (or clear) a
        # capture problem and vice versa.
        self.errors = {"capture": None, "output": None}
        self.fps = 0.0
        self.capture_name = "none"
        self.output_name = "none"
        self.capture_size = ""
        self.paused = None
        # Set by the setup wizard to paint a fixed pattern instead of the screen.
        self._override = None
        # Last frame actually sent, downsampled for the UI's live preview.
        self._preview = []
        # Smoothing state outlives a capture restart, so a display waking up
        # glides back in instead of snapping.
        self.stage = OutputStage()
        self.emitter = None
        self.output_fps = 0.0
        self.out = None
        self.out_key = None
        self.recorder = None
        self.last_recording = None
        self.awake = None
        self.mouse = mouse.LightsyncMouse(mouse.load_hid())

    @property
    def last_error(self):
        return self.errors["capture"] or self.errors["output"]

    # --- lifecycle ---------------------------------------------------------
    def start(self):
        with self.lifecycle:
            if self.running:
                return True
            # A fresh event per run: a thread from an earlier run that missed
            # its join can never be revived by clearing a shared flag.
            stop = self.stop_event = threading.Event()
            self.errors = {"capture": None, "output": None}
            self._thread = threading.Thread(target=self._run, args=(stop,), daemon=True, name="sync")
            self.emitter = threading.Thread(target=self.emit_loop, args=(stop,), daemon=True, name="emit")
            self.running = True
            self._thread.start()
            self.emitter.start()
            self.awake = KeepAwake.start()
            return True

    def stop(self):
        with self.lifecycle:
            self.stop_event.set()
            for thread in (self._thread, self.emitter):
                if thread is not None:
                    thread.join(timeout=2.0)
            self.running = False
            self.mouse.release()
            if self.awake is not None:
                self.awake.stop()
                self.awake = None
            self._release()
            self.fps = self.output_fps = 0.0

    def start_recording(self, seconds):
        """Keep the next `seconds` of per-LED averages for replay and tuning."""
        if not self.running:
            raise RuntimeError("turn screen sync on before recording")
        self.recorder = Recorder(new_path(), seconds, time.perf_counter())
        return self.recorder.path.name

    def record(self, now, linear):
        recorder = self.recorder
        if recorder is not None and not recorder.add(now, linear):
            self.recorder = None
            # Compressing to disk takes a moment; never on the capture thread.
            threading.Thread(target=self.save_recording, args=(recorder,), daemon=True).start()

    def save_recording(self, recorder):
        try:
            self.last_recording = recorder.save().name
        except OSError as exc:
            log.error("could not save recording %s: %s", recorder.path, exc)

    def set_override(self, pixels):
        """Paint a literal frame (wizard markers, gradients) and pause screen sync."""
        self._override = pixels
        if pixels is not None:
            # Held until the wizard moves on: no capture is refreshing it.
            self._push(pixels, HOLD_FOREVER)

    def clear_override(self):
        self._override = None

    # --- the frame path ----------------------------------------------------
    def _run(self, stop):
        """Keep sync alive across display sleep, lid close, monitor changes and bugs.

        A sleeping display makes the monitor disappear entirely, so opening the
        capture fails. That is temporary, not fatal: wait and retry rather than
        ending the thread, or sync would never come back without a restart.
        An unexpected error is logged and retried the same way, so the lights
        are never left frozen on a dead thread.
        """
        while not stop.is_set():
            try:
                capture = self._open_capture()
                if capture is None:
                    stop.wait(RETRY_DISPLAY_S)
                    continue
                try:
                    self._loop(capture, stop)
                finally:
                    capture.close()
            except Exception as exc:
                log.exception("sync loop failed")
                self.errors["capture"] = f"sync hit an error and is retrying: {exc}"
                stop.wait(RETRY_DISPLAY_S)

    def _open_capture(self):
        """Start the capture plugin. Returns None and records why on failure."""
        try:
            plugin = capture_plugin()
            capture = plugin()
            width, height = capture.open(
                resolve_display(self.cfg.get("monitor_index"), plugin.display_count()),
                self.cfg.get("band_height"))
        except Exception as exc:
            self.errors["capture"] = capture_hint(exc)
            return None
        self.capture_name = capture.name
        self.capture_size = f"{width}x{height}"
        self.errors["capture"] = None
        return capture

    def emit_loop(self, stop):
        """Send smoothed frames at a steady rate, independent of capture speed."""
        pacer = _Pacer()
        while not stop.is_set():
            cfg = self.cfg.snapshot()
            try:
                if self._override is None:
                    pixels = self.stage.frame(time.perf_counter(), cfg)
                    if pixels is not None:
                        self._push(pixels)
            except Exception as exc:
                log.exception("output loop failed")
                self.errors["output"] = f"output hit an error and is retrying: {exc}"
                stop.wait(0.5)
            self.output_fps = pacer.tick(cfg["fps"], stop)

    def _loop(self, capture, stop):
        pipeline = _Pipeline(self.stage)
        pacer = _Pacer()
        watchdog = _BlackFrameWatchdog()
        plugin = type(capture)
        opened_with = (self.cfg.get("band_height"), self.cfg.get("monitor_index"),
                       resolve_display(self.cfg.get("monitor_index"), plugin.display_count()))
        next_display_check = time.perf_counter() + DISPLAY_CHECK_S
        tracker = _PictureTracker()
        next_search = 0.0
        frame_source = _FrameSource()

        while not stop.is_set():
            cfg = self.cfg.snapshot()

            if time.perf_counter() >= next_display_check:
                next_display_check = time.perf_counter() + DISPLAY_CHECK_S
                now_display = resolve_display(cfg["monitor_index"], plugin.display_count())
                if (cfg["band_height"], cfg["monitor_index"], now_display) != opened_with:
                    return      # _run reopens the capture on the display now in use
            elif (cfg["band_height"], cfg["monitor_index"]) != opened_with[:2]:
                return      # a setting changed: reopen with the new one

            # A locked Mac shows the lock screen, not anything worth mirroring;
            # without fresh targets the output stage fades the strip out.
            self.paused = "screen locked" if screen_locked() else None
            if self.paused:
                stop.wait(0.5)
                continue

            crop = (0, None)
            wrapped = cfg["layout"] == "frame"
            if not wrapped and cfg["letterbox"]:
                if time.perf_counter() >= next_search:
                    next_search = time.perf_counter() + SEARCH_EVERY_S
                    try:
                        strip = capture.grab_search(SEARCH_ROWS)
                    except OSError:
                        strip = None        # the band grab below reports a lost display
                    if strip is not None:
                        capture.set_inset(tracker.update(strip)[0])
                if tracker.applied is not None:
                    crop = tracker.applied[1:]
            elif not wrapped:
                capture.set_inset(0)

            try:
                band = self._grab(
                    capture, stop, (lambda: frame_source.grab(capture, cfg)) if wrapped else None)
            except CaptureLost as lost:
                self.errors["capture"] = str(lost)
                return      # _run reopens the capture once a display is back
            if band is None:
                continue

            if wrapped:
                edges, band = band, band["bottom"]
            elif crop != (0, None):
                band = band[:, crop[0]:crop[1]]

            message = watchdog.check(band)
            if message is not None:
                self.errors["capture"] = message or None

            linear, target = (pipeline.measure_edges(edges, cfg) if wrapped
                              else pipeline.measure(band, cfg))
            now = time.perf_counter()
            self.stage.set_target(target, now)
            self.record(now, linear)

            self.fps = pacer.tick(cfg["fps"], stop)

    def _grab(self, capture, stop, grab=None):
        """One frame, or None. Raises CaptureLost when the display went away."""
        try:
            band = (grab or capture.grab)()
        except Exception as exc:
            # The handle is tied to a monitor that no longer exists, so the
            # capture must be rebuilt rather than retried on a dead handle.
            raise CaptureLost(capture_hint(exc)) from None
        if band is None:
            self.errors["capture"] = "capture returned no data"
            stop.wait(0.5)
        return band

    # --- output ------------------------------------------------------------
    def _output(self):
        """Return the configured output, rebuilding it when the target changes.

        Called from the output thread and from HTTP threads (wizard, release),
        so building and swapping happens under a lock.
        """
        cfg = self.cfg.snapshot()
        plugin = OUTPUTS.get(cfg["output"], OUTPUTS["udp"])
        key = plugin.key(cfg)
        with self.output_lock:
            if self.out is not None and self.out_key == key:
                return self.out
            if self.out is not None:
                self.out.close()
            try:
                self.out = plugin.from_config(cfg)
                self.errors["output"] = None
            except OSError as exc:
                # A missing cable must not stop the lights; fall back and say so.
                self.out = OUTPUTS["udp"].from_config(cfg)
                self.errors["output"] = (
                    f"{plugin.name} unavailable ({exc}) -- falling back to Wi-Fi")
            self.out_key = key
            self.output_name = self.out.name
            return self.out

    PREVIEW_SWATCHES = 40

    def _set_preview(self, pixels):
        """Downsample to a handful of swatches the web UI can draw cheaply."""
        n = len(pixels)
        if n == 0:
            self._preview = []
            return
        step = max(1, n // self.PREVIEW_SWATCHES)
        picks = pixels[::step][: self.PREVIEW_SWATCHES]
        self._preview = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in picks]

    def preview(self):
        return list(self._preview)

    def _push(self, pixels, hold_s=FRAME_TIMEOUT_S):
        monitor = pixels[:self.cfg.get("led_count")]   # the bed's pixels, if any, follow
        self._set_preview(monitor)
        out = self._output()
        if not out.send(pixels, hold_s):
            self.errors["output"] = f"could not reach the lights: {out.last_error}"
        elif self.errors["output"] and "could not reach" in self.errors["output"]:
            self.errors["output"] = None
        self.follow_mouse(monitor)

    def follow_mouse(self, pixels):
        cfg = self.cfg.snapshot()
        if cfg["mouse_sync"]:
            self.mouse.show(mouse.vivid(mouse.zone_colours(pixels, cfg)))
        else:
            self.mouse.release()

    def _release(self):
        """Let WLED fall back to its own mode: one black frame, short timeout."""
        try:
            self._output().release()
        except OSError as exc:
            self.errors["output"] = f"release failed: {exc}"

    def status(self):
        return {
            "running": self.running,
            "measured_fps": round(self.fps, 1),
            "output_fps": round(self.output_fps, 1),
            "paused": self.paused,
            "recording": self.recorder is not None,
            "safety": self.stage.safety(),
            "last_recording": self.last_recording,
            "last_error": self.last_error,
            "capture_plugin": self.capture_name,
            "output_plugin": self.output_name,
            "mouse": self.mouse.status(),
        }


def capture_plugin():
    """The first capture source whose dependencies are present."""
    for plugin in CAPTURES.values():
        if plugin.available():
            return plugin
    raise RuntimeError("no capture source available (is mss installed?)")


def screen_locked():
    """True while the Mac shows its lock screen. False when it cannot tell."""
    try:
        import Quartz
    except ImportError:
        return False
    session = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(session.get("CGSSessionScreenIsLocked", False))


class KeepAwake:
    """Holds off idle sleep while sync (or calibration) runs, using macOS's caffeinate.

    By default the display may still sleep (capture then pauses and the
    strip fades); this only keeps the hub itself reachable from phones.
    Calibration instead needs the screen lit for its whole run, so it asks
    for `display=True` as well.
    """

    def __init__(self, process):
        self.process = process

    @classmethod
    def start(cls, display=False):
        tool = shutil.which("caffeinate")
        if tool is None:
            return None
        flags = ["-d", "-i"] if display else ["-i"]
        return cls(subprocess.Popen([tool, *flags, "-w", str(os.getpid())],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
