#!/usr/bin/env python3
"""Ambilight Hub -- screen-synced LED lighting for WLED, controlled from a phone.

    python ambilight.py                       # uses config.json next to this file
    python ambilight.py --config other.json --port 8790 --no-bonjour
    python ambilight.py --deploy-board        # install the phone page on the WLED board

Home LAN only. Never forward the web port to the internet.
"""
import argparse
import logging
import signal
import sys
import threading

from ambilight import board
from ambilight.config import CONFIG_PATH, Config
from ambilight.discovery import Advertiser, hub_urls
from ambilight.engine import Engine
from ambilight.plugins import CAPTURES, OUTPUTS
from ambilight.server import Hub, serve
from ambilight.wizard import Wizard
from ambilight.wled import WLEDClient


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Screen-synced LED lighting for WLED.")
    ap.add_argument("--config", default=str(CONFIG_PATH), help="config file to use")
    ap.add_argument("--port", type=int, help="web port for this run (not saved)")
    ap.add_argument("--no-bonjour", action="store_true",
                    help="do not advertise the hub on the LAN (test instances)")
    ap.add_argument("--deploy-board", action="store_true",
                    help="install the control page and sync preset on the WLED board, then exit")
    return ap.parse_args(argv)


def deploy_board(config):
    """Put the phone page on the board. Needs the screen stream off."""
    host = config.get("wled_ip")
    try:
        result = board.deploy(host, config.get("sync_preset"))
    except (OSError, RuntimeError) as exc:
        print(f"Could not install the page on {host}: {exc}")
        return 1
    print(f"Installed on {host}: page {result['bytes']} bytes "
          f"({result['plain_bytes']} before compression), "
          f"{'verified' if result['page_ok'] else 'NOT verified'}; "
          f"sync preset {'saved' if result['preset_ok'] else 'NOT saved'}.")
    return 0 if result["page_ok"] and result["preset_ok"] else 1


def compatibility_check():
    """Report which plugins are usable; without a capture source nothing can run."""
    print("Plugin check:")
    for kind, registry in (("capture", CAPTURES), ("output", OUTPUTS)):
        for key, plugin in registry.items():
            print(f"  [{'x' if plugin.available() else ' '}] {kind:<8} {key:<8} {plugin.name}")
    if not any(plugin.available() for plugin in CAPTURES.values()):
        print("\nFATAL: no capture source. Run: python3 -m pip install -r requirements.txt")
        sys.exit(1)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(sys.argv[1:])
    config = Config(args.config)
    if args.deploy_board:
        sys.exit(deploy_board(config))
    compatibility_check()

    engine = Engine(config)
    wled = WLEDClient(config)
    wizard = Wizard(config, engine)
    port = args.port or config.get("http_port")
    hub = Hub(config, engine, wled, wizard, port)

    try:
        server = serve(hub, port)
    except OSError as exc:
        print(f"\nFATAL: cannot bind port {port}: {exc}")
        print("Another copy may already be running, or pick a different port.")
        sys.exit(1)

    advertiser = None if args.no_bonjour else Advertiser.start(port)
    print("\n  Open the control page from any device on this network:")
    for url in hub_urls(port):
        print(f"    {url}")
    print(f"  WLED target: {config.get('wled_ip')}:{config.get('wled_port')}")
    if config.get("pin"):
        print("  PIN is on -- the page will ask for it")
    print("\nHome LAN only. Ctrl-C to stop.\n")

    if config.get("sync_on_start"):
        engine.start()

    stopping = threading.Event()
    hub.intent.start(stopping)      # follow the board's "Monitor sync" choice

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
        if advertiser is not None:
            advertiser.stop()
        server.server_close()
        print("Stopped.")


if __name__ == "__main__":
    main()
