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
        band = min(band_height, mon["height"])
        self._region = {
            "left": mon["left"],
            "top": mon["top"] + mon["height"] - band,
            "width": mon["width"],
            "height": band,
        }
        probe = self.grab()
        if probe is None:
            raise RuntimeError("capture returned no data")
        return probe.shape[1], probe.shape[0]

    def grab(self):
        shot = self._sct.grab(self._region)
        # mss hands back BGRA; drop alpha and flip to RGB.
        frame = np.frombuffer(shot.rgb, dtype=np.uint8)
        return frame.reshape(shot.height, shot.width, 3)

    def close(self):
        if self._sct is not None:
            self._sct.close()
            self._sct = None
