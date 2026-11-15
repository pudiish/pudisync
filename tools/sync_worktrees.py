#!/usr/bin/env python3
"""Show how far every worktree is from the shared branch, and fast-forward the safe ones.

    python tools/sync_worktrees.py            # fetch, then report only
    python tools/sync_worktrees.py --apply    # also fast-forward clean checkouts of the shared branch
    python tools/sync_worktrees.py --no-fetch # work from what is already fetched

Several sessions work in their own worktrees at once, so this only ever does the
one thing nobody can object to: `merge --ff-only` on a clean checkout of the
shared branch. Everything else (a workstream branch that fell behind, a diverged
branch, unpushed commits) is reported for the session that owns it. It never
pushes, rebases, resets or touches `main`, and it skips any tree with
uncommitted work.
"""
import argparse
import subprocess
import sys

BASE = "feature/color-engine"
REMOTE = "origin"


def decide(tree):
    """(action, note) for one worktree. Only "fast-forward" ever changes anything."""
    branch = tree["branch"]
    if branch is None:
        return "skip", "detached HEAD"
    if branch == "main":
        return "skip", "main is never touched"
    if tree["dirty"]:
        return "skip", "uncommitted work"
    if branch == BASE:
        if tree["behind_base"] and tree["ahead_base"]:
            return "report", f"diverged from {REMOTE}/{BASE} ({tree['ahead_base']} ahead, {tree['behind_base']} behind)"
        if tree["behind_base"]:
            return "fast-forward", f"{tree['behind_base']} behind {REMOTE}/{BASE}"
        if tree["ahead_base"]:
            return "report", f"{tree['ahead_base']} commit(s) not pushed"
        return "ok", "level with origin"
    notes = []
    if tree["behind_base"]:
        notes.append(f"{tree['behind_base']} behind {BASE}: merge or rebase in its own session")
    if tree["ahead_upstream"]:
        notes.append(f"{tree['ahead_upstream']} commit(s) not pushed")
    return ("report", "; ".join(notes)) if notes else ("ok", "level")


def git(*args, cwd=None, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def counts(cwd, left, right):
    """(commits only in `left`, commits only in `right`), or (0, 0) when `right` does not exist."""
    out = subprocess.run(["git", "rev-list", "--left-right", "--count", f"{left}...{right}"],
                         cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        return 0, 0
    ahead, behind = out.stdout.split()
    return int(ahead), int(behind)


def worktrees():
    paths = []
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            paths.append(line[len("worktree "):])
    return paths


def inspect(path):
    branch = git("branch", "--show-current", cwd=path) or None
    ahead_base, behind_base = counts(path, "HEAD", f"{REMOTE}/{BASE}")
    ahead_up, behind_up = counts(path, "HEAD", "@{upstream}")
    return {"path": path, "branch": branch, "dirty": bool(git("status", "--porcelain", cwd=path)),
            "ahead_base": ahead_base, "behind_base": behind_base,
            "ahead_upstream": ahead_up, "behind_upstream": behind_up}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="fast-forward clean checkouts of the shared branch")
    parser.add_argument("--no-fetch", action="store_true", help="do not contact the remote first")
    args = parser.parse_args()

    if not args.no_fetch:
        git("fetch", "--quiet", REMOTE)
    failed = False
    for path in worktrees():
        tree = inspect(path)
        action, note = decide(tree)
        if action == "fast-forward" and args.apply:
            try:
                git("merge", "--ff-only", f"{REMOTE}/{BASE}", cwd=path)
                action, note = "updated", note.replace("behind", "was behind")
            except RuntimeError as exc:
                action, note, failed = "report", str(exc), True
        print(f"{action:<12} {tree['branch'] or '(detached)':<28} {note}\n{'':<12} {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
