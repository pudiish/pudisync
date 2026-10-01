"""Plugin interfaces. Hardware-specific parts stay swappable behind these."""
from abc import ABC, abstractmethod


class CapturePlugin(ABC):
    """Grabs a horizontal band of the screen as an RGB array."""

    name = "abstract-capture"

    @staticmethod
    def available():
        """True when this plugin's dependencies and permissions are present."""
        return False

    @abstractmethod
    def open(self, monitor_index, band_height):
        """Prepare for capture. Returns (width, height) of the capture region."""

    @abstractmethod
    def grab(self):
        """Return an (h, w, 3) uint8 RGB array of the band, or None on failure."""

    def close(self):
        pass


class OutputPlugin(ABC):
    """Pushes an (n, 3) uint8 LED array to the lights."""

    name = "abstract-output"

    @staticmethod
    def available():
        return False

    @abstractmethod
    def send(self, pixels, timeout_s):
        """Send one frame. timeout_s is the per-packet hold time WLED honours."""

    def release(self):
        """Hand control back to the device's normal mode."""

    def close(self):
        pass
