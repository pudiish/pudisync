"""Native macOS capture through Quartz (CoreGraphics), via pyobjc.

About 30% faster than mss for a full 2560x1440 frame on the owner's Mac
(~23ms vs ~34ms), which is most of the capture budget. Displays are numbered
like mss numbers them (1 = first active display), so monitor_index keeps its
meaning whichever plugin runs.
"""
import numpy as np

from . import CapturePlugin


class QuartzCapture(CapturePlugin):
    name = "quartz"

    @staticmethod
    def available():
        try:
            import Quartz  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def display_count():
        import Quartz

        return len(Quartz.CGGetActiveDisplayList(16, None, None)[1] or ())

    def __init__(self):
        self.display = None
        self.band = 0
        self.inset = 0

    def open(self, monitor_index, band_height):
        import Quartz

        displays = Quartz.CGGetActiveDisplayList(16, None, None)[1] or ()
        if not 1 <= monitor_index <= len(displays):
            raise IndexError(f"monitor {monitor_index} not found; {len(displays)} attached")
        self.display = displays[monitor_index - 1]
        probe = self.grab_full()
        self.band = min(band_height, probe.shape[0])
        self.inset = 0
        return probe.shape[1], probe.shape[0]

    def grab_full(self):
        """The whole display as an RGB view of Quartz's BGRA buffer (no copy)."""
        import Quartz

        image = Quartz.CGDisplayCreateImage(self.display)
        if image is None:
            raise OSError("monitor not found; the display is asleep or gone")
        width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
        row_bytes = Quartz.CGImageGetBytesPerRow(image)
        data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
        # Rows can be padded past the width, and Retina buffers carry extra
        # bytes after the last row, so read exactly height rows of row_bytes.
        pixels = np.frombuffer(data, dtype=np.uint8, count=height * row_bytes)
        return pixels.reshape(height, row_bytes // 4, 4)[:, :width, 2::-1]

    def grab(self):
        full = self.grab_full()
        bottom = full.shape[0] - self.inset
        return full[bottom - self.band:bottom]

    def grab_search(self, rows):
        full = self.grab_full()
        return full[full.shape[0] - min(rows, full.shape[0]):]

    def set_inset(self, rows):
        self.inset = max(0, int(rows))

    def close(self):
        self.display = None
