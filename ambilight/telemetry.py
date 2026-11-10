"""Live timing for the sync pipeline: how long each stage takes, how many
frames reach the lights, and how old a picture is when it is first sent.

The capture thread and the output thread write; HTTP threads read a snapshot.
Every figure covers the last few seconds, so a status poll shows now, not the
average since start.
"""
import math
import threading
import time
from collections import deque
from contextlib import contextmanager

STAGES = ("capture", "process", "colour", "smooth", "send")
WINDOW = 240            # samples kept per stage: several seconds at 30-60 fps
RATE_WINDOW_S = 1.0     # delivered fps counts the sends in this last stretch


def percentile(values, share):
    """Nearest-rank percentile of `values` (0 < share <= 1), or None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summary_ms(seconds):
    """Median and p95 of durations in seconds, as milliseconds."""
    return {name: None if value is None else round(value * 1000, 2)
            for name, value in (("p50", percentile(seconds, 0.5)), ("p95", percentile(seconds, 0.95)))}


class Telemetry:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        """Forget everything; called each time sync starts."""
        with self.lock:
            self.stages = {name: deque(maxlen=WINDOW) for name in STAGES}
            self.latency = deque(maxlen=WINDOW)
            self.recent_sends = deque()
            self.delivered = 0
            self.send_errors = 0
            self.missed_slots = 0
            self.capture_started = None
            self.capture_counted = None

    @contextmanager
    def stage(self, name):
        """Time the block as one sample of `name`. A block that raises is not counted."""
        start = self.clock()
        yield
        self.record(name, self.clock() - start)

    def record(self, name, seconds):
        with self.lock:
            self.stages[name].append(seconds)

    def captured(self, started):
        """A new target is ready, from a grab that began at `started`."""
        with self.lock:
            self.capture_started = started

    def sent(self, ok, now):
        """One frame handed to the output. The first good send after a new
        capture measures how old that picture was when it left."""
        with self.lock:
            if not ok:
                self.send_errors += 1
                return
            self.delivered += 1
            self.recent_sends.append(now)
            while now - self.recent_sends[0] > RATE_WINDOW_S:
                self.recent_sends.popleft()
            if self.capture_started is not None and self.capture_started != self.capture_counted:
                self.latency.append(now - self.capture_started)
                self.capture_counted = self.capture_started

    def missed(self, slots):
        """Output frames the pacer skipped because it fell too far behind."""
        if slots:
            with self.lock:
                self.missed_slots += slots

    def snapshot(self):
        with self.lock:
            now = self.clock()
            fresh = sum(1 for t in self.recent_sends if now - t <= RATE_WINDOW_S)
            return {
                "delivered_fps": round(fresh / RATE_WINDOW_S, 1),
                "delivered": self.delivered,
                "dropped": self.missed_slots + self.send_errors,
                "send_errors": self.send_errors,
                "stages_ms": {name: summary_ms(samples) for name, samples in self.stages.items()},
                "latency_ms": summary_ms(self.latency),
            }
