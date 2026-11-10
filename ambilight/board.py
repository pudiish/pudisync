"""The control page that lives on the WLED board itself, and putting it there.

WLED serves /index.htm (or /index.htm.gz) from its own flash when it exists, so the page
loads whenever the strip has power, with the Mac off. The page talks to WLED's
JSON API on the same origin and offers "Monitor sync" as WLED preset N; the hub
on the Mac follows that choice (intent.py). Settings pages stay at /settings,
and deleting /index.htm in WLED's file editor (/edit) restores WLED's own page.
"""
import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "board" / "index.htm"
TIMEOUT = 20    # writing to flash makes the board slow to answer
SYNC_NAME = "Monitor sync"
# A plain, dim warm lamp: what the strip shows while the Mac is off or not yet
# streaming. Its colour must differ from the hub's fallback look (routes.py).
SYNC_LOOK = {"id": 0, "start": 0, "stop": 180, "fx": 0, "sx": 128, "ix": 128, "pal": 0,
             "col": [[40, 26, 10], [0, 0, 0], [0, 0, 0]]}


def request(host, path, data=None, headers=None):
    req = urllib.request.Request(f"http://{host}{path}", data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def delete(host, name):
    """Remove a file from the board's flash (what WLED's /edit page does)."""
    request(host, "/edit?" + urllib.parse.urlencode({"func": "delete", "path": name}))


def get_json(host, path):
    return json.loads(request(host, path))


def multipart(name, data):
    """A one-file multipart/form-data body, field "file", as WLED's /upload expects."""
    boundary = uuid.uuid4().hex
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{name}"\r\nContent-Type: text/html\r\n\r\n').encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def upload(host, name, data):
    body, content_type = multipart(name, data)
    request(host, "/upload", body, {"Content-Type": content_type})


def sync_preset_payload(preset_id):
    # ib=False: the preset must not carry brightness. Loading it would reset the
    # user's brightness, and putting it back is a manual change, which clears the
    # preset and with it "monitor sync".
    return {"on": True, "seg": [SYNC_LOOK], "psave": int(preset_id),
            "n": SYNC_NAME, "ib": False, "sb": True}


def save_sync_preset(host, preset_id):
    payload = json.dumps(sync_preset_payload(preset_id)).encode()
    request(host, "/json/state", payload, {"Content-Type": "application/json"})
    saved = get_json(host, "/presets.json").get(str(preset_id)) or {}
    return saved.get("n") == SYNC_NAME


def deploy(host, preset_id=1, page=PAGE):
    """Put the page and the sync preset on the board. Returns what was verified."""
    if get_json(host, "/json/info").get("live"):
        raise RuntimeError("the board is receiving the screen stream; turn sync off "
                           "first, then deploy (it writes to the board's flash)")
    html = Path(page).read_bytes()
    packed = gzip.compress(html, 9, mtime=0)
    # Compressed, the page is a third of the size and loads about three times
    # faster from the board. WLED prefers a plain /index.htm when both exist,
    # so the plain copy must go.
    upload(host, "/index.htm.gz", packed)
    try:
        delete(host, "/index.htm")
    except urllib.error.HTTPError:
        pass    # nothing to delete on a first install
    served = gzip.decompress(request(host, "/", headers={"Accept-Encoding": "gzip"}))
    preset_ok = save_sync_preset(host, preset_id)
    return {"page_ok": served == html, "preset_ok": preset_ok,
            "bytes": len(packed), "plain_bytes": len(html)}
