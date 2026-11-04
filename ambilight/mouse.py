"""A Logitech G102 / G203 LIGHTSYNC mouse whose three light zones follow the screen.

The reports come from OpenRGB's driver for this mouse (product 0xC092) and were
checked on the real one: take control, set the zones, apply; handing back
returns the mouse to the lighting stored in it. Zone colours are set live and
never written to the mouse's memory, so sending them often wears nothing out.
Zones 1-3 sit left, middle and right as seen from the seat.
"""
import ctypes
import logging
import threading
import time

import numpy as np

from . import color

VENDOR = 0x046D
PRODUCTS = (0xC092,)
USAGE_PAGE, LONG_REPORTS = 0xFF00, 2
REPORT_SIZE = 20
TAKE_CONTROL = (0x11, 0xFF, 0x0E, 0x50, 0x01, 0x03, 0x07)
HAND_BACK = (0x11, 0xFF, 0x0E, 0x50, 0x01, 0x03, 0x05)
APPLY = (0x11, 0xFF, 0x12, 0x70)
MIN_INTERVAL_S = 1 / 30
RETRY_S = 3.0
LISTEN_EVENT = 1                # IOKit's kIOHIDRequestTypeListenEvent: "Input Monitoring"
ACCESS = {0: "granted", 1: "denied", 2: "not asked"}
IOKIT = "/System/Library/Frameworks/IOKit.framework/IOKit"
LIFT = 0.45     # mouse level = strip level ** LIFT: 20% shows at 48%, 50% at 73%
DARK = 0.03     # below this level the scene is black, and the mouse goes dark with it

log = logging.getLogger(__name__)


def report(*data):
    return bytes(data) + bytes(REPORT_SIZE - len(data))


def zone_report(colours):
    data = [0x11, 0xFF, 0x12, 0x10]
    for zone, rgb in enumerate(colours, start=1):
        data += [zone, *rgb]
    return report(*data, 0xFF)


def zone_colours(pixels, cfg):
    """Left, middle and right colours from the LEDs nearest the mouse: the
    bottom edge of a frame layout (it runs left to right), else the whole strip."""
    leds = np.asarray(pixels)
    if cfg["layout"] == "frame" and cfg["edge_bottom"] >= 3:
        leds = leds[:cfg["edge_bottom"]]
    return [tuple(int(v) for v in part.mean(axis=0).round()) for part in np.array_split(leds, 3)]


def vivid(colours):
    """Strip drive levels -> mouse levels, same colour, brighter.

    The strip's levels are shaped for a glow on the wall (gamma, contrast,
    power limit); passed straight to the mouse's small LEDs they look dull.
    """
    hsv = color.rgb_to_hsv(np.asarray(colours, np.float32) / 255.0)
    level = hsv[:, 2]
    hsv[:, 2] = np.where(level < DARK, 0.0, level ** LIFT)
    return [tuple(int(v) for v in row) for row in (color.hsv_to_rgb(hsv) * 255.0).round()]


def load_hid():
    """The hidapi module set to share devices, or None when it is not installed.

    macOS hidapi opens devices exclusively by default, which fails here: the
    system already holds this interface for the mouse's media keys.
    """
    try:
        import hid
    except ImportError:
        return None
    library = ctypes.CDLL(hid.__file__)
    library.hid_init()
    library.hid_darwin_set_open_exclusive(0)
    return hid


def input_access():
    """macOS's Input Monitoring decision for this app. Opening the mouse needs
    it because the same interface carries the mouse's media keys; a hub run by
    launchd counts as its own app (Python), apart from the Terminal it came from."""
    return ACCESS.get(ctypes.CDLL(IOKIT).IOHIDCheckAccess(LISTEN_EVENT), "not asked")


def request_input_access():
    """Make macOS show its Input Monitoring prompt for this app."""
    ctypes.CDLL(IOKIT).IOHIDRequestAccess(LISTEN_EVENT)


class LightsyncMouse:
    def __init__(self, hid_module, clock=time.monotonic, access=input_access,
                 request_access=request_input_access):
        self.hid = hid_module
        self.clock = clock
        self.access = access
        self.request_access = request_access
        self.asked = False
        self.device = None
        self.path = None
        self.name = None
        self.shown = None
        self.sent_at = float("-inf")
        self.retry_at = float("-inf")
        self.lock = threading.Lock()

    def find(self):
        if self.hid is None:
            return None
        for info in self.hid.enumerate(VENDOR, 0):
            if (info["product_id"] in PRODUCTS and info["usage_page"] == USAGE_PAGE
                    and info["usage"] == LONG_REPORTS):
                return info
        return None

    def open(self):
        info = self.find()
        if info is None:
            return False
        device = self.hid.device()
        try:
            device.open_path(info["path"])
        except OSError:
            if self.access() != "granted":
                if not self.asked:
                    self.asked = True
                    self.request_access()
                raise OSError("macOS has not allowed Input Monitoring for Python: System Settings "
                              "> Privacy & Security > Input Monitoring") from None
            raise
        self.device, self.path, self.name = device, info["path"], info["product_string"]
        self.shown = None
        self.send(report(*TAKE_CONTROL))
        return True

    def send(self, data):
        if self.device.write(data) < 0:
            raise OSError("the mouse did not take the report")

    def show(self, colours):
        """Set the three zones, at most MIN_INTERVAL_S apart; never raises."""
        with self.lock:
            self.show_now([tuple(c) for c in colours], self.clock())

    def show_now(self, colours, now):
        if colours == self.shown or now - self.sent_at < MIN_INTERVAL_S:
            return
        try:
            if self.device is None:
                if now < self.retry_at or not self.open():
                    return
            self.send(zone_report(colours))
            self.send(report(*APPLY))
            self.shown, self.sent_at = colours, now
        except OSError as exc:
            log.warning("mouse lighting stopped (%s); trying again in %.0f s", exc, RETRY_S)
            self.close()
            self.retry_at = now + RETRY_S

    def release(self):
        """Give the mouse its own lighting back."""
        with self.lock:
            if self.device is None:
                return
            try:
                self.send(report(*HAND_BACK))
            except OSError as exc:
                log.warning("could not hand the mouse its lighting back: %s", exc)
            self.close()

    def close(self):
        if self.device is not None:
            self.device.close()
        self.device = None

    def status(self):
        if self.device is not None:
            return {"connected": True, "name": self.name, "permission": "granted"}
        info = self.find()
        return {"connected": info is not None, "name": info["product_string"] if info else None,
                "permission": self.access()}
