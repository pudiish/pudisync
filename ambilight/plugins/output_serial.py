"""USB-serial output: stock WLED's Adalight protocol over a USB cable.

Verified against https://kno.wled.ge/interfaces/serial/ :
  - Stock WLED accepts Adalight and TPM2 on serial with NO firmware change.
  - Default baud is 115200, which the docs say suits "about 50-100 LEDs".
    For ~240 LEDs the baud rate must be raised in WLED's Sync settings.
  - If GPIO1/GPIO3 are assigned to LED output, serial is unavailable.

Adalight frame:  'Ada' + hi + lo + checksum + RGB bytes
  hi/lo encode (led_count - 1); checksum = hi XOR lo XOR 0x55.

Bandwidth reality check (8 bits + start + stop = 10 bits per byte):
  240 LEDs = 726 bytes/frame. At 115200 baud that is ~15 fps -- too slow.
  At 921600 baud it is ~127 fps, so raise the baud rate for a strip this size.
"""
import glob

from . import OutputPlugin

HEADER_MAGIC = b"Ada"
# Ports that are never a microcontroller; hide them from the UI's picker.
_IGNORE = ("Bluetooth", "debug-console", "-WirelessiAP")


def list_ports():
    """USB serial devices that could be a microcontroller.

    Only USB-bridge chip patterns count. A loose /dev/cu.* fallback would match
    Bluetooth audio devices and claim a board is attached when none is.
    """
    found = []
    for pattern in ("/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB*",
                    "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        found.extend(glob.glob(pattern))
    return sorted(p for p in set(found) if not any(bad in p for bad in _IGNORE))


def required_baud(led_count, fps, margin=1.35):
    """Smallest standard baud rate that can carry this strip at this rate."""
    bits = (led_count * 3 + 6) * 10 * fps * margin
    for baud in (115200, 230400, 460800, 500000, 921600, 1000000, 2000000):
        if baud >= bits:
            return baud
    return 2000000


class SerialOutput(OutputPlugin):
    name = "wled-serial-adalight"

    @staticmethod
    def available():
        try:
            import serial  # noqa: F401
        except ImportError:
            return False
        return bool(list_ports())

    def __init__(self, port, baud=921600):
        import serial

        self.port = port
        self.baud = int(baud)
        self.last_error = None
        # write_timeout keeps a stalled cable from freezing the frame loop.
        self._ser = serial.Serial(port, self.baud, timeout=0, write_timeout=0.05)

    def send(self, pixels, timeout_s=2):
        """Adalight carries no timeout field; timeout_s is accepted and ignored."""
        count = len(pixels)
        hi, lo = ((count - 1) >> 8) & 0xFF, (count - 1) & 0xFF
        frame = HEADER_MAGIC + bytes((hi, lo, hi ^ lo ^ 0x55)) + pixels.tobytes()
        try:
            self._ser.write(frame)
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{self.port}: {exc}"
            return False

    def release(self):
        import numpy as np

        blank = np.zeros((1, 3), dtype=np.uint8)
        return self.send(blank)

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass
