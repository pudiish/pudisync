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
BLACK_FRAME_LIMIT = 30  # consecutive dark frames before we suspect permissions


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
        self._smoothed = None
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
            self._smoothed = None
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
        capture = None
        try:
            capture = MSSCapture()
            width, height = capture.open(
                self.cfg.get("monitor_index"), self.cfg.get("band_height"))
            self.capture_name = capture.name
            self.capture_size = f"{width}x{height}"
        except Exception as exc:
            self.last_error = f"capture failed: {exc}"
            self.running = False
            if capture:
                capture.close()
            return

        mapping = fade = None
        shape_key = None
        band_height = self.cfg.get("band_height")
        frames = 0
        dark_streak = 0
        ever_lit = False
        window_start = time.perf_counter()
        next_frame = window_start

        while not self._stop.is_set():
            cfg = self.cfg.snapshot()

            if cfg["band_height"] != band_height:
                try:
                    capture.close()
                    capture = MSSCapture()
                    width, height = capture.open(cfg["monitor_index"], cfg["band_height"])
                    self.capture_size = f"{width}x{height}"
                    band_height = cfg["band_height"]
                    shape_key = None
                except Exception as exc:
                    self.last_error = f"recapture failed: {exc}"
                    break

            try:
                band = capture.grab()
            except Exception as exc:
                self.last_error = f"grab failed: {exc}"
                time.sleep(0.5)
                continue
            if band is None:
                self.last_error = "capture returned no data"
                time.sleep(0.5)
                continue

            # Retina scaling means the captured width differs from the reported
            # monitor width, so map against what we actually got.
            actual_width = band.shape[1]
            key = (actual_width, cfg["led_count"], cfg["left_led"], cfg["right_led"], cfg["fade_leds"])
            if key != shape_key:
                mapping = processing.build_mapping(
                    cfg["led_count"], cfg["left_led"], cfg["right_led"], actual_width
                )
                fade = processing.build_fade(mapping, cfg["fade_leds"])
                shape_key = key
                self._smoothed = None

            if band.max() == 0:
                dark_streak += 1
                # Only a never-lit stream means missing permission. A dark movie
                # scene follows frames that did have light, so it must not warn.
                if dark_streak == BLACK_FRAME_LIMIT and not ever_lit:
                    self.last_error = (
                        "captured frames are all black -- grant Screen Recording "
                        "permission to your terminal in System Settings > Privacy "
                        "& Security > Screen Recording, then restart."
                    )
            else:
                dark_streak = 0
                ever_lit = True
                if self.last_error and "Screen Recording" in self.last_error:
                    self.last_error = None

            if self._override is None:
                frame = processing.zone_colors(band, mapping)
                frame = processing.saturate(frame, cfg["saturation"])
                frame = processing.extend_edges(frame, *fade)
                blend = cfg["blend"]
                if self._smoothed is None or self._smoothed.shape != frame.shape:
                    self._smoothed = frame
                else:
                    self._smoothed += (frame - self._smoothed) * blend
                # Zones paint over the smoothed sync frame; smoothing stays on the
                # sync layer so a solid zone is not dragged toward screen colour.
                composed = zonelib.composite(
                    self._smoothed, cfg["zones"], cfg["led_count"])
                self._push(processing.finalize(composed, cfg["brightness"]))

            frames += 1
            now = time.perf_counter()
            if now - window_start >= 0.5:
                self.fps = frames / (now - window_start)
                frames, window_start = 0, now

            # Time-based pacing: schedule from the target grid, not from "now",
            # so a slow frame does not permanently shift the cadence.
            next_frame += 1.0 / max(1, cfg["fps"])
            sleep = next_frame - time.perf_counter()
            if sleep > 0:
                self._stop.wait(sleep)
            elif sleep < -0.25:
                next_frame = time.perf_counter()  # fell far behind; resync

        capture.close()
        self.running = False

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
