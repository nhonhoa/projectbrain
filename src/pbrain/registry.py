"""Machine-wide list of projects that use pbrain: ``~/.pbrain/projects.json``.

``pbrain init`` and the session-start hook add the project they run in; ``pbrain serve`` (the
review page) and ``pbrain scan --all`` read the list, so a new project shows up without editing
any file by hand. ``PBRAIN_HOME`` moves the whole ``~/.pbrain`` directory (tests use it).

File format (version 1)::

    {"version": 1, "projects": [
        {"root": "Z:\\\\projects\\\\demo", "name": "demo",
         "added": "2026-11-27T10:00:00+00:00", "last_seen": "2026-11-27T10:00:00+00:00"}]}

A project whose ``pbrain.toml`` is gone (deleted or moved) stays listed as *missing* until
``pbrain projects prune`` or ``remove`` drops it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_module

VERSION = 1
FILE_NAME = "projects.json"


def home() -> Path:
    """pbrain's per-user directory (``$PBRAIN_HOME`` or ``~/.pbrain``)."""
    custom = os.environ.get("PBRAIN_HOME")
    return Path(custom).expanduser() if custom else Path.home() / ".pbrain"


def path() -> Path:
    return home() / FILE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(root) -> Path:
    return Path(root).expanduser().resolve()


def key(root) -> str:
    """Comparison key of a root: resolved, and case-folded where the file system is."""
    return os.path.normcase(str(normalize(root)))


def has_config(root) -> bool:
    return (Path(root) / config_module.CONFIG_NAME).is_file()


def load(file=None) -> list[dict]:
    """The registered projects; ``[]`` when the file is missing or unreadable."""
    try:
        data = json.loads(Path(file or path()).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    items = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    entries, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("root"), str):
            continue
        k = os.path.normcase(item["root"])
        if k in seen:
            continue
        seen.add(k)
        entries.append(item)
    return entries


def _save(entries: list[dict], file=None) -> None:
    file = Path(file or path())
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(f"{file.name}.{os.getpid()}.tmp")
    body = {"version": VERSION, "projects": entries}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(20):  # Windows refuses replace while a reader has the file open
        try:
            tmp.replace(file)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


@contextmanager
def _locked(file=None, timeout=5.0):
    """Cross-process lock around read-modify-write (a lock file; stale after 30 s)."""
    lock = Path(file or path()).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"registry is locked: {lock}") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def find(root, file=None) -> dict | None:
    wanted = key(root)
    return next((e for e in load(file) if os.path.normcase(e["root"]) == wanted), None)


def add(root, name: str | None = None, file=None) -> tuple[dict, bool]:
    """Register ``root`` (or refresh its name and ``last_seen``). Returns ``(entry, added)``."""
    root = normalize(root)
    if not has_config(root):
        raise ValueError(f"{root} has no {config_module.CONFIG_NAME}")
    if name is None:
        try:
            name = config_module.load(root).project
        except (OSError, ValueError):
            name = root.name
    wanted, now = os.path.normcase(str(root)), _now()
    with _locked(file):
        entries = load(file)
        for entry in entries:
            if os.path.normcase(entry["root"]) == wanted:
                entry.update(name=name, last_seen=now)
                _save(entries, file)
                return entry, False
        entry = {"root": str(root), "name": name, "added": now, "last_seen": now}
        entries.append(entry)
        _save(entries, file)
        return entry, True


def remove(root, file=None) -> bool:
    wanted = key(root)
    with _locked(file):
        entries = load(file)
        kept = [e for e in entries if os.path.normcase(e["root"]) != wanted]
        if len(kept) == len(entries):
            return False
        _save(kept, file)
        return True


def prune(file=None) -> list[dict]:
    """Drop projects whose ``pbrain.toml`` is gone; return what was dropped."""
    with _locked(file):
        entries = load(file)
        gone = [e for e in entries if not has_config(e["root"])]
        if gone:
            _save([e for e in entries if has_config(e["root"])], file)
        return gone


def roots(file=None, include_missing=False) -> list[Path]:
    return [
        Path(e["root"]) for e in load(file) if include_missing or has_config(e["root"])
    ]


def remember(config) -> bool:
    """Register the project of ``config`` quietly; ``True`` when it was new. Never raises."""
    try:
        if not has_config(config.root):
            return False
        current = find(config.root)
        if current and current.get("name") == config.project:
            if current.get("last_seen", "")[:10] == _now()[:10]:
                return False  # seen today already: skip the write
        return add(config.root, config.project)[1]
    except Exception:  # noqa: BLE001 - bookkeeping must never break a command
        return False


# ---------------------------------------------------------------- command


def _command(args, config) -> int:
    action = args.action or "list"
    if action == "list":
        entries = load()
        rows = [{**e, "status": "ok" if has_config(e["root"]) else "missing"} for e in entries]
        if args.json:
            print(json.dumps({"file": str(path()), "projects": rows}, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print(f"No project registered ({path()}). Run `pbrain init` in a project.")
            return 0
        width = max(len(r["name"]) for r in rows)
        for row in rows:
            flag = "" if row["status"] == "ok" else "  [missing pbrain.toml]"
            print(f"{row['name']:<{width}}  {row['root']}{flag}")
        return 0
    if action == "prune":
        gone = prune()
        for entry in gone:
            print(f"removed {entry['name']}  {entry['root']}")
        print(f"{len(gone)} project(s) pruned")
        return 0
    targets = args.roots or [str(config.root)]
    code = 0
    for target in targets:
        if action == "add":
            try:
                entry, added = add(target)
            except ValueError as error:
                print(f"pbrain projects: {error}", file=sys.stderr)
                code = 2
                continue
            print(f"{'added' if added else 'already registered'}: {entry['name']}  {entry['root']}")
        else:
            done = remove(target)
            print(f"{'removed' if done else 'not registered'}: {normalize(target)}")
    return code


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "projects", help="List or edit the machine-wide project list (~/.pbrain/projects.json)."
    )
    parser.add_argument(
        "action", nargs="?", choices=("list", "add", "remove", "prune"), default="list"
    )
    parser.add_argument("roots", nargs="*", help="Project roots (default: the current project).")
    parser.add_argument("--json", action="store_true", help="`list` as JSON.")
    parser.set_defaults(func=_command)
