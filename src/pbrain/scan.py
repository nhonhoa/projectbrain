"""``pbrain scan``: a non-interactive pass over one or more projects, meant for a scheduler.

For each project it syncs the shared knowledge repos, builds the same review queue the
``pbrain review`` page shows, and compares it with the previous scan. The result is stored in a
small JSON state file; ``pbrain review`` reads that file to mark items that are new.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_module
from . import ecosystem, review

STATE_VERSION = 1
BLOCKING = ("lint_error", "drift")  # new items of these kinds make the exit code 1


def default_state_path() -> Path:
    return Path.home() / ".cache" / "pbrain" / "scan-state.json"


def load_state(path) -> dict:
    """The stored state, or ``{}`` when the file is missing, unreadable or malformed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {}
    return data


def save_state(path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def project_key(config) -> str:
    return str(config.root.resolve())


def item_kind(item: dict) -> str:
    """Queue kind as listed in ``review.KIND_ORDER`` (lint split by severity)."""
    kind = item["kind"]
    if kind == "lint":
        return f"lint_{item['severity']}"
    return "trust_requested" if kind == "trust" else kind


def snapshot(config) -> dict:
    """``{kind: [item ids]}`` of the project's current review queue."""
    ids = {kind: [] for kind in review.KIND_ORDER}
    for item in review.build_queue(config):
        ids[item_kind(item)].append(item["id"])
    return ids


def read_roots_file(path, warnings: list) -> list[Path]:
    """Roots listed one per line; blanks, ``#`` comments and dirs without a config are skipped."""
    text = Path(path).read_text(encoding="utf-8-sig")
    roots = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        root = Path(entry)
        if not (root / config_module.CONFIG_NAME).is_file():
            warnings.append(f"skipped {entry}: no {config_module.CONFIG_NAME}")
            continue
        roots.append(root)
    return roots


def scan_project(config, previous: dict | None, sync: bool, warnings: list) -> dict:
    """Scan one project; ``previous`` is its entry from the last state (None on the first scan)."""
    if sync:
        try:
            for message in ecosystem.sync(config):
                if "could not" in message or "not found" in message:
                    warnings.append(f"{config.project}: {message}")
        except Exception as error:  # noqa: BLE001 - a network failure must not stop the scan
            warnings.append(f"{config.project}: sync failed: {error}")
    ids = snapshot(config)
    old = {i for kind in (previous or {}).get("ids", {}).values() for i in kind}
    current = {i for kind in ids.values() for i in kind}
    new = [i for kind in ids.values() for i in kind if i not in old]
    return {
        "name": config.project,
        "root": project_key(config),
        "ids": ids,
        "new": new,
        "resolved": sorted(old - current),
    }


def new_by_kind(entry: dict) -> dict:
    new = set(entry["new"])
    return {kind: sum(1 for i in ids if i in new) for kind, ids in entry["ids"].items()}


def _format(entry: dict) -> list[str]:
    counts = {kind: len(ids) for kind, ids in entry["ids"].items() if ids}
    fresh = new_by_kind(entry)
    total = sum(counts.values())
    lines = [f"{entry['name']}  ({entry['root']})"]
    lines.append(
        f"  total {total}, new {len(entry['new'])}, resolved {len(entry['resolved'])}"
    )
    for kind, count in counts.items():
        extra = f"  (+{fresh[kind]} new)" if fresh[kind] else ""
        lines.append(f"  {kind}: {count}{extra}")
    return lines


def _command(args, config) -> int:
    warnings: list[str] = []
    explicit = [Path(r) for r in args.roots]
    roots = list(explicit)
    if args.roots_file:
        try:
            roots += read_roots_file(args.roots_file, warnings)
        except OSError as error:
            print(f"pbrain scan: cannot read {args.roots_file}: {error}", file=sys.stderr)
            return 2
    elif not explicit:
        roots = [config.root]
    try:
        configs = review.load_projects(roots[0], roots[1:]) if roots else []
    except (OSError, ValueError) as error:
        print(f"pbrain scan: {error}", file=sys.stderr)
        return 2
    if not configs:
        print("pbrain scan: no project to scan", file=sys.stderr)
        return 2

    state_path = Path(args.state) if args.state else default_state_path()
    previous = load_state(state_path).get("projects", {})
    entries = {}
    for item in configs:
        key = project_key(item)
        prev = previous.get(key)
        entries[key] = scan_project(item, prev if isinstance(prev, dict) else None,
                                    not args.no_sync, warnings)
    scanned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        save_state(state_path, {"version": STATE_VERSION, "scanned_at": scanned_at,
                                "projects": entries})
    except OSError as error:
        warnings.append(f"cannot write state {state_path}: {error}")

    blocking = sum(n for e in entries.values()
                   for kind, n in new_by_kind(e).items() if kind in BLOCKING)
    if args.json:
        payload = {"scanned_at": scanned_at, "warnings": warnings, "new_blocking": blocking,
                   "projects": [
                       {**e, "counts": {k: len(v) for k, v in e["ids"].items()},
                        "new_counts": new_by_kind(e)}
                       for e in entries.values()
                   ]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for entry in entries.values():
            print("\n".join(_format(entry)))
    for warning in warnings:
        print(f"pbrain scan: warning: {warning}", file=sys.stderr)
    return 1 if blocking else 0


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "scan",
        help="Scan projects for pending review items and report what is new (for schedulers).",
    )
    parser.add_argument("roots", nargs="*", help="Project roots (default: the current project).")
    parser.add_argument("--roots-file", help="File with one project root per line (# comments).")
    parser.add_argument("--state", help="State file (default: ~/.cache/pbrain/scan-state.json).")
    parser.add_argument("--no-sync", action="store_true", help="Do not fetch `extends` repos.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
    parser.set_defaults(func=_command)
