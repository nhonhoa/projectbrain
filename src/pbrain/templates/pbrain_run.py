"""Run pbrain with whichever Python of this project has it installed.

Installed by `pbrain init` as `.claude/hooks/pbrain_run.py`. Used by agent hooks and the
git pre-commit hook: `python .claude/hooks/pbrain_run.py <pbrain arguments>`.

Fails open: when no interpreter can import pbrain, it exits 0 and does nothing, so a
machine without pbrain is never blocked.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def interpreters():
    override = os.environ.get("PBRAIN_PYTHON")
    if override:
        yield override
    for folder in ("venv", ".venv", "env"):
        for tail in (("Scripts", "python.exe"), ("bin", "python")):
            yield os.path.join(ROOT, folder, *tail)
    yield sys.executable


def main():
    for python in interpreters():
        if not os.path.isfile(python):
            continue
        try:
            probe = subprocess.run([python, "-c", "import pbrain"], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode != 0:
            continue
        done = subprocess.run([python, "-m", "pbrain", "--root", ROOT, *sys.argv[1:]])
        return done.returncode
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001 - never block work because the runner itself failed
        code = 0
    sys.exit(code)
