"""``pbrain serve`` and ``pbrain setup``: the review page as an always-on local service.

One background server per user ("the daemon") serves every registered project on
``http://127.0.0.1:8765/``. Nothing else is needed to run it: no WebHost, no scheduler.

- ``pbrain setup`` installs a login autostart (Windows: the per-user ``Run`` registry key;
  macOS: a LaunchAgent; Linux: a systemd user unit, else an XDG autostart entry) and starts the
  server now. Run it once after installing pbrain.
- Every other pbrain command (``init``, the session-start hook, ...) calls ``ensure_server()``,
  which starts the daemon when none is alive. ``PBRAIN_AUTOSERVE=0`` (or ``CI``) turns that off.
- Singleton: the daemon holds an OS lock on ``~/.pbrain/serve.lock`` while it lives, so a second
  one exits at once. If the port is taken by another pbrain server (for example one run by
  WebHost with ``pbrain serve --standby``), the daemon waits and takes over when it stops; the
  same holds the other way round. Nothing ever serves the page twice.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, registry

DEFAULT_PORT = 8765
HOST = "127.0.0.1"
LOG_LIMIT = 1024 * 1024
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "pbrain-serve"
LAUNCH_LABEL = "dev.pbrain.serve"
UNIT_NAME = "pbrain-serve.service"


def default_port() -> int:
    try:
        return int(os.environ.get("PBRAIN_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def standby_poll() -> float:
    """Seconds between bind attempts while another server holds the port."""
    try:
        return max(0.1, float(os.environ.get("PBRAIN_STANDBY_POLL") or 10))
    except ValueError:
        return 10.0


def url(port=None, host=HOST) -> str:
    host = f"[{host}]" if ":" in host else host
    return f"http://{host}:{port or default_port()}/"


def state_path() -> Path:
    return registry.home() / "serve.json"


def lock_path() -> Path:
    return registry.home() / "serve.lock"


def log_path() -> Path:
    return registry.home() / "serve.log"


def autoserve_enabled() -> bool:
    flag = os.environ.get("PBRAIN_AUTOSERVE", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return not os.environ.get("CI")


# ---------------------------------------------------------------- singleton lock


class Lock:
    """An exclusive, non-blocking OS lock on a file; released by the OS when the process dies."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - held for the life of the process
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()
        self.handle = None


def daemon_alive() -> bool:
    """True while a daemon holds the lock (serving or waiting for the port)."""
    probe = Lock(lock_path())
    if probe.acquire():
        probe.release()
        return False
    return True


def read_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(**fields) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- probing


def probe(port=None, host=HOST, timeout=1.0) -> dict | None:
    """What answers on the port: ``{"app": "pbrain", ...}``, ``{"app": "other"}`` or ``None``."""
    target = f"{url(port, host)}api/health"
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("app") == "pbrain":
                return data
            return {"app": "other"}
    except urllib.error.HTTPError as error:  # a pbrain review page older than /api/health
        server = error.headers.get("Server", "") if error.headers else ""
        return {"app": "pbrain", "version": "old"} if "pbrain-review" in server else {
            "app": "other"}
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- start / stop


def _python(windowless=True) -> str:
    exe = Path(sys.executable)
    if os.name == "nt" and windowless:
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return str(exe)


def daemon_command(windowless=True) -> list[str]:
    return [_python(windowless), "-m", "pbrain", "serve", "--daemon"]


def spawn_daemon() -> bool:
    """Start ``pbrain serve --daemon`` detached from this process. Never raises."""
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(Path.home()),
        "close_fds": True,
    }
    env = dict(os.environ)
    env.pop("PBRAIN_AUTOSERVE", None)
    kwargs["env"] = env
    if os.name == "nt":
        detached = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_GROUP, NO_WINDOW
        attempts = [detached | 0x01000000, detached]  # first try to leave the caller's job
    else:
        kwargs["start_new_session"] = True
        attempts = [0]
    for flags in attempts:
        try:
            if os.name == "nt":
                subprocess.Popen(daemon_command(), creationflags=flags, **kwargs)
            else:
                subprocess.Popen(daemon_command(), **kwargs)
            return True
        except OSError:
            continue
    return False


def stop_daemon(timeout=5.0) -> bool:
    """Stop the daemon recorded in ``serve.json``; True when none is left running."""
    if not daemon_alive():
        return True
    pid = read_state().get("pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not daemon_alive():
            return True
        time.sleep(0.1)
    return False


def ensure_server(force=False) -> str:
    """Make sure the daemon runs; returns ``running``, ``started``, ``restarted``, ``off``
    or ``failed``. Cheap when it already runs (one lock attempt, no network)."""
    if not force and not autoserve_enabled():
        return "off"
    try:
        if daemon_alive():
            version = read_state().get("version")
            if version and version != __version__ and stop_daemon():
                return "restarted" if spawn_daemon() else "failed"
            return "running"
        return "started" if spawn_daemon() else "failed"
    except Exception:  # noqa: BLE001 - starting the page must never break a command
        return "failed"


def wait_until_serving(port=None, timeout=8.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = probe(port, timeout=0.5)
        if found and found.get("app") == "pbrain":
            return found
        time.sleep(0.2)
    return None


# ---------------------------------------------------------------- autostart


def _launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"


def _systemd_unit() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / UNIT_NAME


def _xdg_autostart() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "pbrain-serve.desktop"


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


def _run_quiet(*argv) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_autostart() -> str:
    """Start the daemon at every login. Returns a line describing what was installed."""
    command = daemon_command()
    if os.name == "nt":
        import winreg

        line = " ".join(_quote(a) for a in command)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, line)
        return f"autostart: HKCU\\{RUN_KEY}\\{RUN_VALUE} = {line}"
    if sys.platform == "darwin":
        plist = _launch_agent()
        plist.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"\n    <string>{a}</string>" for a in command)
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0"><dict>\n  <key>Label</key><string>{LAUNCH_LABEL}</string>\n'
            f"  <key>ProgramArguments</key><array>{args}\n  </array>\n"
            "  <key>RunAtLoad</key><true/>\n</dict></plist>\n",
            encoding="utf-8",
        )
        return f"autostart: {plist}"
    if shutil.which("systemctl") and _run_quiet("systemctl", "--user", "show-environment"):
        unit = _systemd_unit()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "[Unit]\nDescription=pbrain review page\n\n[Service]\nType=simple\n"
            f"ExecStart={' '.join(_quote(a) for a in command)}\nRestart=on-failure\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        _run_quiet("systemctl", "--user", "daemon-reload")
        _run_quiet("systemctl", "--user", "enable", UNIT_NAME)
        return f"autostart: {unit} (systemctl --user enable {UNIT_NAME})"
    entry = _xdg_autostart()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=pbrain review page\n"
        f"Exec={' '.join(_quote(a) for a in command)}\nNoDisplay=true\n",
        encoding="utf-8",
    )
    return f"autostart: {entry}"


def uninstall_autostart() -> list[str]:
    removed = []
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, RUN_VALUE)
            removed.append(f"HKCU\\{RUN_KEY}\\{RUN_VALUE}")
        except OSError:
            pass
        return removed
    if _systemd_unit().is_file():
        _run_quiet("systemctl", "--user", "disable", UNIT_NAME)
    for path in (_launch_agent(), _systemd_unit(), _xdg_autostart()):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def autostart_installed() -> str:
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, RUN_VALUE)[0])
        except OSError:
            return ""
    for path in (_launch_agent(), _systemd_unit(), _xdg_autostart()):
        if path.is_file():
            return str(path)
    return ""


# ---------------------------------------------------------------- serving


class _Log:
    """stdout/stderr of a windowless daemon: ``~/.pbrain/serve.log``, cut when it grows big."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            path.replace(path.with_suffix(".log.1"))
        self.stream = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    def write(self, text):
        self.stream.write(text)

    def flush(self):
        self.stream.flush()


def _say(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] pbrain serve: {message}", flush=True)


def _bind(configs, host, port, state, standby):
    """Bind the review server; while ``standby``, wait for the port instead of failing."""
    from . import review

    warned = None
    while True:
        try:
            return review.ReviewServer(
                (host, port), configs, state_path=state, use_registry=True
            )
        except OSError as error:
            if not standby:
                raise
            holder = probe(port, host)
            who = "another pbrain server" if holder and holder.get("app") == "pbrain" else (
                "another program" if holder else f"an error ({error})")
            if who != warned:
                _say(f"{url(port, host)} is held by {who}; waiting to take over")
                warned = who
            time.sleep(standby_poll())


def fixed_projects(config, roots, roots_file, include_current) -> list:
    """Configs given on the command line or in a roots file (registry projects come later)."""
    from . import review, scan

    candidates: list = []
    if include_current and registry.has_config(config.root):
        candidates.append(config)
    candidates += [Path(r) for r in roots or ()]
    if roots_file:
        warnings: list[str] = []
        candidates += scan.read_roots_file(roots_file, warnings)
        for warning in warnings:
            _say(warning)
    if not candidates:
        return []
    return review.load_projects(candidates[0], candidates[1:])


def _record(status, port, host) -> None:
    _write_state(pid=os.getpid(), status=status, port=port, host=host, version=__version__,
                 url=url(port, host),
                 since=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def run(configs, host, port, state=None, standby=False, open_browser=False, daemon=False) -> int:
    if daemon:
        _record("waiting", port, host)  # the lock holder's pid, even while it waits for the port
    server = _bind(configs, host, port, state, standby)
    address = url(server.server_address[1], host)
    if daemon:
        _record("serving", server.server_address[1], host)
    count = len(server.projects.info())
    _say(f"{address}  ({count} project(s), registry {registry.path()}). Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def _daemon(args, config) -> int:
    lock = Lock(lock_path())
    if not lock.acquire():
        return 0  # one is already running (or waiting for the port)
    if sys.stdout is None or os.name == "nt" or not sys.stdout.isatty():
        sys.stdout = sys.stderr = _Log(log_path())
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        return run([], args.host, args.port, args.state, standby=True, daemon=True)
    except Exception as error:  # noqa: BLE001 - leave a trace for `pbrain serve --status`
        _say(f"stopped: {type(error).__name__}: {error}")
        return 2
    finally:
        lock.release()


def status_lines(port=None) -> list[str]:
    port = port or default_port()
    found = probe(port)
    state = read_state()
    if found and found.get("app") == "pbrain":
        mine = found.get("pid") is not None and state.get("pid") == found.get("pid")
        who = "the pbrain daemon" if mine else "another pbrain server (WebHost, a terminal ...)"
        page = f"page: {url(port)} served by {who}, version {found.get('version')}"
    elif found:
        page = f"page: port {port} is used by another program"
    else:
        page = f"page: nothing listens on {url(port)}"
    alive = daemon_alive()
    daemon = "not running"
    if alive:
        daemon = f"alive, {state.get('status', '?')} (pid {state.get('pid')})"
    lines = [page, f"daemon: {daemon}"]
    lines.append(f"autostart: {autostart_installed() or 'not installed (run `pbrain setup`)'}")
    lines.append(f"autoserve: {'on' if autoserve_enabled() else 'off (PBRAIN_AUTOSERVE / CI)'}")
    lines.append(f"registry: {registry.path()} ({len(registry.load())} project(s))")
    lines.append(f"log: {log_path()}")
    return lines


def _serve(args, config) -> int:
    if args.status:
        print("\n".join(status_lines(args.port)))
        return 0
    if args.stop:
        done = stop_daemon()
        print("daemon stopped" if done else "could not stop the daemon (see `--status`)")
        return 0 if done else 1
    if args.daemon:
        return _daemon(args, config)
    try:
        configs = fixed_projects(config, args.roots, args.roots_file, not args.no_current)
    except (OSError, ValueError) as error:
        print(f"pbrain serve: {error}", file=sys.stderr)
        return 2
    try:
        return run(configs, args.host, args.port, args.state, args.standby, args.open)
    except OSError as error:
        print(f"pbrain serve: cannot listen on {args.host}:{args.port}: {error}", file=sys.stderr)
        return 2


def _setup(args, config) -> int:
    if args.uninstall:
        removed = uninstall_autostart()
        stopped = stop_daemon()
        print("removed: " + (", ".join(removed) if removed else "no autostart entry"))
        print("daemon stopped" if stopped else "daemon could not be stopped")
        return 0
    print(install_autostart())
    if registry.has_config(config.root):
        entry, added = registry.add(config.root, config.project)
        print(f"registered {entry['name']}: {entry['root']}" if added else
              f"already registered: {entry['name']}")
    outcome = ensure_server(force=True)
    deadline = time.monotonic() + 10
    while outcome != "failed" and not daemon_alive() and time.monotonic() < deadline:
        time.sleep(0.2)
    found = wait_until_serving()
    state = read_state()
    if found and found.get("pid") is not None and found.get("pid") == state.get("pid"):
        print(f"review page: {url()} (pbrain {found.get('version')}, background daemon)")
    elif found:
        print(f"review page: {url()} is served by another pbrain server (version "
              f"{found.get('version')}); the daemon waits and takes over when that one stops")
    else:
        print(f"review page: {outcome}; not answering yet on {url()} - see {log_path()}")
    if not autoserve_enabled():
        print("note: PBRAIN_AUTOSERVE is off here, other commands will not restart it")
    return 0


def autoserve_after(command: str) -> None:
    """Hook for ``cli.main``: keep the page running after any command, quietly."""
    if command in ("serve", "setup", "hook", "review"):
        return
    ensure_server()


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "serve",
        help="Run the review page for every registered project (the daemon `setup` installs).",
    )
    parser.add_argument("roots", nargs="*", help="Extra project roots, besides the registry.")
    parser.add_argument("--roots-file", help="File with one project root per line (# comments).")
    parser.add_argument("--port", type=int, default=default_port(),
                        help="Port (default 8765 or $PBRAIN_PORT).")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--state", help="Scan state file (default as `pbrain scan`).")
    parser.add_argument("--open", action="store_true", help="Open the page in the browser.")
    parser.add_argument("--no-current", action="store_true",
                        help="Do not add the current project (only registry and roots).")
    parser.add_argument("--standby", action="store_true",
                        help="If the port is taken, wait and take over when it is free.")
    parser.add_argument("--daemon", action="store_true",
                        help="Background singleton (what autostart runs); logs to ~/.pbrain.")
    parser.add_argument("--status", action="store_true", help="Show page, daemon and autostart.")
    parser.add_argument("--stop", action="store_true", help="Stop the background daemon.")
    parser.set_defaults(func=_serve)

    setup = subparsers.add_parser(
        "setup", help="One-time install: start the review page now and at every login."
    )
    setup.add_argument("--uninstall", action="store_true",
                       help="Remove the autostart entry and stop the daemon.")
    setup.set_defaults(func=_setup)

