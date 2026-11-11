"""Finding the hub on the LAN: Bonjour name and the addresses to open.

macOS ships Bonjour and its `dns-sd` tool, so advertising needs no extra
dependency. The Mac's `.local` name survives DHCP changes, unlike its IP.
"""
import logging
import shutil
import socket
import subprocess

log = logging.getLogger(__name__)

SERVICE_NAME = "Ambilight Hub"


def lan_ip():
    """Our address on the LAN, found without sending anything."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))  # reserved, never routed
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def local_name():
    """This Mac's Bonjour host name, e.g. ishwars-macbook-air.local."""
    name = socket.gethostname()
    return name if name.endswith(".local") else f"{name.split('.')[0]}.local"


def hub_urls(port):
    """Addresses another device can open, the stable name first."""
    return [f"http://{local_name()}:{port}", f"http://{lan_ip()}:{port}"]


def board_urls(host, port, known_ip=None):
    """Addresses of the LED board's own page, which works with the Mac off.

    The configured name comes first; the last address it resolved to follows,
    because the board's own .local name stops answering now and then.
    """
    suffix = "" if port == 80 else f":{port}"
    urls = [f"http://{host}{suffix}"]
    if known_ip and known_ip != host:
        urls.append(f"http://{known_ip}{suffix}")
    return urls


class Advertiser:
    """Advertises the web page as a Bonjour _http._tcp service while running."""

    def __init__(self, process):
        self.process = process

    @classmethod
    def start(cls, port):
        tool = shutil.which("dns-sd")
        if tool is None:
            log.info("dns-sd not found; the hub is reachable by address only")
            return None
        process = subprocess.Popen(
            [tool, "-R", SERVICE_NAME, "_http._tcp", "local", str(port), "path=/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return cls(process)

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
