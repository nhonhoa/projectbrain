"""Shared knowledge repos listed under ``extends`` in ``pbrain.toml``.

An ecosystem repo is an ordinary pbrain project: it has its own ``pbrain.toml``, wiki and
(optionally) reuse catalog. A project that extends it searches its own knowledge first and
the ecosystem second. Git URLs are cloned into a local cache; local paths are used in place.

Nothing here may break the caller: network or git failures produce a message, never an
exception, and a stale cache keeps being used.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from . import config as config_module
from .gitutil import git

STAMP = ".pbrain_synced"
DEFAULT_MAX_AGE_HOURS = 24


def cache_root() -> Path:
    override = os.environ.get("PBRAIN_CACHE")
    return Path(override) if override else Path.home() / ".cache" / "pbrain" / "ecosystems"


def _is_url(entry: str) -> bool:
    return bool(re.match(r"^(https?|ssh|git)://|^[\w.-]+@[\w.-]+:", entry))


def _name(entry: str) -> str:
    tail = entry.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail.removesuffix(".git") or "ecosystem"


def location(config, entry: str) -> Path:
    """Where the knowledge of one ``extends`` entry lives on disk (it may not exist yet)."""
    if _is_url(entry):
        digest = hashlib.sha256(entry.encode("utf-8")).hexdigest()[:8]
        return cache_root() / f"{_name(entry)}-{digest}"
    path = Path(entry).expanduser()
    return path if path.is_absolute() else (config.root / path).resolve()


def sources(config) -> list[dict]:
    """Every ``extends`` entry that is available locally: ``{"name", "entry", "path"}``."""
    found = []
    for entry in config.extends:
        path = location(config, entry)
        if path.is_dir():
            found.append({"name": _name(entry), "entry": entry, "path": path})
    return found


def sync(config, max_age_hours: float = DEFAULT_MAX_AGE_HOURS, force: bool = False) -> list[str]:
    """Clone or fast-forward the cached copy of each git ``extends`` entry; return messages."""
    messages = []
    for entry in config.extends:
        if not _is_url(entry):
            if not location(config, entry).is_dir():
                messages.append(f"extends: local path not found: {entry}")
            continue
        path = location(config, entry)
        stamp = path / STAMP
        if path.is_dir():
            age_hours = (time.time() - stamp.stat().st_mtime) / 3600 if stamp.exists() else None
            if not force and age_hours is not None and age_hours < max_age_hours:
                continue
            if git(path, "pull", "--ff-only", "--quiet", timeout=60) is None:
                messages.append(f"extends: could not update {_name(entry)}; using the cached copy")
                continue
            messages.append(f"extends: updated {_name(entry)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            done = git(
                path.parent, "clone", "--quiet", "--depth", "1", entry, path.name, timeout=120
            )
            if done is None or not path.is_dir():
                messages.append(f"extends: could not clone {_name(entry)}; continuing without it")
                continue
            messages.append(f"extends: cloned {_name(entry)}")
        try:
            stamp.write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass
    return messages


def source_configs(config) -> list[tuple[dict, object]]:
    """Each available source with its own loaded config."""
    loaded = []
    for source in sources(config):
        try:
            loaded.append((source, config_module.load(source["path"])))
        except (OSError, ValueError):
            continue
    return loaded


def catalog_entries(config) -> list[dict]:
    """Reuse-catalog entries of every ecosystem source, labelled with their ``source``."""
    from .reuse import catalog

    entries = []
    for source, source_config in source_configs(config):
        try:
            for entry in catalog.load_catalog(source_config):
                entries.append({**entry, "source": source["name"]})
        except Exception:  # noqa: BLE001 - a broken shared catalog must not break the project
            continue
    return entries


def describe(config) -> list[str]:
    """One line per source pointing at its wiki index, for session-start context."""
    lines = []
    for source, source_config in source_configs(config):
        index = source_config.wiki_dir / "index.md"
        target = index if index.is_file() else source["path"]
        lines.append(f"{source['name']}: {target}")
    return lines


def _run_sync(args, config) -> int:
    for message in sync(config, force=args.force):
        print(message)
    lines = describe(config)
    if not config.extends:
        print("No `extends` entries in pbrain.toml.")
    for line in lines:
        print(line)
    return 0


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "sync", help="Fetch the shared knowledge repos listed in `extends`."
    )
    parser.add_argument("--force", action="store_true", help="Update even if the cache is fresh.")
    parser.set_defaults(func=_run_sync)
