"""``pbrain reuse``: look up reusable components, audit duplicates, manage descriptions."""

from __future__ import annotations

import json
from pathlib import Path

from . import catalog, duplicates, store


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "reuse",
        help="Find reusable components by purpose before writing new code.",
        description=(
            "Searches the reuse catalog first, then all indexed code. Use 2-5 keywords "
            "describing the PURPOSE (accents are ignored)."
        ),
    )
    parser.add_argument("terms", nargs="*", help="Purpose keywords.")
    parser.add_argument("--kind", help="Only catalog entries of this kind; alone, list the group.")
    parser.add_argument("--limit", type=int, default=6, help="Max code results (capped at 6).")
    parser.add_argument(
        "--audit", action="store_true", help="List suspected duplicate clusters across all code."
    )
    parser.add_argument(
        "--undescribed",
        action="store_true",
        help="List symbols with neither a docstring nor a stored description.",
    )
    parser.add_argument(
        "--import-summaries",
        metavar="FILE",
        help='Merge a JSON file {"path::name": {"s": description, "k": keywords}} into the index.',
    )
    parser.add_argument(
        "--validate", action="store_true", help="Check the catalog for stale or malformed entries."
    )
    parser.set_defaults(func=run, parser=parser)


def run(args, config) -> int:
    if args.audit:
        clusters = duplicates.audit(config)
        print("\n".join(f"[{c['why']}] " + " | ".join(c["members"]) for c in clusters) or "(none)")
        return 0
    if args.undescribed:
        rows = store.undescribed(config)
        print(
            "\n".join(f"{r['path']}::{r['name']} {r['start']}-{r['end']}" for r in rows) or "(none)"
        )
        return 0
    if args.import_summaries:
        try:
            items = json.loads(Path(args.import_summaries).read_text(encoding="utf-8"))
            written = store.import_summaries(config, items)
        except (OSError, ValueError) as error:
            print(f"pbrain reuse: {error}")
            return 1
        print(json.dumps({"written": written}))
        return 0
    if args.validate:
        problems = catalog.validate(config)
        if problems:
            print("\n".join(problems))
            return 1
        print("catalog OK")
        return 0
    if not args.terms and not args.kind:
        args.parser.print_usage()
        print("pbrain reuse: give purpose keywords, or --kind to list a group, or an action flag.")
        return 2
    if args.kind:
        kinds = _kinds(config)
        if kinds is not None and args.kind not in kinds:
            print(f"pbrain reuse: --kind must be one of: {', '.join(kinds)}")
            return 2
    from .. import ecosystem

    shared = ecosystem.catalog_entries(config)
    print(
        store.format_search(
            config, args.terms, kind=args.kind, limit=min(args.limit, 6), extra_entries=shared
        )
    )
    return 0


def _kinds(config):
    """Allowed kinds declared by the catalog file, or None when it declares none."""
    if not config.reuse.catalog:
        return None
    path = config.root / config.reuse.catalog
    try:
        return catalog.load_catalog_file(path)["kinds"] if path.is_file() else None
    except Exception:  # noqa: BLE001 - a broken catalog only disables the kind check
        return None
