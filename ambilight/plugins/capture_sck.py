"""Screen capture through ScreenCaptureKit, macOS's current capture API.

Quartz's CGDisplayCreateImage is obsoleted in the macOS 15 SDK and reads back
the whole Retina frame on every grab (about 24ms on the owner's M3, so capture
topped out near 42fps). A ScreenCaptureKit stream has the GPU scale each frame
down to MAX_WIDTH and delivers it as soon as the screen changes, so a grab is
a small copy instead of a full-screen readback.

The engine gives band heights and search rows in screen pixels, as Quartz
delivers them; `scale` turns them into rows of this plugin's smaller frames.
"""
import logging
import threading

import numpy as np

from . import PERMISSION_HINT, CapturePlugin
from .capture_quartz import QuartzCapture

# The edges are averaged into a few dozen LEDs per side, so more width only
# costs copying. Picture-edge detection still lands within a few screen pixels.
MAX_WIDTH = 1280
FRAME_S = 1 / 60        # the stream's frame cap, and how long a grab waits for a newer frame
OPEN_TIMEOUT_S = 2.0
CLOSE_TIMEOUT_S = 1.0
USER_DECLINED = -3801   # SCStreamErrorUserDeclined: Screen Recording permission is off

log = logging.getLogger(__name__)
_receiver = None


class _Latest:
    """The newest frame the stream delivered, handed from its queue to the capture thread."""

    def __init__(self):
        self._ready = threading.Condition()
        self.buffer = None
        self.count = 0
        self.error = None

    def put(self, buffer):
        with self._ready:
            self.buffer, self.count = buffer, self.count + 1
            self._ready.notify_all()

    def fail(self, error):
        with self._ready:
            self.error = error
            self._ready.notify_all()

    def wait(self, after, timeout):
        """(count, buffer) of a frame newer than `after`, or the newest one once `timeout` passes.

        A still screen sends no new frames, so the last one stays current.
        """
        with self._ready:
            self._ready.wait_for(lambda: self.count != after or self.error is not None, timeout)
            if self.error is not None:
                raise OSError(f"screen capture stopped: {self.error}")
            return self.count, self.buffer


def _receiver_class():
    """The Objective-C object the stream calls back. Built once: class names are process-wide."""
    global _receiver
    if _receiver is None:
        import CoreMedia
        import objc
        import ScreenCaptureKit as SCK
        from Foundation import NSObject

        with_pixels = (SCK.SCFrameStatusComplete, SCK.SCFrameStatusStarted)

        class AmbilightFrameReceiver(NSObject, protocols=[objc.protocolNamed("SCStreamOutput"),
                                                          objc.protocolNamed("SCStreamDelegate")]):
            def stream_didOutputSampleBuffer_ofType_(self, stream, buffer, kind):
                if kind != SCK.SCStreamOutputTypeScreen:
                    return
                # Idle, blank and suspended frames carry no new pixels.
                info = CoreMedia.CMSampleBufferGetSampleAttachmentsArray(buffer, False)
                status = info[0].get(SCK.SCStreamFrameInfoStatus) if info else None
                if status in with_pixels and CoreMedia.CMSampleBufferGetImageBuffer(buffer) is not None:
                    self.latest.put(buffer)

            def stream_didStopWithError_(self, stream, error):
                self.latest.fail(error.localizedDescription())

        _receiver = AmbilightFrameReceiver
    return _receiver


def _explain(error):
    if error.code() == USER_DECLINED:
        return f"Screen Recording permission is off -- {PERMISSION_HINT}"
    return str(error.localizedDescription())


def _await(start, what, timeout=OPEN_TIMEOUT_S):
    """Run one ScreenCaptureKit call that answers through a completion handler.

    The handler runs on one of the framework's own queues, so no run loop is
    needed on this thread.
    """
    done, answer = threading.Event(), []

    def finished(*args):
        answer.extend(args)
        done.set()

    start(finished)
    if not done.wait(timeout):
        raise TimeoutError(f"ScreenCaptureKit did not answer while {what}")
    *results, error = answer
    if error is not None:
        raise OSError(_explain(error))
    return results


class SCKCapture(CapturePlugin):
    name = "screencapturekit"

    @staticmethod
    def available():
        try:
            import CoreMedia  # noqa: F401
            import Quartz  # noqa: F401
            import ScreenCaptureKit
        except ImportError:
            return False
        return getattr(ScreenCaptureKit, "SCStream", None) is not None

    @staticmethod
    def display_count():
        return QuartzCapture.display_count()

    def __init__(self):
        self.scale = 1.0
        self.band = 0
        self.inset = 0
        self.frame_time = None      # when the newest grabbed frame was on screen (perf_counter clock)
        self._stream = None
        self._latest = None
        self._receiver = None
        self._seen = None
        self._bgra = None

    def open(self, monitor_index, band_height):
        import CoreMedia
        import Quartz
        import ScreenCaptureKit as SCK

        displays = Quartz.CGGetActiveDisplayList(16, None, None)[1] or ()
        if not 1 <= monitor_index <= len(displays):
            raise IndexError(f"monitor {monitor_index} not found; {len(displays)} attached")
        wanted = displays[monitor_index - 1]
        (content,) = _await(SCK.SCShareableContent.getShareableContentWithCompletionHandler_,
                            "listing displays")
        display = next((d for d in content.displays() if d.displayID() == wanted), None)
        if display is None:
            raise IndexError(f"monitor {monitor_index} not found; the display is asleep or gone")

        # Quartz delivered the display's full pixel size; scale from that.
        mode = Quartz.CGDisplayCopyDisplayMode(wanted)
        screen_w = Quartz.CGDisplayModeGetPixelWidth(mode)
        screen_h = Quartz.CGDisplayModeGetPixelHeight(mode)
        self.scale = min(1.0, MAX_WIDTH / screen_w)
        width, height = max(1, round(screen_w * self.scale)), max(1, round(screen_h * self.scale))

        config = SCK.SCStreamConfiguration.alloc().init()
        config.setWidth_(width)
        config.setHeight_(height)
        config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, round(1 / FRAME_S)))
        config.setPixelFormat_(Quartz.kCVPixelFormatType_32BGRA)
        config.setShowsCursor_(False)       # Quartz leaves the cursor out too
        config.setQueueDepth_(3)            # one held by the newest frame, two for the stream

        self._latest = _Latest()
        self._receiver = _receiver_class().alloc().init()
        self._receiver.latest = self._latest
        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, self._receiver)
        added, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._receiver, SCK.SCStreamOutputTypeScreen, None, None)
        if not added:
            raise OSError(f"could not attach to the screen stream: {error}")
        try:
            _await(self._stream.startCaptureWithCompletionHandler_, "starting the stream")
            self._seen = None
            frame = self._newest(OPEN_TIMEOUT_S)
        except Exception:
            self.close()
            raise
        self.band = min(round(band_height * self.scale), frame.shape[0])
        self.inset = 0
        return frame.shape[1], frame.shape[0]

    def _newest(self, timeout):
        count, buffer = self._latest.wait(self._seen, timeout)
        if buffer is None:
            raise TimeoutError("the screen stream sent no frame")
        if count != self._seen:
            self._bgra = self._copy(buffer)
            self._seen = count
        return self._bgra[..., 2::-1]

    def grab_full(self):
        """The whole display as an RGB view of a copy of the stream's newest BGRA frame."""
        return self._newest(FRAME_S)

    def _copy(self, buffer):
        import CoreMedia
        import Quartz

        stamp = CoreMedia.CMSampleBufferGetPresentationTimeStamp(buffer)
        self.frame_time = stamp.value / stamp.timescale
        image = CoreMedia.CMSampleBufferGetImageBuffer(buffer)
        Quartz.CVPixelBufferLockBaseAddress(image, Quartz.kCVPixelBufferLock_ReadOnly)
        try:
            width = Quartz.CVPixelBufferGetWidth(image)
            height = Quartz.CVPixelBufferGetHeight(image)
            row_bytes = Quartz.CVPixelBufferGetBytesPerRow(image)
            raw = Quartz.CVPixelBufferGetBaseAddress(image).as_buffer(height * row_bytes)
            # Rows can be padded past the width; the copy must outlive the lock.
            pixels = np.frombuffer(raw, dtype=np.uint8).reshape(height, row_bytes // 4, 4)
            return pixels[:, :width].copy()
        finally:
            Quartz.CVPixelBufferUnlockBaseAddress(image, Quartz.kCVPixelBufferLock_ReadOnly)

    def grab(self):
        full = self.grab_full()
        bottom = full.shape[0] - self.inset
        return full[bottom - self.band:bottom]

    def grab_search(self, rows):
        full = self.grab_full()
        return full[full.shape[0] - min(round(rows * self.scale), full.shape[0]):]

    def set_inset(self, rows):
        self.inset = max(0, int(rows))      # found on this plugin's own frames: already scaled

    def close(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            _await(stream.stopCaptureWithCompletionHandler_, "stopping the stream", CLOSE_TIMEOUT_S)
        except (OSError, TimeoutError) as exc:
            # A stream the system already ended cannot be stopped again.
            log.info("screen stream stop: %s", exc)
