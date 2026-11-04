"""Running the webcam calibration on the real hardware.

Needs: the hub running (it paints the strip), a webcam that sees the monitor
and the glow around it, and camera permission for the app that runs this.
The maths lives in camcal.py; this module only moves pixels and photons:

1. find the screen and the wall glow in the camera picture
2. score how well the wall matches the screen with the current settings
3. for white, red, green and blue on the monitor, nudge the strip until
   the wall's colour matches the screen's in the same picture
4. turn the four matches into the colour matrix, and score it
5. keep whichever settings matched best, the old ones included
"""
import json
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from . import camcal
from .engine import KeepAwake, resolve_display
from .plugins.capture_quartz import QuartzCapture
from .recorder import RECORDINGS_DIR

SETTLE_S = 1.5          # the webcam's auto exposure takes this long to settle
STEP_S = 0.7            # one matching step: only proportions are read, so exposure may still drift
VERIFY_SETTLE_S = 1.2   # sync smoothing on top of that
MAX_STEPS = 10          # matching steps per colour
MATCHED = 0.03          # mismatch at which a colour counts as matched
SCREEN_LEVEL_RETRIES = 3   # halve the test level this many times if the screen patch blows out
WORK_WIDTH = 640        # camera pictures are analysed at this width
SETTINGS = ("led_matrix", "wb_r", "wb_g", "wb_b", "saturation")    # what calibration may change
NOT_SHOWN = ("the test colours did not show on the monitor (another window on top?), "
             "or the camera does not see the whole monitor")
SCRIPT = Path(__file__).resolve().parent.parent / "ambilight.py"
JOB_LINES = 60          # progress lines kept for the web page
VERIFY_COLOURS = [(255, 255, 255), (224, 172, 138), (255, 0, 0), (0, 200, 80),
                  (40, 80, 255), (255, 200, 90), (150, 60, 220), (0, 170, 160)]


AVFOUNDATION = "/System/Library/Frameworks/AVFoundation.framework"


def list_cameras():
    """(name, built in?) per camera, numbered as OpenCV numbers them. None is opened.

    OpenCV 4.x lists AVFoundation's video and muxed devices sorted by uniqueID
    (cap_avfoundation_mac.mm), so the same calls and sort give its numbers.
    Plugging a webcam in or out renumbers them, so numbers are never trusted.
    """
    import functools

    import objc

    objc.loadBundle("AVFoundation", {}, bundle_path=AVFOUNDATION)
    device = objc.lookUpClass("AVCaptureDevice")
    found = list(device.devicesWithMediaType_("vide")) + list(device.devicesWithMediaType_("muxx"))
    found.sort(key=functools.cmp_to_key(lambda a, b: a.uniqueID().compare_(b.uniqueID())))
    return [(str(d.localizedName()), str(d.deviceType()).startswith("AVCaptureDeviceTypeBuiltIn"))
            for d in found]


def pick_camera(cameras, wanted=None):
    """(OpenCV number, name) of the camera to use. Never a built-in camera.

    `wanted` is part of a camera's name; without it, the first external
    webcam that is not a phone.
    """
    external = [(i, name) for i, (name, built_in) in enumerate(cameras) if not built_in]
    if wanted:
        usable = [(i, name) for i, name in external if wanted.lower() in name.lower()]
        built_in = [name for name, inside in cameras if inside and wanted.lower() in name.lower()]
        if built_in and not usable:
            raise RuntimeError(f"{built_in[0]} is a built-in camera, which is never used")
    else:
        usable = [(i, name) for i, name in external if "iphone" not in name.lower()]
    if not usable:
        raise RuntimeError("no external webcam is connected (the built-in camera is never used); "
                           f"cameras seen: {', '.join(name for name, _ in cameras) or 'none'}")
    return usable[0]


def list_cameras_in_child():
    """list_cameras(), run in a short-lived process for the hub.

    Loading AVFoundation holds the interpreter lock for about a second the
    first time, which froze the light while the web page listed webcams.
    """
    code = "import json; from ambilight.camrun import list_cameras; print(json.dumps(list_cameras()))"
    try:
        done = subprocess.run([sys.executable, "-c", code], cwd=str(SCRIPT.parent), capture_output=True,
                              text=True, timeout=20, check=True)
        return [(name, builtin) for name, builtin in json.loads(done.stdout)]
    except (subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"could not list the webcams: {exc}") from None


class CameraJob:
    """Runs the calibration (or a check) for the web page, as its own process.

    The test-colour window needs a main thread of its own, which the hub's web
    server cannot give it, so the hub starts `ambilight.py --camera-calibrate`
    and relays its progress lines. Same shape as provision.Provisioner.
    """

    def __init__(self, port, popen=subprocess.Popen, cameras=None):
        self.port = port
        self.popen = popen
        self.cameras = cameras or list_cameras_in_child
        self.lock = threading.Lock()
        self.state, self.task, self.camera, self.lines = "idle", None, None, []

    def status(self):
        with self.lock:
            return {"state": self.state, "task": self.task, "camera": self.camera, "log": list(self.lines)}

    def start(self, task, wanted=None, seconds=60):
        """task "calibrate" or "check"; raises RuntimeError when it cannot start."""
        name = pick_camera(self.cameras(), wanted)[1]       # refuses the built-in camera
        args = [sys.executable, str(SCRIPT), "--port", str(self.port), "--camera", name]
        args += ["--camera-calibrate"] if task == "calibrate" else ["--camera-check", str(int(seconds))]
        with self.lock:
            if self.state == "working":
                raise RuntimeError("a webcam calibration is already running")
            process = self.popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 cwd=str(SCRIPT.parent))
            self.state, self.task, self.camera, self.lines = "working", task, name, []
        threading.Thread(target=self.follow, args=(process,), daemon=True, name="camera-job").start()

    def follow(self, process):
        for line in process.stdout:
            with self.lock:
                self.lines = (self.lines + [line.strip()])[-JOB_LINES:]
        code = process.wait()
        with self.lock:
            self.state = "done" if code == 0 else "error"


def open_camera(wanted=None, log=print):
    index, name = pick_camera(list_cameras(), wanted)
    log(f"Using {name}")
    return Camera(index)


class Camera:
    """A webcam read continuously in the background, so pictures are never stale.
    Opened through open_camera(), which never picks a built-in camera."""

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
    """A borderless panel covering one display, filled with a test colour.

    A video playing full screen lives in its own Space, where ordinary windows
    never appear. This panel joins every Space, full-screen ones included, and
    sits above them without taking focus from the video.
    """

    def __init__(self, display_id):
        import AppKit

        self.appkit = AppKit
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory)
        screen = next(s for s in AppKit.NSScreen.screens()
                      if s.deviceDescription()["NSScreenNumber"] == display_id)
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            screen.frame(), AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        self.panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        self.panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                          | AppKit.NSWindowCollectionBehaviorStationary)
        self.panel.setFrame_display_(screen.frame(), True)
        self.show((0, 0, 0))
        self.panel.orderFrontRegardless()
        self.wait(0.2)

    def show(self, rgb):
        r, g, b = (v / 255.0 for v in rgb)
        # Test colours are sRGB code values, like the video the strip follows.
        self.panel.setBackgroundColor_(self.appkit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0))
        self.panel.display()
        self.wait(0)

    def wait(self, seconds):
        AppKit = self.appkit
        end = time.monotonic() + seconds
        while True:
            AppKit.NSRunLoop.currentRunLoop().runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.02))
            if time.monotonic() >= end:
                return

    def close(self):
        self.panel.orderOut_(None)
        self.wait(0.1)


class HubClient:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path, **params):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())

    def paint(self, rgb):
        return self.get("/api/calibrate/paint", rgb="%02x%02x%02x" % tuple(int(v) for v in rgb))


def display_id(index):
    """The CoreGraphics id of display `index` (numbered like the hub)."""
    import Quartz

    return (Quartz.CGGetActiveDisplayList(16, None, None)[1] or ())[index - 1]


def light(window, hub, screen, strip, settle=SETTLE_S):
    """Show one test pair and let the camera settle on it."""
    window.show(screen)
    hub.paint(strip)
    window.wait(settle / 2)
    hub.paint(strip)        # the paint is one UDP packet over Wi-Fi; a lost one would leave the old colour
    window.wait(settle / 2)


def locate(window, hub, cam):
    """Find where to read the screen and the wall glow in the camera picture."""
    light(window, hub, camcal.dimmed((255, 255, 255)), (0, 0, 0))
    screen_lit = cam.snapshot(8)
    light(window, hub, (0, 0, 0), (round(255 * camcal.DRIVE_PEAK),) * 3)
    strip_lit = cam.snapshot(8)
    screen, wall = camcal.find_regions(screen_lit, strip_lit)
    screen = largest_region(screen)     # not a laptop screen or a lamp elsewhere in view
    if not camcal.looks_like_screen(screen):
        save_view(strip_lit, screen, wall)
        raise RuntimeError(NOT_SHOWN)
    screen = camcal.screen_edge(screen)     # the border, which the strip follows
    save_view(strip_lit, screen, wall)
    if screen.sum() < camcal.MIN_PIXELS or wall.sum() < camcal.MIN_PIXELS:
        raise RuntimeError("the camera cannot see both the monitor and the glow on the wall "
                           "around it; aim it so both are in view")
    return screen, wall


def largest_region(mask):
    import cv2

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count < 2:
        return mask
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == biggest


def save_view(picture, screen, wall):
    """A picture of what was found, so the setup can be checked by eye."""
    import cv2

    RECORDINGS_DIR.mkdir(exist_ok=True)
    view = picture.clip(0, 255).astype(np.uint8).copy()
    view[screen] = (view[screen] * 0.5 + np.array([0, 120, 255]) * 0.5).astype(np.uint8)
    view[wall] = (view[wall] * 0.5 + np.array([255, 160, 0]) * 0.5).astype(np.uint8)
    cv2.imwrite(str(RECORDINGS_DIR / "calibration-view.jpg"), view[..., ::-1])
    np.savez_compressed(RECORDINGS_DIR / "calibration-regions.npz", screen=screen, wall=wall)


def read(cam, screen, wall):
    """(screen proportions, wall proportions) from one fresh picture."""
    picture = cam.snapshot(3)
    return camcal.proportions(picture, screen), camcal.proportions(picture, wall)


def score(window, hub, cam, screen, wall):
    """How far the wall is from the screen with the hub syncing (0 = a match)."""
    hub.get("/api/calibrate/done")
    hub.get("/api/sync/on")
    pairs = []
    for colour in VERIFY_COLOURS:
        window.show(camcal.dimmed(colour))
        window.wait(VERIFY_SETTLE_S)
        pairs.append(read(cam, screen, wall))
    return camcal.mismatch(pairs)


def match(window, hub, cam, screen, wall, name):
    """The strip drive (linear) whose glow matches `name` on screen, and how closely.

    A saturated primary (e.g. a panel whose blue subpixel runs bright) can
    blow out the camera's screen patch even at the usual SCREEN_LEVEL; each
    step halves the level and retries rather than aborting the whole match.
    """
    colour = camcal.PRIMARIES[name]
    drive = camcal.first_guess(colour)
    closest = (float("inf"), drive)
    for _ in range(MAX_STEPS):
        level = camcal.SCREEN_LEVEL
        for attempt in range(SCREEN_LEVEL_RETRIES):
            light(window, hub, camcal.dimmed(colour, level), np.round(drive * 255).astype(int), STEP_S)
            try:
                seen_screen, seen_wall = read_test(cam, screen, wall, name)
                break
            except RuntimeError:
                if attempt == SCREEN_LEVEL_RETRIES - 1:
                    raise
                level /= 2
        off = camcal.mismatch([(seen_screen, seen_wall)])
        if off < closest[0]:
            closest = (off, drive)
        if off < MATCHED:
            break
        drive = camcal.nudge(drive, seen_screen, seen_wall)
    return closest[1], closest[0]


def read_test(cam, screen, wall, name):
    """read(), refusing pictures that cannot be trusted while `name` is on screen."""
    seen_screen, seen_wall = read(cam, screen, wall)
    if seen_screen is None:
        raise RuntimeError(NOT_SHOWN + ", or the camera is blown out by it")
    if seen_wall is None:
        raise RuntimeError("the camera lost the wall glow (blown out or too dark); "
                           "move it further back")
    # A hidden test window shows the video instead: its colour does not follow.
    if name != "white" and int(np.argmax(seen_screen)) != "rgb".index(name[0]):
        raise RuntimeError(NOT_SHOWN)
    return seen_screen, seen_wall


def candidates(fitted, status):
    """Settings worth scoring: the fitted matrix, and the same with the
    saturation boost off (once colours match, a boost only pulls them apart).

    Each one sets every key it is scored on: candidates are tried in turn, so
    a key left out would keep whatever the previous candidate set.
    """
    matrix = json.dumps(fitted.round(4).flatten().tolist())
    tried = [{"led_matrix": matrix, "wb_r": 1, "wb_g": 1, "wb_b": 1, "saturation": status["saturation"]}]
    if status["saturation"] != 1.0:
        tried.append({**tried[0], "saturation": 1.0})
    return tried


def run_calibration(port, camera_name=None, log=print):
    """Open the camera and a test-colour window on the synced display, then calibrate.

    The screen must stay lit and showing the test colours for the whole run;
    a display that sleeps mid-match leaves the camera reading a dark panel
    and the strip stuck on the last test colour, so sleep is held off here.
    """
    hub = HubClient(port)
    status = hub.get("/api/status")
    display = resolve_display(status["monitor_index"], QuartzCapture.display_count())
    cam = open_camera(camera_name, log)
    awake = KeepAwake.start(display=True)
    try:
        window = PatchWindow(display_id(display))
        try:
            return calibrate(window, hub, cam, status, log)
        finally:
            window.close()
    finally:
        cam.close()
        if awake is not None:
            awake.stop()


def calibrate(window, hub, cam, status, log):
    """Match, fit, then keep whichever settings make the wall match the screen best."""
    previous = {k: (json.dumps(status[k]) if k == "led_matrix" else status[k]) for k in SETTINGS}
    try:
        log("Finding the screen and the wall glow…")
        screen, wall = locate(window, hub, cam)
        log("Scoring the current colours…")
        scores = [(score(window, hub, cam, screen, wall), previous)]
        log(f"  mismatch now: {scores[0][0]:.3f}")
        matched = {}
        for name in camcal.PRIMARIES:
            matched[name], off = match(window, hub, cam, screen, wall, name)
            log(f"  matched {name}: mismatch {off:.3f}")
        fitted = camcal.matrix_from_primaries(matched)
        with open(RECORDINGS_DIR / "calibration-result.json", "w") as fh:
            json.dump({"matched": {k: v.tolist() for k, v in matched.items()},
                       "matrix": fitted.tolist()}, fh, indent=1)
        for settings in candidates(fitted, status):
            hub.get("/api/set", **settings)
            scores.append((score(window, hub, cam, screen, wall), settings))
            log(f"  mismatch with {describe(settings)}: {scores[-1][0]:.3f}")
        best_score, best = min(scores, key=lambda pair: pair[0])
        hub.get("/api/set", **best)
        return {"before": scores[0][0], "after": best_score, "kept": best is not previous,
                "chosen": describe(best), "matrix": fitted.round(4).tolist(),
                "view": str(RECORDINGS_DIR / "calibration-view.jpg")}
    finally:
        hub.get("/api/calibrate/done")
        hub.get("/api/sync/on")


def describe(settings):
    if json.loads(settings["led_matrix"]) == []:
        return "the old colours"
    return "the measured colours" + (", no saturation boost" if settings.get("saturation") == 1.0 else "")


def run_check(port, camera_name, seconds, log=print):
    """Score the match while a video plays, without changing anything."""
    regions = RECORDINGS_DIR / "calibration-regions.npz"
    if not regions.exists():
        raise RuntimeError("run the calibration once first, so the screen and wall are known")
    data = np.load(regions)
    cam = open_camera(camera_name, log)
    try:
        result = watch(cam, data["screen"], data["wall"], seconds)
    finally:
        cam.close()
    log(f"Over {seconds}s of video: mismatch {result:.3f} (0 = the wall matches the screen)")
    return result


def watch(cam, screen, wall, seconds):
    """Mismatch between the screen's border and the wall glow over `seconds`.
    Dark scenes, with no colour to compare, are skipped."""
    pairs, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        pairs.append(read(cam, screen, wall))
    try:
        return camcal.mismatch(pairs)
    except ValueError:
        raise RuntimeError("the video stayed too dark to compare colours; play something brighter") from None
