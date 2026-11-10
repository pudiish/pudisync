"""Monitor sync is a choice made on the board; the Mac only follows it.

The page served by the WLED board offers "Monitor sync" as one of its looks:
WLED preset `sync_preset`. The hub watches the board and streams the screen
exactly while that preset is active. Picking any effect or colour changes the
board's state, which clears the preset, and the stream stops. So the phone
needs no address for the Mac, and with the Mac off the page still works.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)

POLL_S = 1.0


class SyncIntent:
    def __init__(self, cfg, wled, engine, clock=time.monotonic):
        self.cfg = cfg
        self.wled = wled
        self.engine = engine
        self.clock = clock
        self.paused_until = 0.0
        # Also shown every state read here, on this thread (bed.Keeper).
        self.watchers = []
        self.woken = threading.Event()

    def pause(self, seconds=600):
        """Hold off while something else owns the strip (setup wizard, flashing)."""
        self.paused_until = self.clock() + seconds

    def resume(self):
        self.paused_until = 0.0

    def tick(self):
        """One look at the board. Returns "started", "stopped" or None."""
        if self.clock() < self.paused_until:
            return None
        state = self.wled.state()
        if state is None:       # an unreachable board says nothing about intent
            return None
        wanted = state.get("ps") == self.cfg.get("sync_preset")
        change = None
        if wanted and not self.engine.running:
            self.engine.start()
            change = "started"
        elif not wanted and self.engine.running:
            self.engine.stop()
            change = "stopped"
        for watch in self.watchers:
            watch(state)
        return change

    def nudge(self):
        """Look at the board now rather than at the next poll (a setting changed)."""
        self.woken.set()

    def run(self, stop):
        """Watch until `stop` is set; one bad poll never ends the watch."""
        while not stop.is_set():
            self.woken.wait(POLL_S)
            self.woken.clear()
            if stop.is_set():
                break
            try:
                self.tick()
            except Exception:
                log.exception("sync watcher hit an error and keeps going")

    def start(self, stop):
        thread = threading.Thread(target=self.run, args=(stop,), daemon=True, name="intent")
        thread.start()
        return thread
