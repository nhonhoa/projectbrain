"""Command line entry point: ``pbrain <command>`` or ``python -m pbrain <command>``.

Each command lives in its own module and exposes ``add_parser(subparsers)``. That function
registers a sub-parser and sets ``func`` to a handler ``func(args, config) -> int``.
A module that fails to import only disables its own command.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__
from . import config as config_module

COMMAND_MODULES = (
    "pbrain.init",
    "pbrain.wiki",
    "pbrain.cases",
    "pbrain.reuse.command",
    "pbrain.check",
    "pbrain.ecosystem",
    "pbrain.hooks",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbrain", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"pbrain {__version__}")
    parser.add_argument("--root", help="Project root (default: nearest pbrain.toml or git root).")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in COMMAND_MODULES:
        try:
            importlib.import_module(name).add_parser(subparsers)
        except ImportError as error:  # a missing optional module must not break the others
            print(f"pbrain: command module {name} unavailable: {error}", file=sys.stderr)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    from pathlib import Path

    try:
        config = config_module.load(Path(args.root) if args.root else None)
    except (OSError, ValueError) as error:  # unreadable or invalid pbrain.toml
        print(f"pbrain: cannot read {config_module.CONFIG_NAME}: {error}", file=sys.stderr)
        return 0 if args.command == "hook" else 2  # hooks fail open
    return int(args.func(args, config) or 0)


if __name__ == "__main__":
    sys.exit(main())
