"""Baseline output plugin: WLED UDP realtime, DNRGB protocol.

Protocol per https://kno.wled.ge/interfaces/udp-realtime/ :
  byte 0  = 4            (DNRGB)
  byte 1  = timeout seconds before WLED returns to normal mode (255 = hold)
  byte 2  = start index, high byte
  byte 3  = start index, low byte
  byte 4+ = RGB triplets
Docs cap DNRGB at 489 LEDs per packet; we chunk at 400 for headroom.
"""
import socket
import time

from . import OutputPlugin

DNRGB = 4
MAX_LEDS_PER_PACKET = 400
HOLD_FOREVER = 255
RESOLVE_EVERY_S = 30.0   # a .local name follows the controller across DHCP changes


class WLEDUDPOutput(OutputPlugin):
    name = "wled-udp-dnrgb"

    @staticmethod
    def available():
        return True  # stdlib sockets only

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg["wled_ip"], cfg["wled_port"])

    @staticmethod
    def key(cfg):
        return ("udp", cfg["wled_ip"], cfg["wled_port"])

    def __init__(self, host, port=21324):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self.last_error = None
        self.address = None
        self.resolved_at = 0.0

    def target(self):
        """The controller's address, resolved once and refreshed periodically.

        Resolving a host name per packet would stall the output thread
        whenever mDNS is slow; an IP literal resolves instantly.
        """
        now = time.monotonic()
        if self.address is None or now - self.resolved_at > RESOLVE_EVERY_S:
            info = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_DGRAM)
            self.address = info[0][4]
            self.resolved_at = now
        return self.address

    def send(self, pixels, timeout_s=2):
        """Send pixels as one or more DNRGB packets. Returns True on success."""
        raw = memoryview(pixels.tobytes())
        total = len(pixels)
        ok = True
        for start in range(0, total, MAX_LEDS_PER_PACKET):
            count = min(MAX_LEDS_PER_PACKET, total - start)
            header = bytes((DNRGB, timeout_s & 0xFF, (start >> 8) & 0xFF, start & 0xFF))
            body = raw[start * 3 : (start + count) * 3]
            if not self._sendto(header + bytes(body)):
                ok = False
        return ok

    def release(self):
        """Send one black frame with a 1s timeout so WLED resumes its own mode."""
        import numpy as np

        blank = np.zeros((1, 3), dtype=np.uint8)
        return self.send(blank, timeout_s=1)

    def _sendto(self, packet):
        try:
            self._sock.sendto(packet, self.target())
            self.last_error = None
            return True
        except OSError as exc:
            # A dark or rebooting NodeMCU must not kill the frame loop, and a
            # stale address must be looked up again on the next packet.
            self.address = None
            self.last_error = f"{self.host}:{self.port} {exc}"
            return False

    def close(self):
        self._sock.close()
