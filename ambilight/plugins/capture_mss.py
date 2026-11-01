"""Baseline capture plugin: mss. Portable, fast enough for 60fps at a thin band."""
import numpy as np

from . import CapturePlugin


class MSSCapture(CapturePlugin):
    name = "mss"

    @staticmethod
    def available():
        try:
            import mss  # noqa: F401
        except ImportError:
            return False
        return True

    def __init__(self):
        self._sct = None
        self._region = None
        self._mon = None
        self._band = 0
        self._inset = 0

    def open(self, monitor_index, band_height):
        import mss

        # mss objects are bound to the thread that created them.
        self._sct = mss.mss()
        monitors = self._sct.monitors
        if monitor_index >= len(monitors):
            raise IndexError(
                f"monitor {monitor_index} not found; {len(monitors) - 1} attached"
            )
        mon = monitors[monitor_index]
        self._mon = mon
        self._band = min(band_height, mon["height"])
        self._inset = 0
        self._region = self._band_region()
        probe = self.grab()
        if probe is None:
            raise RuntimeError("capture returned no data")
        return probe.shape[1], probe.shape[0]

    def _band_region(self):
        mon = self._mon
        return {
            "left": mon["left"],
            "top": mon["top"] + mon["height"] - self._band - self._inset,
            "width": mon["width"],
            "height": self._band,
        }

    def set_inset(self, rows):
        """Lift the band off the screen bottom, to clear letterbox bars."""
        rows = max(0, min(int(rows), self._mon["height"] - self._band))
        if rows != self._inset:
            self._inset = rows
            self._region = self._band_region()

    def grab_search(self, rows):
        """A taller strip from the screen bottom, for finding the picture edge."""
        mon = self._mon
        rows = min(rows, mon["height"])
        shot = self._sct.grab({"left": mon["left"],
                               "top": mon["top"] + mon["height"] - rows,
                               "width": mon["width"], "height": rows})
        return np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)

    def grab_full(self):
        """The whole monitor in one grab.

        One grab costs about the same whatever its size, so cutting the four
        edges out of this is roughly twice as fast as grabbing each separately.
        Returns an RGB view of mss's raw BGRA buffer: shot.rgb would convert
        every pixel of the screen (~10ms) when only the thin edges are used.
        """
        mon = self._mon
        shot = self._sct.grab({k: mon[k] for k in ("left", "top", "width", "height")})
        bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return bgra[..., 2::-1]

    def grab(self):
        shot = self._sct.grab(self._region)
        # mss hands back BGRA; drop alpha and flip to RGB.
        frame = np.frombuffer(shot.rgb, dtype=np.uint8)
        return frame.reshape(shot.height, shot.width, 3)

    def close(self):
        if self._sct is not None:
            self._sct.close()
            self._sct = None
