"""Running the webcam calibration on the real hardware.

Needs: the hub running (it paints the strip), a webcam that sees the monitor
and the glow around it, and camera permission for the app that runs this.
The maths lives in camcal.py; this module only moves pixels and photons:

1. find the screen and the wall glow in the camera picture
2. score how well the wall matches the screen with the current settings
3. show test colours on the monitor while painting the strip with
   independent colours, and photograph each pair
4. fit the colour matrix, apply it, and score again
5. keep it only if the wall now matches the screen better; otherwise restore
"""
import json
import threading
import time
import urllib.parse
import urllib.request

import numpy as np

from . import camcal
from .engine import resolve_display
from .plugins.capture_quartz import QuartzCapture
from .recorder import RECORDINGS_DIR

SETTLE_S = 0.8          # strip, screen and camera exposure all need a moment
VERIFY_SETTLE_S = 1.4   # sync smoothing on top of that
WORK_WIDTH = 640        # camera pictures are analysed at this width
MIN_READING = 20        # camera code value below which a region shows no usable colour
VERIFY_COLOURS = [(255, 255, 255), (224, 172, 138), (255, 0, 0), (0, 200, 80),
                  (40, 80, 255), (255, 200, 90), (150, 60, 220), (0, 170, 160)]


class Camera:
    """A webcam read continuously in the background, so pictures are never stale."""

    def __init__(self, index):
        import cv2

        self.cv2 = cv2
        self.cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"camera {index} could not be opened. Allow camera access for the app running "
                "this (System Settings > Privacy & Security > Camera), then restart that app.")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.lock = threading.Lock()
        self.frame, self.count = None, 0
        self.running = True
        self.thread = threading.Thread(target=self.read_loop, daemon=True, name="camera")
        self.thread.start()
        if not self.wait_frames(1, 5.0):
            self.close()
            raise RuntimeError("the camera opened but sends no pictures (permission, or in use)")

    def read_loop(self):
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                scale = WORK_WIDTH / frame.shape[1]
                small = self.cv2.resize(frame, None, fx=scale, fy=scale, interpolation=self.cv2.INTER_AREA)
                with self.lock:
                    self.frame, self.count = small[..., ::-1].copy(), self.count + 1

    def wait_frames(self, count, timeout):
        with self.lock:
            target = self.count + count
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if self.count >= target:
                    return True
            time.sleep(0.01)
        return False

    def snapshot(self, frames=5):
        """The average of the next `frames` pictures, as float RGB."""
        total, seen = None, 0
        for _ in range(frames):
            if not self.wait_frames(1, 2.0):
                raise RuntimeError("the camera stopped sending pictures")
            with self.lock:
                frame = self.frame.astype(np.float64)
            total = frame if total is None else total + frame
            seen += 1
        return total / seen

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.cap.release()


class PatchWindow:
    """A borderless window covering one display, filled with a test colour."""

    def __init__(self, bounds):
        import tkinter as tk

        x, y, width, height = bounds
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg="#000000", cursor="none")
        self.root.update()

    def show(self, rgb):
        self.root.configure(bg="#%02x%02x%02x" % tuple(int(v) for v in rgb))
        self.root.update()

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.02)

    def close(self):
        self.root.destroy()


class HubClient:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path, **params):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())

    def paint(self, rgb):
        return self.get("/api/calibrate/paint", rgb="%02x%02x%02x" % tuple(int(v) for v in rgb))


def display_bounds(index):
    """(x, y, width, height) in points of display `index` (numbered like the hub)."""
    import Quartz

    displays = Quartz.CGGetActiveDisplayList(16, None, None)[1] or ()
    box = Quartz.CGDisplayBounds(displays[index - 1])
    return int(box.origin.x), int(box.origin.y), int(box.size.width), int(box.size.height)


def locate(window, hub, cam):
    """Find the screen and the wall glow in the camera picture."""
    window.show((0, 0, 0)); hub.paint((0, 0, 0)); window.wait(1.5)
    dark = cam.snapshot(8)
    window.show((255, 255, 255)); window.wait(SETTLE_S)
    screen_lit = cam.snapshot(8)
    window.show((0, 0, 0)); hub.paint((255, 255, 255)); window.wait(SETTLE_S)
    strip_lit = cam.snapshot(8)
    screen, wall = camcal.find_regions(dark, screen_lit, strip_lit)
    if screen.mean() < 0.02 or wall.mean() < 0.01:
        raise RuntimeError("the camera cannot see both the screen and the glow on the wall; "
                           "aim it so the monitor and the wall around it are in view")
    save_view(strip_lit, screen, wall)
    return screen, wall


def save_view(picture, screen, wall):
    """A picture of what was found, so the setup can be checked by eye."""
    import cv2

    RECORDINGS_DIR.mkdir(exist_ok=True)
    view = picture.clip(0, 255).astype(np.uint8).copy()
    view[screen] = (view[screen] * 0.5 + np.array([0, 120, 255]) * 0.5).astype(np.uint8)
    view[wall] = (view[wall] * 0.5 + np.array([255, 160, 0]) * 0.5).astype(np.uint8)
    cv2.imwrite(str(RECORDINGS_DIR / "calibration-view.jpg"), view[..., ::-1])
    np.savez_compressed(RECORDINGS_DIR / "calibration-regions.npz", screen=screen, wall=wall)


def score(window, hub, cam, screen, wall):
    """How far the wall is from the screen with the hub syncing (0 = a match)."""
    hub.get("/api/calibrate/done")
    hub.get("/api/sync/on")
    pairs = []
    for colour in VERIFY_COLOURS:
        window.show(colour); window.wait(VERIFY_SETTLE_S)
        picture = cam.snapshot(5)
        pairs.append((camcal.region_colour(picture, screen), camcal.region_colour(picture, wall)))
    return camcal.mismatch(pairs)


def measure(window, hub, cam, screen, wall, log):
    samples = []
    plan = camcal.patch_plan()
    for i, (colour, drive) in enumerate(plan, 1):
        window.show(colour); hub.paint(drive); window.wait(SETTLE_S)
        picture = cam.snapshot(5)
        samples.append((colour, drive, camcal.region_colour(picture, screen),
                        camcal.region_colour(picture, wall)))
        if i % 6 == 0:
            log(f"  measured {i}/{len(plan)} colours")
    return samples


def run_calibration(port, camera_index, log=print):
    """Open the camera and a test-colour window on the synced display, then calibrate."""
    hub = HubClient(port)
    status = hub.get("/api/status")
    display = resolve_display(status["monitor_index"], QuartzCapture.display_count())
    cam = Camera(camera_index)
    try:
        window = PatchWindow(display_bounds(display))
        try:
            return calibrate(window, hub, cam, status, log)
        finally:
            window.close()
    finally:
        cam.close()


def calibrate(window, hub, cam, status, log):
    """Measure, fit, apply and verify. Returns a summary dict."""
    previous = {k: status[k] for k in ("led_matrix", "wb_r", "wb_g", "wb_b")}
    try:
        log("Finding the screen and the wall glow…")
        screen, wall = locate(window, hub, cam)
        log("Scoring the current colours…")
        before = score(window, hub, cam, screen, wall)
        log(f"  mismatch before: {before:.3f}")
        log("Measuring test colours…")
        fitted = camcal.fit_matrix(measure(window, hub, cam, screen, wall, log))
        # The fitted matrix already includes this strip's channel balance.
        hub.get("/api/set", led_matrix=json.dumps(fitted.round(4).flatten().tolist()),
                wb_r=1, wb_g=1, wb_b=1)
        log("Scoring the fitted colours…")
        after = score(window, hub, cam, screen, wall)
        log(f"  mismatch after: {after:.3f}")
        kept = after < before
        if not kept:
            hub.get("/api/set", led_matrix=json.dumps(previous["led_matrix"]),
                    wb_r=previous["wb_r"], wb_g=previous["wb_g"], wb_b=previous["wb_b"])
        return {"before": before, "after": after, "kept": kept,
                "matrix": fitted.round(4).tolist(), "view": str(RECORDINGS_DIR / "calibration-view.jpg")}
    finally:
        hub.get("/api/calibrate/done")
        hub.get("/api/sync/on")


def run_check(port, camera_index, seconds, log=print):
    """Score the match while a video plays, without changing anything."""
    regions = RECORDINGS_DIR / "calibration-regions.npz"
    if not regions.exists():
        raise RuntimeError("run the calibration once first, so the screen and wall are known")
    data = np.load(regions)
    cam = Camera(camera_index)
    try:
        result = watch(cam, data["screen"], data["wall"], seconds)
    finally:
        cam.close()
    log(f"Over {seconds}s of video: mismatch {result:.3f} (0 = the wall matches the screen)")
    return result


def watch(cam, screen, wall, seconds):
    """Mismatch between the screen's border and the wall glow over `seconds`."""
    # The strip follows the screen's edges, not its middle.
    edge = screen & camcal.grow(~screen, max(2, min(screen.shape) // 25))
    pairs, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        picture = cam.snapshot(3)
        seen = camcal.region_colour(picture, edge), camcal.region_colour(picture, wall)
        # Dark scenes carry no colour to compare, only sensor noise.
        if min(seen[0].max(), seen[1].max()) >= MIN_READING:
            pairs.append(seen)
    if not pairs:
        raise RuntimeError("the video stayed too dark to compare colours; play something brighter")
    return camcal.mismatch(pairs)
