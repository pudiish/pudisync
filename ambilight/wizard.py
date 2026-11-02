"""Setup wizard state: LED-count binary search, edge marker, test gradient."""
import threading

import numpy as np


class Wizard:
    """Paints fixed patterns through the engine's override channel."""

    def __init__(self, config, engine):
        self.cfg = config
        self.engine = engine
        self._lock = threading.Lock()
        self._lo = 0
        self._hi = 0
        self._k = 0
        self._active = False

    # --- LED count ---------------------------------------------------------
    def count_start(self, wled_max):
        """Begin bisecting. wled_max is WLED's configured count, our upper bound."""
        with self._lock:
            self._hi = max(1, int(wled_max))
            self._lo = 1
            self._active = True
            self._k = self._hi  # start by lighting everything WLED knows about
        return self._probe_payload(
            "All LEDs WLED knows about are lit. Is the very last LED on the strip lit?")

    def count_answer(self, lit):
        """Narrow the range. 'lit' means the strip's physical end is illuminated."""
        with self._lock:
            if not self._active:
                raise RuntimeError("count search not started")
            if lit:
                # k reaches the end, so the strip is at most k.
                self._hi = self._k
            else:
                # k stops short, so the strip is longer than k.
                self._lo = self._k + 1
            if self._lo >= self._hi:
                found = self._hi
                self._active = False
                self.cfg.update({"led_count": found,
                                 "right_led": min(found - 1, self.cfg.get("right_led"))})
                self.engine.clear_override()
                return {"k": found, "done": True,
                        "message": f"LED count set to {found}."}
            self._k = (self._lo + self._hi) // 2
        return self._probe_payload("Is the very last LED on the strip lit?")

    def _probe_payload(self, message):
        with self._lock:
            k, lo, hi = self._k, self._lo, self._hi
        self._paint_probe(k)
        extra = ""
        if k >= hi and lo == 1:
            extra = (" If it is not lit, your strip is longer than WLED's configured "
                     "count — raise the LED count in WLED's LED Preferences first.")
        return {"k": k, "lo": lo, "hi": hi, "done": False, "message": message + extra}

    def _paint_probe(self, k):
        """Light the first k LEDs dim, with the last one bright as the marker."""
        n = max(k, self.cfg.get("led_count"))
        px = np.zeros((n, 3), dtype=np.uint8)
        px[:k] = (40, 40, 40)
        if k:
            px[k - 1] = (255, 255, 255)
        self.engine.set_override(px)

    # --- edge marker -------------------------------------------------------
    def marker(self, index):
        n = self.cfg.get("led_count")
        index = max(0, min(n - 1, int(index)))
        px = np.zeros((n, 3), dtype=np.uint8)
        px[index] = (255, 180, 0)
        self.engine.set_override(px)
        return {"index": index}

    # --- test gradient -----------------------------------------------------
    # Frame test pattern: one colour per side, so a wrong count or start corner
    # shows up at once (bottom, right, top, left).
    SIDE_COLOURS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    START_MARK = (255, 160, 0)

    def gradient(self):
        """Paint the layout test pattern: per-side colours, or the strip ramp."""
        cfg = self.cfg.snapshot()
        if cfg["layout"] == "frame":
            return self.frame_pattern(cfg)
        return self.strip_gradient(cfg)

    def frame_pattern(self, cfg):
        """Each side its own colour, brightening along the strip's direction."""
        counts = (cfg["edge_bottom"], cfg["edge_right"], cfg["edge_top"], cfg["edge_left"])
        px = np.zeros((cfg["led_count"], 3), dtype=np.float32)
        start = 0
        for count, colour in zip(counts, self.SIDE_COLOURS):
            stop = min(start + count, len(px))
            ramp = np.linspace(0.25, 1.0, max(stop - start, 1), dtype=np.float32)[:, None]
            px[start:stop] = np.array(colour, np.float32) * ramp[:stop - start]
            start = stop
        if len(px):
            px[0] = self.START_MARK
        self.engine.set_override(np.clip(px * cfg["brightness"], 0, 255).astype(np.uint8))
        return {"sides": list(counts)}

    def strip_gradient(self, cfg):
        """Red at the left screen edge, blue at the right, faded beyond."""
        from . import processing

        n = cfg["led_count"]
        mapping = processing.build_mapping(n, cfg["left_led"], cfg["right_led"], 1024)
        span = mapping["zone_hi"] - mapping["zone_lo"] + 1
        # Build the ramp in screen order, then let the mapping's own direction
        # handling place it, so a reversed strip still shows red on the left.
        t = np.linspace(0.0, 1.0, span, dtype=np.float32)[:, None]
        screen = np.array([255, 0, 0], np.float32) * (1 - t) + np.array([0, 0, 255], np.float32) * t
        if cfg["left_led"] > cfg["right_led"]:
            screen = screen[::-1]
        zone = screen
        low, high = processing.build_fade(mapping, cfg["fade_leds"])
        full = processing.extend_edges(zone, low, high)
        self.engine.set_override(processing.finalize(full, cfg["brightness"]))
        return {"span": int(span)}

    def stop(self):
        with self._lock:
            self._active = False
        self.engine.clear_override()
        if not self.engine.running:
            self.engine._release()
        return {"stopped": True}
