"""A second strip, round a bed, on the same board's second output.

The board drives it as its own WLED segment, so it can run an effect or a
colour of its own while the monitor strip follows the screen. With realtime
data limited to the main segment ("mso"), the stream only ever reaches segment
0; to follow the screen too, segment 0 is stretched over both strips and the
hub sends the bed's pixels after the monitor's.

The board's sync preset deletes every other segment when it loads, and any
change to a segment ends the active preset (which the hub reads as "sync
off"). So the hub keeps the layout itself: live while sync is off, and while
sync runs by rewriting the sync preset with the bed in it and loading it again.
"""
import time

import numpy as np

from . import color
from .board import SYNC_LOOK, SYNC_NAME
from .safety import Guard
from .smoothing import Quantizer, Smoother, settle_seconds

SAVE_GAP_S = 1.5        # the sync preset is rewritten at most this often (flash wear)
WRITE_PAUSE_S = 5.0     # the sync watcher waits this long at most while the preset is rewritten
EDGES = ("bottom", "right", "top", "left")


def rgb(text):
    digits = text.lstrip("#")
    return [int(digits[i:i + 2], 16) for i in (0, 2, 4)]


def first_led(cfg):
    """Where the bed starts on the board: after the board's monitor output, never inside the hub's LEDs."""
    return max(cfg["bed_start"], cfg["led_count"])


def layout(cfg):
    """The segments the board should have, or None without a bed strip."""
    monitor, count = first_led(cfg), cfg["bed_leds"]
    if count <= 0:
        return None
    if cfg["bed_mode"] == "screen":
        return [{"id": 0, "start": 0, "stop": monitor + count}, {"id": 1, "stop": 0}]
    own = {"id": 1, "start": monitor, "stop": monitor + count, "sel": False,
           "on": cfg["bed_mode"] != "off", "bri": round(cfg["bed_brightness"] * 255)}
    if cfg["bed_mode"] == "effect":
        own.update({"fx": cfg["bed_effect"], "sx": cfg["bed_speed"]})
    elif cfg["bed_mode"] == "colour":
        own.update({"fx": 0, "col": [rgb(cfg["bed_colour"])]})
    return [{"id": 0, "start": 0, "stop": monitor}, own]


def matches(state, want):
    """True when the board's live segments are already `want`."""
    if state.get("mainseg", 0) != 0:
        return False
    have = {seg.get("id"): seg for seg in state.get("seg") or []}
    for seg in want:
        found = have.get(seg["id"])
        if seg.get("stop") == 0 and "start" not in seg:
            if found is not None and found.get("stop", 0) > found.get("start", 0):
                return False
            continue
        if found is None:
            return False
        for key, value in seg.items():
            if key == "sel":
                continue
            shown = (found.get("col") or [[]])[0][:3] if key == "col" else found.get(key)
            if shown != (value[0] if key == "col" else value):
                return False
    return True


def sync_preset(cfg, want):
    """The sync preset as a stored command: the board's sync look, plus the bed."""
    monitor = {**SYNC_LOOK, **want[0]}
    return {"psave": cfg["sync_preset"], "o": True, "n": SYNC_NAME, "on": True,
            "mainseg": 0, "seg": [monitor, *want[1:]]}


class Keeper:
    """Keeps the board's segments in the bed's layout. Runs on the sync watcher's thread."""

    def __init__(self, cfg, wled, intent, clock=time.monotonic):
        self.cfg = cfg
        self.wled = wled
        self.intent = intent
        self.clock = clock
        self.next_save = 0.0

    def tick(self, state):
        """One look at the board's state. Returns "applied", "saved" or None."""
        cfg = self.cfg.snapshot()
        want = layout(cfg)
        if want is None or state is None or matches(state, want):
            return None
        if state.get("ps") != cfg["sync_preset"]:
            self.wled.apply({"mainseg": 0, "seg": want})
            return "applied"
        if self.clock() < self.next_save:
            return None
        self.next_save = self.clock() + SAVE_GAP_S
        # Storing the command also applies it, which ends the active preset for
        # a moment: the watcher must not take that for "sync off".
        self.intent.pause(WRITE_PAUSE_S)
        try:
            self.wled.apply(sync_preset(cfg, want))
            self.wled.set_preset(cfg["sync_preset"])
        finally:
            self.intent.resume()
        return "saved"


def bus_config(hw_led, monitor_leds, monitor_ma, bed_leds, bed_ma, bed_pin):
    """The board's /json/cfg change for a bed strip of `bed_leds` (0 removes it).

    The board's limits match the hub's, so they also hold while the board
    renders its own looks (sync off, or an effect on the bed).
    """
    outputs = hw_led.get("ins") or []
    if not outputs or outputs[0].get("start", 0) != 0 or outputs[0].get("len", 0) < monitor_leds:
        raise ValueError(f"the board's first output is shorter than the monitor strip's {monitor_leds} LEDs; "
                         "set the LED count in Setup first")
    length = outputs[0]["len"]
    monitor = {**outputs[0], "maxpwr": monitor_ma}
    if bed_leds <= 0:
        return {"hw": {"led": {"total": length, "maxpwr": monitor_ma, "ins": [monitor]}}}
    second = {**monitor, "start": length, "len": bed_leds, "pin": [bed_pin], "rev": False,
              "skip": 0, "maxpwr": bed_ma}
    # A total limit of 0 makes WLED limit each output to its own maxpwr: right
    # for two adapters, and for one supply split between the strips.
    return {"hw": {"led": {"total": length + bed_leds, "maxpwr": 0, "ins": [monitor, second]}},
            "if": {"live": {"mso": True}}}


def streams(cfg):
    """True when the hub sends the bed's pixels after the monitor's."""
    return cfg["bed_leds"] > 0 and cfg["bed_mode"] == "screen"


def mood(targets):
    """One colour for the whole bed: the scene's strongest, or its average when it has none."""
    targets = np.asarray(targets, dtype=np.float32)
    average = targets.mean(axis=0)
    colour, strength = color.theme(targets, *color.colourful_presence(targets))
    if colour is None:
        return average
    return average + (colour - average) * strength


def side_colours(targets, cfg, count):
    """One edge of the screen (or the whole of it) stretched over `count` LEDs."""
    picked = np.asarray(targets, dtype=np.float32)[:cfg["led_count"]]
    if cfg["layout"] == "frame" and cfg["bed_side"] in EDGES:
        counts = [cfg[f"edge_{edge}"] for edge in EDGES]
        start = sum(counts[:EDGES.index(cfg["bed_side"])])
        edge = picked[start:start + counts[EDGES.index(cfg["bed_side"])]]
        if len(edge):
            picked = edge
    where = np.linspace(0, len(picked) - 1, count)
    return np.stack([np.interp(where, np.arange(len(picked)), picked[:, c]) for c in range(3)], axis=1)


def blend_colours(targets, cfg, count):
    """A strip low on the wall beside the monitor: the screen's bottom edge, mirrored out from the monitor.

    The near end carries the bottom-right corner of the screen and eases into
    the scene's mood colour toward the far end. The top of the screen never
    reaches it, since the strip sits below the monitor.
    """
    edge = side_colours(targets, {**cfg, "bed_side": "bottom"}, count)
    ramp = np.linspace(0.0, 1.0, count)[:, None]
    if not cfg["bed_reverse"]:
        edge = edge[::-1]
    else:
        ramp = ramp[::-1]
    ease = ramp * ramp * (3.0 - 2.0 * ramp)
    return edge + (mood(targets)[None, :] - edge) * ease


class BedStage:
    """The bed's pixels while it follows the screen: its own look, pace, brightness and current limit."""

    def __init__(self):
        self.smoother = Smoother()
        self.guard = Guard()
        self.quantizer = Quantizer()

    def frame(self, targets, now, cfg, fade=1.0):
        count = cfg["bed_leds"]
        # Every look moves at the monitor strip's pace; a slower bed reads as out of sync.
        settle = settle_seconds(cfg["blend"])
        if cfg["bed_look"] == "mood":
            wanted = np.repeat(mood(targets)[None, :], count, axis=0)
        elif cfg["bed_look"] == "blend":
            wanted = blend_colours(targets, cfg, count)
        else:
            wanted = side_colours(targets, cfg, count)
        self.smoother.set_target(wanted, now)
        shown = self.smoother.step(now, settle)
        levels = color.to_led(shown, cfg) * cfg["bed_brightness"] * fade
        limits = {**cfg, "power_limit_ma": cfg["bed_power_ma"],
                  "sustained_ma": cfg["bed_power_ma"] * cfg["sustained_ma"] / cfg["power_limit_ma"]}
        return self.quantizer.apply(self.guard.apply(levels, now, limits))
