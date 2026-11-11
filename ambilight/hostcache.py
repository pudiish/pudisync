"""Board addresses that survive a flaky .local name.

The ESP8266's mDNS stops answering now and then while the board itself stays
reachable. A failed lookup takes macOS about 5 s, so looking the name up per
packet or request froze the stream for seconds at a time. The last good
address is kept instead, and refreshed in the background so a board that
moves to a new DHCP address is still followed.
"""
import ipaddress
import logging
import socket
import threading
import time

REFRESH_S = 30.0

log = logging.getLogger(__name__)


def lookup(host):
    return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]


def is_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class HostCache:
    def __init__(self, resolve=None, refresh_s=REFRESH_S, clock=time.monotonic):
        self.resolve = resolve
        self.refresh_s = refresh_s
        self.clock = clock
        self.lock = threading.Lock()
        self.known = {}             # host -> (address, when it was last confirmed)
        self.refreshing = set()

    def address(self, host):
        """An IP for `host`, never waiting on a lookup once one has succeeded."""
        if is_ip(host):
            return host
        with self.lock:
            entry = self.known.get(host)
            refresh = (entry is not None and host not in self.refreshing
                       and self.clock() - entry[1] > self.refresh_s)
            if refresh:
                self.refreshing.add(host)
        if entry is None:
            return self.refresh(host)       # nothing to fall back on: wait, or raise OSError
        if refresh:
            threading.Thread(target=self.refresh_in_background, args=(host,),
                             daemon=True, name="hostcache").start()
        return entry[0]

    def last_known(self, host):
        """The last address a lookup of `host` gave, without looking it up; None if never."""
        if is_ip(host):
            return host
        with self.lock:
            entry = self.known.get(host)
        return entry[0] if entry else None

    def mark_stale(self, host):
        """A send failed: look the name up again soon, but keep using the old address."""
        with self.lock:
            if host in self.known:
                self.known[host] = (self.known[host][0], float("-inf"))

    def refresh(self, host):
        try:
            found = (self.resolve or lookup)(host)
        finally:
            with self.lock:
                self.refreshing.discard(host)
        with self.lock:
            self.known[host] = (found, self.clock())
        return found

    def refresh_in_background(self, host):
        try:
            self.refresh(host)
        except OSError as exc:
            log.warning("could not look up %s (%s); still using %s",
                        host, exc, self.known.get(host, ("nothing",))[0])


BOARD = HostCache()     # shared by the stream and the JSON API, so both learn together
