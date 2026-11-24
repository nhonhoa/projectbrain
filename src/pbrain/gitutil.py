"""Small git helpers. Every call is best effort: a failure returns an empty result."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(root: Path, *args: str, timeout: int = 30) -> str | None:
    """Stdout of a git command, or None when git fails or is missing."""
    try:
        done = subprocess.run(
            ["git", *args],
            capture_output=True,
            cwd=root,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def changed_files(root: Path, staged: bool = False) -> list[str]:
    """Paths (relative, forward slashes) that differ from HEAD, untracked files included.

    With ``staged`` only the index is considered, which is what a pre-commit hook needs.
    Deleted files are left out.
    """
    if staged:
        out = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        return sorted(p for p in (out or "").split("\0") if p)
    names = set()
    out = git(root, "diff", "HEAD", "--name-only", "--diff-filter=ACMR", "-z")
    names.update(p for p in (out or "").split("\0") if p)
    out = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    names.update(p for p in (out or "").split("\0") if p)
    return sorted(names)
