"""The sync engine: one thread, time-paced, restartable, never fatal."""
import threading
import time

import numpy as np

from . import processing, zones as zonelib
from .plugins.capture_mss import MSSCapture
from .plugins.output_wled_udp import WLEDUDPOutput

# Each packet tells WLED to hold this long; comfortably over one frame interval
# so a dropped packet does not flicker, short enough that a crash self-heals.
FRAME_TIMEOUT_S = 2

RETRY_DISPLAY_S = 2.0   # how often to look for a display that went to sleep

# Letterbox search: a taller strip is grabbed only a few times a second, so the
# per-frame capture stays as thin (and as fast) as before.
SEARCH_ROWS = 500
SEARCH_EVERY_S = 0.25


class CaptureLost(Exception):
    """The screen we were capturing disappeared -- sleep, lid close, unplug."""


def capture_hint(exc):
    """Turn a capture failure into something the user can act on."""
    text = str(exc)
    if "not found" in text or "0 attached" in text:
        return ("no display to capture -- the screen is asleep or the lid is "
                "closed. Sync resumes on its own when it wakes.")
    return f"capture failed: {text}"


class _PictureTracker:
    """Smooths picture-edge detections so a dark scene cannot make the lights jump.

    A new position is adopted only after two detections in a row agree.
    """

    TOLERANCE = 16      # pixels of jitter that still count as "the same edge"

    def __init__(self, detect=processing.find_picture,
                 fallback=lambda img: (0, 0, img.shape[1])):
        self._detect = detect
        self._fallback = fallback
        self.applied = None
        self._pending = None

    @classmethod
    def _same(cls, a, b):
        return all(abs(x - y) <= cls.TOLERANCE for x, y in zip(a, b))

    def update(self, image):
        """Returns the edges to sample with."""
        seen = self._detect(image)
        if self.applied is None:
            self.applied = self._fallback(image)
        if self._same(seen, self.applied):
            self._pending = None
        elif self._pending is not None and self._same(seen, self._pending):
            self.applied, self._pending = seen, None
        else:
            self._pending = seen
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


class _Pipeline:
    """Screen band in, LED bytes out. Owns the mapping cache and the smoothing."""

    def __init__(self, config):
        self.cfg = config
        self._key = None
        self._mapping = None
        self._fade = None
        self._smoothed = None

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
        self._smoothed = None

    def render(self, band, cfg):
        # Retina scaling makes the captured width differ from the reported
        # monitor width, so map against what we actually got.
        self._ensure(band.shape[1], cfg)

        frame = processing.zone_colors(band, self._mapping)
        frame = processing.saturate(frame, cfg["saturation"])
        frame = processing.extend_edges(frame, *self._fade)
        return self._compose(frame, cfg)

    def render_edges(self, edges, cfg):
        """Four screen edges in, LED bytes out, for a strip wrapped round the monitor."""
        counts = (cfg["edge_bottom"], cfg["edge_right"], cfg["edge_top"], cfg["edge_left"])
        pitch, wide, tall = cfg["led_pitch_mm"], cfg["screen_mm_w"], cfg["screen_mm_h"]
        share = (processing.coverage(counts[0], pitch, wide),
                 processing.coverage(counts[1], pitch, tall),
                 processing.coverage(counts[2], pitch, wide),
                 processing.coverage(counts[3], pitch, tall))
        wrapped = processing.saturate(
            processing.frame_colors(edges, counts, share), cfg["saturation"])
        frame = np.zeros((cfg["led_count"], 3), dtype=np.float32)
        used = min(len(wrapped), len(frame))
        frame[:used] = wrapped[:used]
        return self._compose(frame, cfg)

    def _compose(self, frame, cfg):
        if self._smoothed is None or self._smoothed.shape != frame.shape:
            self._smoothed = frame
        else:
            self._smoothed += (frame - self._smoothed) * cfg["blend"]

        # Zones paint over the smoothed sync frame; smoothing stays on the sync
        # layer so a solid zone is not dragged toward screen colour.
        composed = zonelib.composite(self._smoothed, cfg["zones"], cfg["led_count"])
        return processing.finalize(composed, cfg["brightness"])


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
               "permission to your terminal in System Settings > Privacy & "
               "Security > Screen Recording, then restart.")

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
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.running = False
        self.last_error = None
        self.fps = 0.0
        self.capture_name = "none"
        self.output_name = "none"
        self.capture_size = ""
        # Set by the setup wizard to paint a fixed pattern instead of the screen.
        self._override = None
        # Last frame actually sent, downsampled for the UI's live preview.
        self._preview = []

    # --- lifecycle ---------------------------------------------------------
    def start(self):
        with self._lock:
            if self.running:
                return True
            self._stop.clear()
            self.last_error = None
            self._thread = threading.Thread(target=self._run, daemon=True, name="sync")
            self.running = True
            self._thread.start()
            return True

    def stop(self):
        with self._lock:
            thread, self.running = self._thread, False
        self._stop.set()
        if thread is not None:
            thread.join(timeout=2.0)
        self._release()
        self.fps = 0.0

    def set_override(self, pixels):
        """Paint a literal frame (wizard markers, gradients) and pause screen sync."""
        self._override = pixels
        if pixels is not None:
            self._push(pixels)

    def clear_override(self):
        self._override = None

    # --- the frame path ----------------------------------------------------
    def _run(self):
        """Keep sync alive across display sleep, lid close and monitor changes.

        A sleeping display makes the monitor disappear entirely, so opening the
        capture fails. That is temporary, not fatal: wait and retry rather than
        ending the thread, or sync would never come back without a restart.
        """
        try:
            while not self._stop.is_set():
                capture = self._open_capture()
                if capture is None:
                    if self._stop.wait(RETRY_DISPLAY_S):
                        return
                    continue
                try:
                    self._loop(capture)
                finally:
                    capture.close()
        finally:
            self.running = False

    def _open_capture(self):
        """Start the capture plugin. Returns None and records why on failure."""
        try:
            capture = MSSCapture()
            width, height = capture.open(
                self.cfg.get("monitor_index"), self.cfg.get("band_height"))
        except Exception as exc:
            self.last_error = capture_hint(exc)
            return None
        self.capture_name = capture.name
        self.capture_size = f"{width}x{height}"
        if self.last_error and "display" in self.last_error:
            self.last_error = None
        return capture

    def _loop(self, capture):
        pipeline = _Pipeline(self.cfg)
        pacer = _Pacer()
        watchdog = _BlackFrameWatchdog()
        band_height = self.cfg.get("band_height")
        tracker = _PictureTracker()
        next_search = 0.0
        frame_source = _FrameSource()

        while not self._stop.is_set():
            cfg = self.cfg.snapshot()

            if cfg["band_height"] != band_height:
                capture = self._reopen(capture, cfg)
                if capture is None:
                    return
                band_height = cfg["band_height"]
                pipeline.invalidate()

            crop = (0, None)
            wrapped = cfg["layout"] == "frame"
            if not wrapped and cfg["letterbox"]:
                if time.perf_counter() >= next_search:
                    next_search = time.perf_counter() + SEARCH_EVERY_S
                    try:
                        strip = capture.grab_search(SEARCH_ROWS)
                    except Exception:
                        strip = None        # the band grab below reports a lost display
                    if strip is not None:
                        capture.set_inset(tracker.update(strip)[0])
                if tracker.applied is not None:
                    crop = tracker.applied[1:]
            elif not wrapped:
                capture.set_inset(0)

            try:
                band = self._grab(
                    capture, (lambda: frame_source.grab(capture, cfg)) if wrapped else None)
            except CaptureLost as lost:
                self.last_error = str(lost)
                return      # _run reopens the capture once a display is back
            if band is None:
                continue

            if wrapped:
                edges, band = band, band["bottom"]
            elif crop != (0, None):
                band = band[:, crop[0]:crop[1]]

            message = watchdog.check(band)
            if message is not None:
                self.last_error = message or None

            if self._override is None:
                self._push(pipeline.render_edges(edges, cfg) if wrapped
                           else pipeline.render(band, cfg))

            self.fps = pacer.tick(cfg["fps"], self._stop)

    def _reopen(self, capture, cfg):
        """Rebuild the capture after a band-height change."""
        try:
            capture.close()
            fresh = MSSCapture()
            width, height = fresh.open(cfg["monitor_index"], cfg["band_height"])
        except Exception as exc:
            self.last_error = f"recapture failed: {exc}"
            return None
        self.capture_size = f"{width}x{height}"
        return fresh

    def _grab(self, capture, grab=None):
        """One frame, or None. Raises CaptureLost when the display went away."""
        try:
            band = (grab or capture.grab)()
        except Exception as exc:
            # The handle is tied to a monitor that no longer exists, so the
            # capture must be rebuilt rather than retried on a dead handle.
            raise CaptureLost(capture_hint(exc)) from None
        if band is None:
            self.last_error = "capture returned no data"
            self._stop.wait(0.5)
        return band

    # --- output ------------------------------------------------------------
    def _output(self):
        """Return the configured output, rebuilding it when the target changes."""
        cfg = self.cfg.snapshot()
        want = cfg["output"]
        key = ((want, cfg["serial_port"], cfg["serial_baud"]) if want == "serial"
               else (want, cfg["wled_ip"], cfg["wled_port"]))
        out = getattr(self, "_out", None)
        if out is not None and getattr(self, "_out_key", None) == key:
            return out
        if out is not None:
            out.close()

        if want == "serial" and cfg["serial_port"]:
            try:
                from .plugins.output_serial import SerialOutput

                out = SerialOutput(cfg["serial_port"], cfg["serial_baud"])
            except Exception as exc:
                # A missing cable must not stop the lights; fall back and say so.
                self.last_error = (
                    f"serial port {cfg['serial_port']} unavailable ({exc}) "
                    "-- falling back to Wi-Fi")
                out = WLEDUDPOutput(cfg["wled_ip"], cfg["wled_port"])
        else:
            out = WLEDUDPOutput(cfg["wled_ip"], cfg["wled_port"])

        self._out = out
        self._out_key = key
        self.output_name = out.name
        # A previous transport's complaint does not apply to the new one.
        if self.last_error and "serial port" in self.last_error and want != "serial":
            self.last_error = None
        return out

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

    def _push(self, pixels):
        self._set_preview(pixels)
        out = self._output()
        if not out.send(pixels, FRAME_TIMEOUT_S):
            self.last_error = f"UDP send failed: {out.last_error}"
        elif self.last_error and "UDP send failed" in self.last_error:
            self.last_error = None

    def _release(self):
        """Let WLED fall back to its own mode: one black frame, short timeout."""
        try:
            self._output().release()
        except Exception as exc:
            self.last_error = f"release failed: {exc}"

    def status(self):
        return {
            "running": self.running,
            "measured_fps": round(self.fps, 1),
            "last_error": self.last_error,
            "capture_plugin": self.capture_name,
            "output_plugin": self.output_name,
        }
