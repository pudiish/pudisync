#!/usr/bin/env python3
"""Soak test: run a sandbox hub for hours and check memory stays flat and fps steady.

    python3 tools/soak.py --hours 4
    python3 tools/soak.py --dry-run         # the same checks over one minute

It starts its own fake WLED and a sandbox hub on its own ports with a
throwaway config, so it never touches the live hub on 8080 or real LEDs. Ports
follow the tests: AMBILIGHT_TEST_PORT_BASE (or --port-base) for the hub, +1
for the fake board's HTTP, +12601 for its UDP. The hub captures the real
screen, so the display is held awake for the run; time spent locked or asleep
is left out of the verdict.

Every sample is written as one JSON line, so a long run can be read back while
it is still going. Exit code 0 means every check passed.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ambilight.engine import KeepAwake  # noqa: E402
from ambilight.telemetry import STAGES, percentile  # noqa: E402

LIVE_PORT_BASES = (8079, 8080)      # either would put a port on the live hub's 8080
WARMUP_SHARE, WARMUP_MAX_S = 0.2, 600
MIN_SAMPLES = 5
GROWTH_MB = 20.0            # resident memory may rise this much after warm-up, no more
LEAK_MB_PER_HOUR = 5.0
SLOPE_MIN_SPAN_S = 1800     # a shorter run cannot tell a leak from warm-up noise
DELIVERED_SHARE = 0.95      # of the target fps, in the slowest 5% of samples
FRESH_SHARE = 0.5           # of the run's median capture fps, in the slowest 5%
DROPPED_SHARE = 0.01
PROGRESS_EVERY_S = 60


def ends(values):
    """Medians of the first and last fifth: macOS swaps pages in and out of
    the memory compressor, so single resident-size readings swing by tens of MB."""
    part = max(1, len(values) // 5)
    return statistics.median(values[:part]), statistics.median(values[-part:])


def ports(base):
    """(hub, fake board HTTP, fake board UDP) for a port base, laid out as the tests do."""
    if base in LIVE_PORT_BASES:
        raise SystemExit(f"port base {base} would land on the live hub's port 8080")
    return base, base + 1, base + 12601


def fetch(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


@contextmanager
def sandbox(base):
    """A fake WLED and a hub on their own ports; yields (hub URL, board URL, hub pid)."""
    hub_port, http_port, udp_port = ports(base)
    folder = Path(tempfile.mkdtemp(prefix="ambilight-soak-"))
    config = folder / "config.json"
    config.write_text(json.dumps({"wled_ip": "127.0.0.1", "wled_http_port": http_port,
                                  "wled_port": udp_port, "monitor_index": 0}))
    log = open(folder / "hub.log", "w")
    procs = [
        subprocess.Popen([sys.executable, "tools/fake_wled.py", "--udp-port", str(udp_port),
                          "--http-port", str(http_port), "--quiet"], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([sys.executable, "hub.py", "--config", str(config), "--port", str(hub_port),
                          "--no-bonjour"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT),
    ]
    hub_url = f"http://127.0.0.1:{hub_port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                fetch(hub_url + "/api/status", timeout=1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"sandbox hub did not start; see {log.name}") from None
                time.sleep(0.2)
        yield hub_url, f"http://127.0.0.1:{http_port}", procs[1].pid
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(timeout=5)
        log.close()


def cpu_seconds(text):
    """ps's cumulative CPU time ("[dd-][hh:]mm:ss.ss") in seconds."""
    days, _, clock = text.rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86400 if days else 0)


def process_usage(pid):
    """(CPU seconds so far, resident MB) for a process. Read from ps: psutil 5.8
    under-reports CPU time about 40x on Apple Silicon, and ps needs no package."""
    done = subprocess.run(["ps", "-o", "time=,rss=", "-p", str(pid)],
                          capture_output=True, text=True, check=True)
    cpu, rss_kb = done.stdout.split()
    return cpu_seconds(cpu), int(rss_kb) / 1024


def sample(hub_url, board_url, pid, started):
    status = fetch(hub_url + "/api/status")
    board = fetch(board_url + "/stats")
    cpu, rss = process_usage(pid)
    timing = status["telemetry"]
    return {"t": round(time.monotonic() - started, 2), "rss_mb": round(rss, 1), "cpu_s": cpu,
            "paused": bool(status["paused"] or status["last_error"]),
            "fresh_fps": status["measured_fps"], "delivered_fps": timing["delivered_fps"],
            "delivered": timing["delivered"], "dropped": timing["dropped"], "packets": board["packets"],
            "latency_ms": timing["latency_ms"], "stages_ms": timing["stages_ms"]}


def run(base, seconds, every, layout, fps, out, log=print):
    """Sync for `seconds` on a sandbox hub, sampling every `every` s. Returns the samples."""
    samples = []
    with sandbox(base) as (hub_url, board_url, pid), open(out, "w") as fh:
        fetch(f"{hub_url}/api/set?layout={layout}&fps={fps}")
        fetch(hub_url + "/api/sync/on")
        time.sleep(2.0)     # past the first frames and the smoother's fade-in
        started = time.monotonic()
        next_progress = PROGRESS_EVERY_S
        while True:
            samples.append(sample(hub_url, board_url, pid, started))
            fh.write(json.dumps(samples[-1]) + "\n")
            fh.flush()
            if samples[-1]["t"] >= next_progress:
                next_progress += PROGRESS_EVERY_S
                log(f"  {samples[-1]['t'] / 60:.0f} min: {samples[-1]['delivered_fps']} fps delivered, "
                    f"{samples[-1]['fresh_fps']} fresh, {samples[-1]['rss_mb']} MB")
            if samples[-1]["t"] >= seconds:
                break
            time.sleep(every)
        fetch(hub_url + "/api/sync/off")
    return samples


def judge(samples, target_fps):
    """[(check, passed, detail)] for a run. Warm-up and paused samples are left out."""
    if not samples:
        return [("enough samples to judge", False, "no samples")]
    span = samples[-1]["t"] - samples[0]["t"]
    warm = samples[0]["t"] + min(WARMUP_MAX_S, WARMUP_SHARE * span)
    kept = [s for s in samples if s["t"] >= warm and not s["paused"]]
    paused = sum(1 for s in samples if s["paused"])
    checks = [("enough samples to judge", len(kept) >= MIN_SAMPLES, f"{len(kept)} judged, {paused} paused")]
    if len(kept) < MIN_SAMPLES:
        return checks

    rss = [s["rss_mb"] for s in kept]
    start, end = ends(rss)
    checks.append(("memory does not grow", end - start <= GROWTH_MB,
                   f"{start:.1f} -> {end:.1f} MB after warm-up"))
    if kept[-1]["t"] - kept[0]["t"] >= SLOPE_MIN_SPAN_S:
        rate = statistics.linear_regression([s["t"] for s in kept], rss).slope * 3600
        checks.append(("memory stays flat", rate <= LEAK_MB_PER_HOUR, f"{rate:+.1f} MB/h"))

    delivered = percentile([s["delivered_fps"] for s in kept], 0.05)
    checks.append(("delivered fps holds the target", delivered >= DELIVERED_SHARE * target_fps,
                   f"p5 {delivered:.1f} of {target_fps}"))
    fresh = [s["fresh_fps"] for s in kept]
    low, typical = percentile(fresh, 0.05), statistics.median(fresh)
    checks.append(("fresh frames never collapse", low >= FRESH_SHARE * typical,
                   f"p5 {low:.1f}, median {typical:.1f}"))
    dropped = kept[-1]["dropped"] - kept[0]["dropped"]
    due = dropped + kept[-1]["delivered"] - kept[0]["delivered"]
    share = dropped / due if due else 0.0
    checks.append(("dropped frames stay rare", share <= DROPPED_SHARE, f"{dropped} of {due} ({share:.2%})"))
    return checks


def summary(samples):
    """The run's numbers as printable lines."""
    first, last = samples[0], samples[-1]
    span = max(last["t"] - first["t"], 1e-9)
    kept = [s for s in samples if not s["paused"]] or samples

    def spread(values, unit):
        values = [v for v in values if v is not None]
        if not values:
            return "--"
        return f"median {statistics.median(values):.1f}{unit}   p5 {percentile(values, 0.05):.1f}{unit}"

    def timing(pick):
        p50 = [pick(s)["p50"] for s in kept if pick(s)["p50"] is not None]
        p95 = [pick(s)["p95"] for s in kept if pick(s)["p95"] is not None]
        if not p50:
            return "--"
        return f"p50 {statistics.median(p50):.1f} ms   p95 {statistics.median(p95):.1f} ms"

    dropped = last["dropped"] - first["dropped"]
    due = dropped + last["delivered"] - first["delivered"]
    lines = [
        f"  run              {span:.0f} s, {len(samples)} samples ({len(samples) - len(kept)} paused)",
        f"  fresh frames     {spread([s['fresh_fps'] for s in kept], ' fps')}",
        f"  delivered        {spread([s['delivered_fps'] for s in kept], ' fps')}",
        f"  board packets    {(last['packets'] - first['packets']) / span:.1f} /s",
        f"  dropped          {dropped} of {due}",
        f"  hub CPU          {(last['cpu_s'] - first['cpu_s']) / span * 100:.0f}% of one core",
        "  hub memory       {:.1f} -> {:.1f} MB (medians of the first and last fifth)".format(
            *ends([s["rss_mb"] for s in samples])),
        f"  screen to send   {timing(lambda s: s['latency_ms'])}",
    ]
    lines += [f"  {name:<16} {timing(lambda s, n=name: s['stages_ms'][n])}" for name in STAGES]
    return lines


def main():
    ap = argparse.ArgumentParser(description="Run a sandbox hub for hours; check memory and fps hold.")
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--every", type=float, default=10.0, help="seconds between samples (default %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="the same checks over one minute")
    ap.add_argument("--port-base", type=int, default=int(os.environ.get("AMBILIGHT_TEST_PORT_BASE", "8790")))
    ap.add_argument("--layout", choices=("frame", "strip"), default="frame",
                    help="frame (four edges, the heavier path) or strip (default %(default)s)")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--out", help="samples file, one JSON object per line (default: a new temp file)")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    seconds, every = (60.0, 2.0) if args.dry_run else (args.hours * 3600, args.every)
    out = args.out or os.path.join(tempfile.mkdtemp(prefix="ambilight-soak-"), "samples.jsonl")
    print(f"Soak: {seconds / 60:.0f} min, {args.layout} layout at {args.fps} fps, "
          f"sandbox on port {ports(args.port_base)[0]}; samples in {out}")
    awake = KeepAwake.start(display=True)
    try:
        samples = run(args.port_base, seconds, every, args.layout, args.fps, out)
    finally:
        if awake is not None:
            awake.stop()

    print("\n".join(summary(samples)))
    checks = judge(samples, args.fps)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  -- {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
