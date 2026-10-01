#!/usr/bin/env python3
"""Ambilight Hub -- screen-synced LED lighting for WLED, controlled from a phone.

    python ambilight.py

Home LAN only. Never forward the web port to the internet.
"""
import signal
import sys
import threading

from ambilight.config import Config
from ambilight.engine import Engine
from ambilight.server import Hub, lan_ip, serve
from ambilight.wizard import Wizard
from ambilight.wled import WLEDClient


def compatibility_check(config):
    """Report which plugins are usable. Missing extras fall back silently."""
    from ambilight.plugins.capture_mss import MSSCapture
    from ambilight.plugins.output_wled_udp import WLEDUDPOutput

    print("Plugin check:")
    rows = [
        ("capture", MSSCapture.name, MSSCapture.available(), "baseline"),
        ("capture", "macos-native", False, "not built yet -- using mss"),
        ("output", WLEDUDPOutput.name, WLEDUDPOutput.available(), "baseline"),
        ("output", "usb-serial", False, "not built yet -- using UDP"),
    ]
    for kind, name, ok, note in rows:
        print(f"  [{'x' if ok else ' '}] {kind:<8} {name:<16} {note}")
    if not MSSCapture.available():
        print("\nFATAL: mss is not installed. Run: python3 -m pip install mss numpy")
        sys.exit(1)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    config = Config()
    compatibility_check(config)

    engine = Engine(config)
    wled = WLEDClient(config)
    wizard = Wizard(config, engine)
    hub = Hub(config, engine, wled, wizard)

    try:
        server = serve(hub)
    except OSError as exc:
        print(f"\nFATAL: cannot bind port {config.get('http_port')}: {exc}")
        print("Another copy may already be running, or pick a different port.")
        sys.exit(1)

    url = f"http://{lan_ip()}:{config.get('http_port')}"
    print(f"\n  Phone control page:  {url}")
    print(f"  WLED target:         {config.get('wled_ip')}:{config.get('wled_port')}")
    if config.get("pin"):
        print(f"  PIN is on -- append ?pin=… to the URL")
    print("\nHome LAN only. Ctrl-C to stop.\n")

    if config.get("sync_on_start"):
        engine.start()

    stopping = threading.Event()

    def shutdown(signum, frame):
        if stopping.is_set():
            return
        stopping.set()
        print("\nStopping -- releasing LEDs back to WLED…")
        engine.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.serve_forever()
    finally:
        if not stopping.is_set():
            engine.stop()
        server.server_close()
        print("Stopped.")


if __name__ == "__main__":
    main()
