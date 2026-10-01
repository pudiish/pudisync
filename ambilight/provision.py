"""Flash WLED onto an ESP board and hand it Wi-Fi credentials over USB.

Two independent pieces:

1. Flashing -- shells out to `esptool`, which is the only maintained way to talk
   to the ESP ROM bootloader. Binaries come from WLED's official GitHub releases.

2. Improv Serial -- WLED supports it, so Wi-Fi credentials go over the USB cable
   and the board joins your network without the WLED-AP hotspot dance.

Improv wire format, verified against https://www.improv-wifi.com/serial/ and
ESPHome's reference implementation (improv_serial_component.cpp):

    "IMPROV" | version(0x01) | type | length | data... | checksum | '\\n'

    checksum = (sum of every preceding byte) & 0xFF

    type 0x01 current state     0x02 error state
    type 0x03 RPC command       0x04 RPC result
    RPC 0x01 send Wi-Fi settings    0x02 request current state
    state 0x02 ready   0x03 provisioning   0x04 provisioned
"""
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

try:                    # python.org builds often lack system CA certificates
    import certifi
    import ssl

    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = None

IMPROV_HEADER = b"IMPROV"
IMPROV_VERSION = 0x01
TYPE_CURRENT_STATE = 0x01
TYPE_ERROR_STATE = 0x02
TYPE_RPC = 0x03
TYPE_RPC_RESULT = 0x04
RPC_SEND_WIFI = 0x01
RPC_REQUEST_STATE = 0x02

STATE_READY = 0x02
STATE_PROVISIONING = 0x03
STATE_PROVISIONED = 0x04

IMPROV_ERRORS = {
    0x00: "no error",
    0x01: "the board rejected the packet",
    0x02: "the board does not support this command",
    0x03: "could not connect -- check the Wi-Fi name and password",
    0x05: "bad hostname",
    0xFF: "unknown error on the board",
}

WLED_RELEASES = "https://api.github.com/repos/wled/WLED/releases/latest"
# Chip id (as esptool reports it) -> the substring that marks the right binary.
BINARY_FOR_CHIP = {
    "esp8266": "ESP01",      # the generic 1MB+ ESP8266 build
    "esp32": "ESP32",
    "esp32-s2": "ESP32-S2",
    "esp32-s3": "ESP32-S3",
    "esp32-c3": "ESP32-C3",
}


def _checksum(payload):
    return sum(payload) & 0xFF


def build_packet(ptype, data):
    body = bytearray(IMPROV_HEADER)
    body.append(IMPROV_VERSION)
    body.append(ptype)
    body.append(len(data))
    body.extend(data)
    body.append(_checksum(body))
    body.append(ord("\n"))
    return bytes(body)


def wifi_packet(ssid, password):
    """RPC command carrying the credentials, each as a length-prefixed string."""
    ssid_b, pw_b = ssid.encode(), password.encode()
    payload = bytearray([RPC_SEND_WIFI])
    inner = bytearray()
    inner.append(len(ssid_b))
    inner.extend(ssid_b)
    inner.append(len(pw_b))
    inner.extend(pw_b)
    payload.append(len(inner))
    payload.extend(inner)
    return build_packet(TYPE_RPC, payload)


def parse_packets(buffer):
    """Pull complete Improv packets out of a byte buffer. Returns (packets, rest)."""
    out = []
    while True:
        start = buffer.find(IMPROV_HEADER)
        if start < 0:
            # Keep a tail in case a header straddles two reads.
            return out, buffer[-len(IMPROV_HEADER):] if buffer else b""
        if len(buffer) < start + 9:
            return out, buffer[start:]
        ptype = buffer[start + 7]
        length = buffer[start + 8]
        end = start + 9 + length + 1
        if len(buffer) < end:
            return out, buffer[start:]
        data = buffer[start + 9:start + 9 + length]
        got = buffer[start + 9 + length]
        want = _checksum(buffer[start:start + 9 + length])
        out.append({"type": ptype, "data": bytes(data), "valid": got == want})
        buffer = buffer[end:]


def esptool_available():
    if shutil.which("esptool.py") or shutil.which("esptool"):
        return True
    try:
        subprocess.run([sys.executable, "-m", "esptool", "version"],
                       capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def _esptool_cmd():
    for exe in ("esptool.py", "esptool"):
        if shutil.which(exe):
            return [exe]
    return [sys.executable, "-m", "esptool"]


def detect_chip(port):
    """Ask the ROM bootloader what it is. Returns (chip_id, raw_output)."""
    cmd = _esptool_cmd() + ["--port", port, "chip_id"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return None, str(exc)
    text = proc.stdout + proc.stderr
    match = re.search(r"Detecting chip type\.*\s*(ESP[\w-]*)", text, re.I)
    if not match:
        match = re.search(r"Chip is (ESP[\w-]*)", text, re.I)
    return (match.group(1).lower() if match else None), text


def list_firmware():
    """Official WLED release assets, newest release."""
    with urllib.request.urlopen(WLED_RELEASES, timeout=15, context=_SSL) as resp:
        release = json.loads(resp.read())
    builds = [
        {"name": a["name"], "url": a["browser_download_url"], "size": a["size"]}
        for a in release.get("assets", [])
        if a["name"].endswith(".bin")
    ]
    return {"version": release.get("tag_name"), "builds": builds}


def pick_binary(chip, builds):
    """Choose the release asset matching this chip exactly.

    A plain substring match is unsafe here: "ESP32" also matches ESP32-C3 and
    ESP32-S3 assets, and flashing an S3 image to a plain ESP32 bricks it. So the
    chip token must be followed by a separator, never another variant suffix.
    """
    want = BINARY_FOR_CHIP.get(chip)
    if not want:
        return None
    # WLED names assets like WLED_16.0.1_ESP32.bin / _ESP32-C3-QIO.bin. A plain
    # ESP32 must NOT match _ESP32-C3, so a hyphen-variant suffix is disqualifying.
    token = re.escape(want)
    exact = re.compile(rf"_{token}(?!-?(?:C\d|S\d))(?:[._]|$)", re.I)
    matches = [b for b in builds if exact.search(b["name"].rsplit(".bin", 1)[0] + "_")]
    if not matches:
        return None
    # Prefer the plain build over compat/160MHz/ethernet/audio variants.
    plain = [b for b in matches
             if not re.search(r"(compat|_160|eth|audioreactive|_4m|_16mb|opi|qio)",
                              b["name"], re.I)]
    return (plain or matches)[0]


class Provisioner:
    """Runs flash + provision in the background so the HTTP server stays free."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.state = "idle"       # idle | working | done | error
            self.step = ""
            self.progress = 0
            self.error = None
            self.detail = []
            self.chip = None
            self.ip = None

    def _set(self, step, progress, **kw):
        with self._lock:
            self.step = step
            self.progress = progress
            for key, value in kw.items():
                setattr(self, key, value)
            self.detail.append(step)
            self.detail = self.detail[-40:]

    def status(self):
        with self._lock:
            return {
                "state": self.state, "step": self.step, "progress": self.progress,
                "error": self.error, "chip": self.chip, "ip": self.ip,
                "log": list(self.detail),
            }

    def start(self, port, ssid, password, flash=True):
        with self._lock:
            if self.state == "working":
                raise RuntimeError("a flash is already running")
        self.reset()
        with self._lock:
            self.state = "working"
        thread = threading.Thread(target=self._run, args=(port, ssid, password, flash),
                                  daemon=True, name="provision")
        thread.start()

    def _run(self, port, ssid, password, flash):
        try:
            if flash:
                self._set("Looking for the board", 5)
                chip, raw = detect_chip(port)
                if not chip:
                    raise RuntimeError(
                        "No ESP board answered on that port. Hold the BOOT/FLASH "
                        "button while plugging it in, then try again.")
                self._set(f"Found {chip}", 15, chip=chip)

                self._set("Fetching the WLED firmware list", 22)
                release = list_firmware()
                binary = pick_binary(chip, release["builds"])
                if not binary:
                    raise RuntimeError(f"No official WLED build matches {chip}")
                self._set(f"Downloading {binary['name']}", 30)
                path = f"/tmp/{binary['name']}"
                with urllib.request.urlopen(binary["url"], timeout=120,
                                            context=_SSL) as src:
                    with open(path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                self._set("Erasing the board", 42)
                self._run_esptool(["--port", port, "erase_flash"], timeout=120)

                self._set(f"Writing WLED {release['version']}", 55)
                self._run_esptool(
                    ["--port", port, "--baud", "460800", "write_flash", "0x0", path],
                    timeout=300)
                self._set("Firmware written, waiting for reboot", 75)
                time.sleep(5)

            self._set("Sending your Wi-Fi details over USB", 82)
            ip = self._improv(port, ssid, password)
            self._set(f"Board joined your network at {ip}", 100, ip=ip)
            with self._lock:
                self.state = "done"
        except Exception as exc:
            with self._lock:
                self.state = "error"
                self.error = str(exc)
                self.detail.append(f"failed: {exc}")

    def _run_esptool(self, args, timeout):
        cmd = _esptool_cmd() + args
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            raise RuntimeError("esptool failed: " + " / ".join(tail))

    def _improv(self, port, ssid, password, timeout=45):
        """Hand credentials to the board and wait for it to report an address."""
        import serial

        with serial.Serial(port, 115200, timeout=0.4) as ser:
            time.sleep(2.0)                     # let WLED finish booting
            ser.reset_input_buffer()
            ser.write(build_packet(TYPE_RPC, bytes([RPC_REQUEST_STATE, 0])))
            ser.flush()
            time.sleep(0.6)
            ser.write(wifi_packet(ssid, password))
            ser.flush()

            buffer = b""
            deadline = time.time() + timeout
            while time.time() < deadline:
                chunk = ser.read(256)
                if chunk:
                    buffer += chunk
                    packets, buffer = parse_packets(buffer)
                    for pkt in packets:
                        if not pkt["valid"]:
                            continue
                        if pkt["type"] == TYPE_ERROR_STATE and pkt["data"]:
                            code = pkt["data"][0]
                            if code:
                                raise RuntimeError(IMPROV_ERRORS.get(
                                    code, f"board error 0x{code:02x}"))
                        if pkt["type"] == TYPE_CURRENT_STATE and pkt["data"]:
                            if pkt["data"][0] == STATE_PROVISIONING:
                                self._set("Board is connecting to Wi-Fi", 90)
                        if pkt["type"] == TYPE_RPC_RESULT and pkt["data"]:
                            # Result payload carries the device URL, e.g. http://1.2.3.4
                            text = pkt["data"].decode("utf-8", "ignore")
                            found = re.search(r"(\d+\.\d+\.\d+\.\d+)", text)
                            if found:
                                return found.group(1)
                time.sleep(0.05)
        raise RuntimeError(
            "The board did not confirm a Wi-Fi connection. Check the network name "
            "and password, and that it is a 2.4GHz network -- ESP8266 cannot use 5GHz.")


# --- Wi-Fi network discovery ------------------------------------------------
# macOS removed the `airport` binary that used to do live scans, so we offer the
# networks this Mac already remembers. The target network is almost certainly
# among them, since the Mac is on it.
_FIVE_GHZ_HINT = re.compile(r"(^|[^a-z0-9])5\s*-?\s*(g|ghz)([^a-z0-9]|$)", re.I)


def _wifi_interfaces():
    try:
        out = subprocess.run(["/usr/sbin/networksetup", "-listallhardwareports"],
                             capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return []
    interfaces, want = [], False
    for line in out.splitlines():
        if "Wi-Fi" in line or "AirPort" in line:
            want = True
        elif want and line.startswith("Device:"):
            interfaces.append(line.split(":", 1)[1].strip())
            want = False
    return interfaces


def current_network():
    """The SSID this Mac is on right now, or None if it is not on Wi-Fi."""
    for iface in _wifi_interfaces():
        try:
            out = subprocess.run(
                ["/usr/sbin/networksetup", "-getairportnetwork", iface],
                capture_output=True, text=True, timeout=8).stdout.strip()
        except Exception:
            continue
        if ":" in out and "not associated" not in out.lower():
            return out.split(":", 1)[1].strip()
    return None


def known_networks():
    """Networks this Mac remembers, current one first.

    Each entry is flagged if its name suggests 5GHz -- an ESP8266 cannot join
    those, and it is the most common reason provisioning fails.
    """
    names = []
    for iface in _wifi_interfaces():
        try:
            out = subprocess.run(
                ["/usr/sbin/networksetup", "-listpreferredwirelessnetworks", iface],
                capture_output=True, text=True, timeout=8).stdout
        except Exception:
            continue
        for line in out.splitlines()[1:]:
            ssid = line.strip()
            if ssid and ssid not in names:
                names.append(ssid)

    current = current_network()
    if current and current in names:
        names.remove(current)
    ordered = ([current] if current else []) + names
    return {
        "current": current,
        "networks": [{"ssid": n, "likely_5ghz": bool(_FIVE_GHZ_HINT.search(n)),
                      "current": n == current}
                     for n in ordered],
    }
