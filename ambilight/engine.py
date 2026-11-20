"""The sync engine: a capture thread and an output thread, restartable, never fatal."""
import logging
import os
import shutil
import subprocess
import threading
import time

import numpy as np

from . import beat, bed, color, experience, mouse, processing, zones as zonelib
from .plugins import CAPTURES, OUTPUTS, PERMISSION_HINT
from .plugins.output_wled_udp import HOLD_FOREVER
from .recorder import Recorder, new_path
from .safety import Guard, shares
from .smoothing import Quantizer, Smoother, settle_seconds
from .telemetry import Telemetry

# Each packet tells WLED to hold this long; comfortably over one frame interval
# so a dropped packet does not flicker, short enough that a crash self-heals.
FRAME_TIMEOUT_S = 2

RETRY_DISPLAY_S = 2.0   # how often to look for a display that went to sleep
DISPLAY_CHECK_S = 2.0   # how often to notice a monitor being plugged in or removed
LOCK_CHECK_S = 1.0      # how often to ask whether the screen is locked
# Every grab makes macOS composite the whole display (~35 ms of WindowServer
# work on a Retina panel, whatever size is asked for), and grabbing at the
# output rate kept the capture thread busy ~70% of the time. 30 readings a
# second is ample for ambient colour; the output thread still sends at
# cfg["fps"], gliding the strip, bed and mouse between readings.
CAPTURE_FPS_MAX = 30
# Streaming services' DRM hides their video from screen capture, so a full-screen
# film captures as exact black for as long as it plays. A dark scene keeps some
# light somewhere and a fade to black passes within a couple of seconds, so only
# exact black that lasts this long is taken for protected video.
PROTECTED_HOLD_S = 2.5
# What the strip shows meanwhile: the lamp's warm white (#ffb46b) at a soft level,
# graded, limited and smoothed like any frame, so it eases in and out.
PROTECTED_GLOW = np.array([1.0, 0.706, 0.42], np.float32) * 0.55


# Room Studio eases in from dark over this long rather than snapping on.
ROOM_FADE_IN_S = 1.0
# Room scenes go through the person's own LED calibration (strip balance, LED gamma,
# black level, hue balance), but not the screen-only stages: the palette is the look.
ROOM_NEUTRAL = {"contrast": 1.0, "adaptive": 0.0, "dark_boost": 0.0, "white_kelvin": 6500}


def beat_on(cfg):
    """True when Bounce or Shake is on and Calm is off."""
    return not cfg.get("calm") and (cfg.get("bass_bounce", 0.0) > 0 or cfg.get("bass_shake", 0.0) > 0)


def wants_sound(cfg):
    """True when screen sync uses the sound: a beat effect, or Follow the sound. Never under Calm."""
    return beat_on(cfg) or experience.sync_wants_sound(cfg)

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
        # Settings are in screen pixels; the frame may arrive scaled down.
        scale = capture.scale
        return processing.slice_edges(frame, rect, max(1, round(cfg["band_height"] * scale)),
                                      round(cfg["edge_skip"] * scale))


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
        self.meter = None       # the engine's BassMeter, when the beat effects can hear the sound
        self.sound = experience.SoundStage()
        self.trippy = experience.TrippyStage()
        self.room = experience.RoomStage()
        self.room_since = None  # when Room Studio took the strip; None while the screen drives it

    @staticmethod
    def centre(cfg):
        """The LED in the middle of the screen's bottom edge, where beats start."""
        if cfg["layout"] == "frame":
            return cfg["edge_bottom"] / 2.0
        return (cfg["left_led"] + cfg["right_led"]) / 2.0

    def on_beat(self, levels, now, cfg):
        """The last bass beat's bounce and shake, before zones and the safety guard."""
        age, strength = now - self.meter.last_beat, self.meter.strength
        ring = cfg["layout"] == "frame"
        levels = beat.bounce(levels, self.centre(cfg), age, strength, cfg.get("bass_bounce", 0.0))
        return beat.shake(levels, age, strength, cfg.get("bass_shake", 0.0), ring=ring)

    def on_trippy(self, levels, now, cfg):
        """The Trippy treatment; with the sound on, the last beat's kick travels through it."""
        kick = None
        if self.meter is not None:
            kick = (now - self.meter.last_beat, self.meter.strength)
        count = max(1, len(levels) - 1)
        return self.trippy.apply(levels, now, cfg, self.features(now), kick, self.centre(cfg) / count,
                                 ring=cfg["layout"] == "frame")

    def set_target(self, target, now):
        with self.lock:
            self.smoother.set_target(target, now)
            self.last_target = now

    def features(self, now):
        return self.meter.features(now) if self.meter is not None else beat.neutral_features()

    def frame(self, now, cfg):
        """Bytes to send now, or None before the first target."""
        with self.lock:
            if self.room_since is not None:
                return self.room_frame(now, cfg)
            shown = self.smoother.step(now, settle_seconds(cfg["blend"]), cfg.get("adaptive_pace", 0.0))
            if shown is None:
                return None
            # A frozen or lost capture must not hold the last bright frame on
            # the wall indefinitely.
            age = now - self.last_target
            fade = max(0.0, 1.0 - (age - self.STALE_S) / self.FADE_S if age > self.STALE_S else 1.0)
            levels = color.to_led(shown, cfg) * fade
            if cfg.get("trippy"):
                levels = self.on_trippy(levels, now, cfg)
            else:
                levels = self.sound.apply(levels, now, cfg, self.features(now))
            if self.meter is not None and beat_on(cfg):
                levels = self.on_beat(levels, now, cfg)
            return self.finish(levels, now, cfg, self.smoother.target, fade)

    def room_frame(self, now, cfg):
        """Room Studio: the scene's colours for every LED, through the same calibration, zones and guard."""
        scene = experience.scene_for(cfg)
        perceptual = self.room.render(scene, cfg["led_count"], now, cfg, self.features(now))
        fade = min(1.0, max(0.0, now - self.room_since) / ROOM_FADE_IN_S)
        levels = color.to_led(perceptual, {**cfg, **ROOM_NEUTRAL}) * (experience.room_level(scene, cfg) * fade)
        return self.finish(levels, now, cfg, perceptual, fade)

    def finish(self, levels, now, cfg, colours, fade):
        """Zones, brightness, the safety guard and quantizing: the one way out for every frame."""
        # Zones paint over the sync layer after smoothing, so a solid zone
        # is not dragged toward screen colour.
        composed = zonelib.composite(levels, cfg["zones"], cfg["led_count"])
        safe = self.guard.apply(composed * cfg["brightness"], now, {**cfg, **shares(cfg)[0]})
        pixels = self.quantizer.apply(safe)
        if not bed.streams(cfg):
            return pixels
        # Board LEDs between the hub's and the bed's (a monitor strip longer than its frame) stay dark.
        gap = np.zeros((bed.first_led(cfg) - len(pixels), 3), dtype=np.uint8)
        return np.concatenate([pixels, gap, self.bed.frame(colours, now, cfg, fade)])

    def enter_room(self, now):
        with self.lock:
            self.room_since = now

    def leave_room(self):
        with self.lock:
            self.room_since = None

    def safety(self):
        with self.lock:
            return self.guard.state()


class _Pipeline:
    """Screen in, per-LED target colours out. Owns the mapping cache."""

    def __init__(self, stage=None, telemetry=None):
        self._key = None
        self._mapping = None
        self._fade = None
        self.stage = stage or OutputStage()
        self.telemetry = telemetry or Telemetry()

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
        with self.telemetry.stage("process"):
            linear = processing.spread(
                processing.zone_colors(band, self._mapping, cfg["colour_weight"]), cfg["spread"])
        with self.telemetry.stage("colour"):
            graded = color.grade(linear, cfg)
        return linear, processing.extend_edges(graded, *self._fade)

    def measure_edges(self, edges, cfg):
        """Four screen edges -> (linear averages, target), for a strip round the monitor."""
        counts = (cfg["edge_bottom"], cfg["edge_right"], cfg["edge_top"], cfg["edge_left"])
        with self.telemetry.stage("process"):
            share = processing.box_coverage(
                counts, cfg["led_pitch_mm"], cfg["screen_mm_w"], cfg["screen_mm_h"],
                (cfg["box_mm_w"], cfg["box_mm_h"]))
            linear = processing.spread(
                processing.frame_colors(edges, counts, share, cfg["colour_weight"]),
                cfg["spread"], ring=True)
        target = np.zeros((cfg["led_count"], 3), dtype=np.float32)
        used = min(len(linear), len(target))
        with self.telemetry.stage("colour"):
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

    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        now = clock()
        self._next = now
        self._window_start = now
        self._frames = 0
        self.fps = 0.0
        self.missed = 0     # frames skipped by resyncing, never sent

    def tick(self, target_fps, stop):
        self._frames += 1
        now = self._clock()
        elapsed = now - self._window_start
        if elapsed >= self.REPORT_EVERY:
            self.fps = self._frames / elapsed
            self._frames = 0
            self._window_start = now

        # Schedule from the target grid, not from "now", so one slow frame does
        # not permanently shift the cadence.
        interval = 1.0 / max(1, target_fps)
        self._next += interval
        sleep = self._next - self._clock()
        if sleep > 0:
            stop.wait(sleep)
        elif sleep < -self.RESYNC_AFTER:
            self.missed += int(-sleep / interval)
            self._next = self._clock()
        return self.fps


class _BlackFrameWatchdog:
    """Tells missing Screen Recording permission apart from a dark scene.

    A permission failure is black from the very first frame and never recovers.
    A dark scene follows frames that did have light, so it must not warn.
    """

    LIMIT = 30
    MESSAGE = ("captured frames are all black -- if a protected film or show is playing full screen, that is "
               f"its copy protection and the strip glows warm meanwhile; otherwise {PERMISSION_HINT}")

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


class _ProtectedVideo:
    """Tells protected video apart from a dark scene: exact black for longer than a fade lasts."""

    def __init__(self, hold_s=None):
        self.hold_s = PROTECTED_HOLD_S if hold_s is None else hold_s
        self._since = None

    def update(self, black, now):
        """True while the capture has been exactly black for hold_s; any light ends it."""
        if not black:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        return now - self._since >= self.hold_s


def exactly_black(band):
    """True when no pixel has any light: sampled rows first, the whole band only if those are dark."""
    return band[::processing.ROW_SAMPLES].max() == 0 and band.max() == 0


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
        # "screen": the screen drives the strip. "room": a Room Studio scene does, and nothing is captured.
        self.mode = "screen"
        # True while protected video blacks out the capture and the strip glows warm instead.
        self.protected = False
        # Set by the setup wizard to paint a fixed pattern instead of the screen.
        self._override = None
        # Last frame actually sent, downsampled for the UI's live preview.
        self._preview = []
        # Smoothing state outlives a capture restart, so a display waking up
        # glides back in instead of snapping.
        self.stage = OutputStage()
        # Bass beats from the sound, heard only while Bounce or Shake is on (beat.py).
        self.meter = self.stage.meter = beat.BassMeter()
        self.emitter = None
        self.output_fps = 0.0
        self.out = None
        self.out_key = None
        self.recorder = None
        self.last_recording = None
        self.awake = None
        self.mouse = mouse.LightsyncMouse(mouse.load_hid())
        self.telemetry = Telemetry()

    @property
    def last_error(self):
        return self.errors["capture"] or self.errors["output"]

    # --- lifecycle ---------------------------------------------------------
    def start(self, mode="screen"):
        """Drive the strip from the screen ("screen") or a Room Studio scene ("room").

        Switching mode stops the other's threads first, so Room Studio never
        leaves a screen capture running, and the screen never a scene.
        """
        if mode not in ("screen", "room"):
            raise ValueError("mode is screen or room")
        with self.lifecycle:
            if self.running and self.mode == mode:
                return True
            if self.running:
                self._halt()
            # A fresh event per run: a thread from an earlier run that missed
            # its join can never be revived by clearing a shared flag.
            stop = self.stop_event = threading.Event()
            self.errors = {"capture": None, "output": None}
            self.telemetry.reset()
            self.mode = mode
            if mode == "room":
                self.stage.enter_room(time.perf_counter())
                self.capture_name, self.capture_size, self.paused, self.protected = "none", "", None, False
            else:
                self.stage.leave_room()
            work = self._run if mode == "screen" else self._listen
            self._thread = threading.Thread(target=work, args=(stop,), daemon=True, name=f"{mode}-source")
            self.emitter = threading.Thread(target=self.emit_loop, args=(stop,), daemon=True, name="emit")
            self.running = True
            self._thread.start()
            self.emitter.start()
            if self.awake is None:
                self.awake = KeepAwake.start()
            return True

    def _halt(self):
        """End this run's threads; the caller holds the lifecycle lock."""
        self.stop_event.set()
        for thread in (self._thread, self.emitter):
            if thread is not None:
                thread.join(timeout=2.0)
        self.running = False
        self.fps = 0.0

    def stop(self):
        with self.lifecycle:
            self._halt()
            self.stage.leave_room()
            self.mode = "screen"
            self.mouse.release()
            if self.awake is not None:
                self.awake.stop()
                self.awake = None
            self._release()
            self.fps = self.output_fps = 0.0
            self.protected = False

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
                    lost = self._loop(capture, stop)
                finally:
                    capture.close()
                if lost:
                    # A display that opens but cannot be read would otherwise
                    # be reopened and lost again in a tight, CPU-burning loop.
                    stop.wait(RETRY_DISPLAY_S)
            except Exception as exc:
                log.exception("sync loop failed")
                self.errors["capture"] = f"sync hit an error and is retrying: {exc}"
                stop.wait(RETRY_DISPLAY_S)

    def _listen(self, stop):
        """Room Studio's source thread: it hears the sound while the scene follows it, and nothing else.

        No screen is captured. When the scene's Audio Reactivity is off (or Calm
        is on) the sound stream is closed too.
        """
        while not stop.is_set():
            if not experience.room_wants_sound(self.cfg.snapshot()):
                self.capture_name = "none"
                stop.wait(0.5)
                continue
            ears = self._open_ears()
            if ears is None:
                stop.wait(RETRY_DISPLAY_S)
                continue
            try:
                while not stop.is_set() and experience.room_wants_sound(self.cfg.snapshot()):
                    stop.wait(0.5)
            finally:
                ears.close()
                self.capture_name = "none"

    def _open_ears(self):
        """A stream of the Mac's sound alone, or None, saying why; the scene carries on without it."""
        failure = None
        for plugin in capture_plugins():
            if not hasattr(plugin, "open_sound"):
                continue
            candidate = plugin()
            candidate.listener = self.meter.feed
            try:
                candidate.open_sound(resolve_display(self.cfg.get("monitor_index"), plugin.display_count()))
            except Exception as exc:
                log.warning("could not hear the sound with %s: %s", plugin.name, exc)
                failure = exc
                continue
            self.capture_name = f"{plugin.name} (sound only)"
            self.errors["capture"] = None
            return candidate
        self.errors["capture"] = (f"the scene cannot hear the sound ({failure or 'no capture source can'}); "
                                  "it carries on without it")
        return None

    def _open_capture(self):
        """Start the first capture source that works. Returns None and records why on failure.

        A missing display is missing for every source, so that stops the search.
        Anything else (ScreenCaptureKit not answering, say) falls through to the
        next source, so the lights keep following the screen.
        """
        capture, failure = None, None
        for plugin in capture_plugins():
            candidate = plugin()
            candidate.listener = self.meter.feed if getattr(plugin, "hears", False) and wants_sound(self.cfg.snapshot()) else None
            try:
                width, height = candidate.open(
                    resolve_display(self.cfg.get("monitor_index"), plugin.display_count()),
                    self.cfg.get("band_height"))
            except IndexError as exc:
                failure = exc
                break
            except Exception as exc:
                log.warning("capture %s failed to start, trying the next: %s", plugin.name, exc)
                failure = exc
                continue
            capture = candidate
            break
        if capture is None:
            self.errors["capture"] = capture_hint(
                failure or RuntimeError("no capture source available (is mss installed?)"))
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
                    with self.telemetry.stage("smooth"):
                        pixels = self.stage.frame(time.perf_counter(), cfg)
                    if pixels is not None:
                        with self.telemetry.stage("send"):
                            sent = self._push(pixels)
                        self.telemetry.sent(sent, time.perf_counter())
            except Exception as exc:
                log.exception("output loop failed")
                self.errors["output"] = f"output hit an error and is retrying: {exc}"
                stop.wait(0.5)
            missed = pacer.missed
            self.output_fps = pacer.tick(cfg["fps"], stop)
            self.telemetry.missed(pacer.missed - missed)

    def _loop(self, capture, stop):
        pipeline = _Pipeline(self.stage, self.telemetry)
        pacer = _Pacer()
        watchdog = _BlackFrameWatchdog()
        protected = _ProtectedVideo()
        plugin = type(capture)
        opened_with = (self.cfg.get("band_height"), self.cfg.get("monitor_index"),
                       resolve_display(self.cfg.get("monitor_index"), plugin.display_count()))
        # The sound is captured only while a beat effect is on; switching one on or off reopens the stream.
        heard_with = getattr(capture, "listener", None) is not None
        next_display_check = time.perf_counter() + DISPLAY_CHECK_S
        next_lock_check = 0.0
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
            if getattr(plugin, "hears", False) and wants_sound(cfg) != heard_with:
                return      # a beat effect went on or off: reopen, hearing the sound only when one is on

            # A locked Mac shows the lock screen, not anything worth mirroring;
            # without fresh targets the output stage fades the strip out. Asking
            # is a round trip to WindowServer, so it is not done every frame.
            if time.perf_counter() >= next_lock_check:
                next_lock_check = time.perf_counter() + LOCK_CHECK_S
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

            grabbed_at = time.perf_counter()
            try:
                band = self._grab(
                    capture, stop, (lambda: frame_source.grab(capture, cfg)) if wrapped else None)
            except CaptureLost as lost:
                self.errors["capture"] = str(lost)
                return True     # _run reopens the capture once a display is back
            if band is None:
                continue
            self.telemetry.record("capture", time.perf_counter() - grabbed_at)

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
            black = all(exactly_black(e) for e in edges.values()) if wrapped else exactly_black(band)
            self.protected = protected.update(black, now)
            if self.protected:
                target = np.tile(PROTECTED_GLOW, (len(target), 1))
            self.stage.set_target(target, now)
            self.telemetry.captured(grabbed_at)
            self.record(now, linear)

            self.fps = pacer.tick(min(cfg["fps"], CAPTURE_FPS_MAX), stop)

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
        """Send one frame; True when the output took it."""
        monitor = pixels[:self.cfg.get("led_count")]   # the bed's pixels, if any, follow
        self._set_preview(monitor)
        out = self._output()
        sent = out.send(pixels, hold_s)
        if not sent:
            self.errors["output"] = f"could not reach the lights: {out.last_error}"
        elif self.errors["output"] and "could not reach" in self.errors["output"]:
            self.errors["output"] = None
        self.follow_mouse(monitor)
        return sent

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
        now = time.perf_counter()
        return {
            "running": self.running,
            # What drives the strip while it runs: the screen, or a Room Studio scene.
            "source": self.mode if self.running else None,
            "room_name": experience.scene_for(self.cfg.snapshot())["name"],
            "audio": {**self.meter.status(now),
                      "wanted": self.running and (experience.room_wants_sound(self.cfg.snapshot())
                                                  if self.mode == "room" else wants_sound(self.cfg.snapshot()))},
            "measured_fps": round(self.fps, 1),
            "output_fps": round(self.output_fps, 1),
            "paused": self.paused,
            "protected_video": self.protected,
            "recording": self.recorder is not None,
            "safety": self.stage.safety(),
            "last_recording": self.last_recording,
            "last_error": self.last_error,
            "capture_plugin": self.capture_name,
            "output_plugin": self.output_name,
            "mouse": self.mouse.status(),
            "telemetry": self.telemetry.snapshot(),
        }


def capture_plugins():
    """Capture sources whose dependencies are present, most preferred first."""
    return [plugin for plugin in CAPTURES.values() if plugin.available()]


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
