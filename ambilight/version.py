"""Which build is running: the VERSION file, plus the git commit when there is one."""
import functools
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text().strip()


@functools.cache
def build_id():
    """VERSION+commit, like 2.0.0-dev+193d10f; just VERSION outside a git checkout."""
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return VERSION
    commit = done.stdout.strip() if done.returncode == 0 else ""
    return f"{VERSION}+{commit}" if commit else VERSION
