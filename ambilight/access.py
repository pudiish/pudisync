"""Who may drive the hub: home-LAN devices, not other websites.

Every action is a plain GET (so iOS Shortcuts can call it), which would let any
web page a household member opens fire them with an <img> tag. Browsers label
such requests with Sec-Fetch-Site; Shortcuts and curl send no label at all, so
refusing labelled cross-site requests blocks the attack without breaking them.
A Host check stops DNS rebinding, where a public name is re-pointed at the
hub's LAN address to read its answers.
"""
import hmac
import ipaddress
import socket
import threading
import time


def allowed_host(host_header):
    """True for IP literals, localhost, Bonjour .local names and this Mac's name."""
    host = host_header.strip().lower()
    if not host:
        return False
    if host.startswith("["):                     # [ipv6]:port
        host = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:                    # name:port or ipv4:port
        host = host.split(":")[0]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    own = socket.gethostname().lower()
    return host in ("localhost", own) or host.endswith(".local")


def allowed_fetch_site(value):
    """Only the hub's own pages, typed URLs and non-browser clients."""
    return value is None or value in ("same-origin", "none")


class PinGate:
    """Constant-time PIN checks with a per-device lockout against guessing."""

    def __init__(self, max_failures=5, window_s=60.0, lockout_s=60.0):
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self.lock = threading.Lock()
        self.failures = {}
        self.locked_until = {}

    def check(self, given, pin, client, now=None):
        """Returns "ok", "denied" or "locked"."""
        if not pin:
            return "ok"
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.locked_until.get(client, 0) > now:
                return "locked"
            if hmac.compare_digest(given.encode(), pin.encode()):
                self.failures.pop(client, None)
                return "ok"
            recent = [t for t in self.failures.get(client, []) if now - t < self.window_s]
            recent.append(now)
            self.failures[client] = recent
            if len(recent) >= self.max_failures:
                self.locked_until[client] = now + self.lockout_s
                self.failures.pop(client, None)
            return "denied"
