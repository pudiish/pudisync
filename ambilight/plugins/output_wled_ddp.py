"""Output plugin: WLED realtime over DDP (Distributed Display Protocol).

Packet format as WLED 16.0.1 receives it on UDP port 4048
(wled00/src/dependencies/e131/ESPAsyncE131.h:52-75, wled00/e131.cpp:20-93):
  byte 0    flags: 0x40 = version 1, 0x01 = push (draw the frame now)
  byte 1    sequence number in the low 4 bits, 1-15 (0 = unused)
  byte 2    data type: 0x0B = RGB, 8 bits per channel
  byte 3    destination: 1 = the display
  byte 4-7  where this packet's data starts in the frame, in bytes, big-endian
  byte 8-9  data length in bytes, big-endian
  byte 10+  RGB triplets
WLED draws once per frame, on the packet that carries push, so a frame split
over several packets never shows half old and half new. 1440 data bytes
(480 LEDs) per packet is what WLED's own DDP sender uses (ESPAsyncE131.h:63).

DDP has no timeout field: the board returns to its own mode after its realtime
timeout (2.5 s by default). Holding a frame indefinitely, and handing control
back at once, are only possible on WLED's UDP realtime port, so those two go
out as DNRGB.
"""
import socket

from . import OutputPlugin
from .output_wled_udp import HOLD_FOREVER, WLEDUDPOutput
from .. import hostcache

DDP_PORT = 4048
HEADER_LEN = 10
MAX_LEDS_PER_PACKET = 480
VERSION_1 = 0x40
PUSH = 0x01
RGB24 = 0x0B
DISPLAY = 1


def header(offset, length, sequence, push):
    flags = VERSION_1 | (PUSH if push else 0)
    return (bytes((flags, sequence, RGB24, DISPLAY))
            + offset.to_bytes(4, "big") + length.to_bytes(2, "big"))


class WLEDDDPOutput(OutputPlugin):
    name = "wled-ddp"

    @staticmethod
    def available():
        return True  # stdlib sockets only

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg["wled_ip"], cfg["ddp_port"], cfg["wled_port"])

    @staticmethod
    def key(cfg):
        return ("ddp", cfg["wled_ip"], cfg["ddp_port"], cfg["wled_port"])

    def __init__(self, host, port=DDP_PORT, udp_port=21324):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._control = WLEDUDPOutput(host, udp_port)
        self._sequence = 0
        self.last_error = None

    def send(self, pixels, timeout_s=2):
        """Send one frame. Returns True on success.

        Only HOLD_FOREVER is honoured (over DNRGB); any other timeout is the
        board's own realtime timeout.
        """
        if timeout_s == HOLD_FOREVER:
            return self._via_control(self._control.send, pixels, HOLD_FOREVER)
        self._sequence = self._sequence % 15 + 1
        raw = memoryview(pixels.tobytes())
        step = MAX_LEDS_PER_PACKET * 3
        ok = True
        for offset in range(0, len(raw), step):
            body = raw[offset:offset + step]
            last = offset + step >= len(raw)
            if not self._sendto(header(offset, len(body), self._sequence, last) + bytes(body)):
                ok = False
        return ok

    def release(self):
        """Hand the board back to its own mode now (DNRGB timeout 0)."""
        return self._via_control(self._control.release)

    def _via_control(self, call, *args):
        ok = call(*args)
        self.last_error = self._control.last_error
        return ok

    def _sendto(self, packet):
        try:
            self._sock.sendto(packet, (hostcache.BOARD.address(self.host), self.port))
            self.last_error = None
            return True
        except OSError as exc:
            # Same as the DNRGB output: a dark board must not kill the frame
            # loop, and its address may be stale.
            hostcache.BOARD.mark_stale(self.host)
            self.last_error = f"{self.host}:{self.port} {exc}"
            return False

    def close(self):
        self._sock.close()
        self._control.close()
