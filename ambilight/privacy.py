"""What the hub keeps on disk stays private to the Mac's user, and only as long as needed.

The screen itself is never written: frames live in memory only, and what leaves
the hub is per-LED colour. What is written (settings and their journal, restore
points, the log, colour recordings someone asked for, the webcam calibration's
picture of the room) is readable by this account alone.
"""
import logging
import os
import stat
from pathlib import Path

PRIVATE_UMASK = 0o077       # new files 0600, new folders 0700
KEEP_RECORDINGS = 10        # colour recordings kept; older ones are deleted when a new one is saved

log = logging.getLogger(__name__)


def lock_down(config_path, recordings_dir, streams=()):
    """Make every file the hub writes from now on private, and tighten the ones it already wrote.

    `streams` are open files such as the log launchd hands the hub as stdout.
    Returns the paths whose permissions were tightened.
    """
    os.umask(PRIVATE_UMASK)
    config_path = Path(config_path)
    journal = config_path.with_suffix(".journal.jsonl")
    files = [config_path, journal, Path(f"{journal}.1")]
    folders = [config_path.parent / "backups", Path(recordings_dir)]
    for folder in folders:
        if folder.is_dir():
            files.append(folder)
            files.extend(path for path in folder.iterdir() if path.is_file() and not path.is_symlink())
    tightened = [path for path in files if _restrict(path)]
    for stream in streams:
        try:
            fd = stream.fileno()
            if stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_mode & 0o077:
                os.fchmod(fd, 0o600)
        except (OSError, ValueError, AttributeError):
            pass    # a terminal or a pipe: nothing on disk to protect
    return tightened


def _restrict(path):
    """Owner-only permissions on `path` if it exists and others can read it. True when changed."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode) or not mode & 0o077:
        return False
    try:
        os.chmod(path, 0o700 if stat.S_ISDIR(mode) else 0o600)
    except OSError as exc:
        log.warning("could not make %s private: %s", path, exc)
        return False
    return True


def prune_recordings(folder, keep=KEEP_RECORDINGS):
    """Delete all but the newest `keep` colour recordings (rec-*.npz). Returns the names deleted."""
    recordings = sorted(Path(folder).glob("rec-*.npz"), reverse=True)    # names carry the time
    gone = []
    for old in recordings[keep:]:
        try:
            old.unlink()
            gone.append(old.name)
        except OSError as exc:
            log.warning("could not delete old recording %s: %s", old, exc)
    return gone
