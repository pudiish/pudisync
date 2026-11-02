"""Plugin interfaces and registries. Hardware-specific parts stay swappable.

Adding a capture source or an output transport is one module plus one entry
in CAPTURES / OUTPUTS below; the engine, diagnostics and config validation all
read these registries.
"""
from abc import ABC, abstractmethod


class CapturePlugin(ABC):
    """Grabs the screen as RGB arrays."""

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
        """Return an (h, w, 3) uint8 RGB array of the bottom band, or None on failure."""

    @abstractmethod
    def grab_full(self):
        """Return an (h, w, 3) uint8 RGB array of the whole monitor."""

    @abstractmethod
    def grab_search(self, rows):
        """Return a taller strip from the screen bottom, for finding letterbox bars."""

    @abstractmethod
    def set_inset(self, rows):
        """Lift the bottom band `rows` off the screen edge, to clear letterbox bars."""

    def close(self):
        pass


class OutputPlugin(ABC):
    """Pushes an (n, 3) uint8 LED array to the lights."""

    name = "abstract-output"

    @staticmethod
    def available():
        return False

    @classmethod
    @abstractmethod
    def from_config(cls, cfg):
        """Build the output a config snapshot asks for."""

    @staticmethod
    @abstractmethod
    def key(cfg):
        """The config values this output depends on; a change means rebuild."""

    @abstractmethod
    def send(self, pixels, timeout_s):
        """Send one frame. timeout_s is the per-packet hold time WLED honours."""

    def release(self):
        """Hand control back to the device's normal mode."""

    def close(self):
        pass


# Registries come last: the plugin modules import the base classes above.
from .capture_mss import MSSCapture  # noqa: E402
from .capture_quartz import QuartzCapture  # noqa: E402
from .output_serial import SerialOutput  # noqa: E402
from .output_wled_udp import WLEDUDPOutput  # noqa: E402

# Order is preference: the engine uses the first one that is available.
CAPTURES = {"quartz": QuartzCapture, "mss": MSSCapture}
OUTPUTS = {"udp": WLEDUDPOutput, "serial": SerialOutput}
