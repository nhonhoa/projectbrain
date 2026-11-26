"""Screenshot the hub-ui showcase page with headless Chrome/Edge (standard library only).

    python -m hub_ui.tools.showcase_snap http://127.0.0.1:8000/hub-ui/showcase/ out_dir

Writes ``showcase-1280.png`` and ``showcase-768.png`` into out_dir. Set the CHROME environment
variable to a browser executable to override the auto-detection.
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

WIDTHS = (1280, 768)
HEIGHT = 2400

CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
NAMES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
    "microsoft-edge", "msedge",
)


def find_browser():
    """Path of a Chrome/Edge executable, or None."""
    env = os.environ.get("CHROME")
    if env and pathlib.Path(env).exists():
        return env
    for path in CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def snap(url, out_dir, browser=None, timeout=120):
    """Write one PNG per width into out_dir; returns the list of paths."""
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError("No Chrome/Edge found; set the CHROME environment variable.")
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    profile = out / ".chrome-profile"
    written = []
    for width in WIDTHS:
        target = out / f"showcase-{width}.png"
        cmd = [
            browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            f"--user-data-dir={profile}", f"--window-size={width},{HEIGHT}",
            "--virtual-time-budget=8000", f"--screenshot={target}", url,
        ]
        subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
        if not target.exists():
            raise RuntimeError(f"Browser produced no screenshot for width {width}: {' '.join(cmd)}")
        written.append(target)
    shutil.rmtree(profile, ignore_errors=True)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="URL of the showcase page")
    parser.add_argument("out_dir", help="directory for the PNG files")
    args = parser.parse_args(argv)
    try:
        paths = snap(args.url, args.out_dir)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
