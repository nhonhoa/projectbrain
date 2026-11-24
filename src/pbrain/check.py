"""``pbrain check``: one command for the agent's "done" step, the pre-commit hook and CI.

Steps, in order:

1. ``ruff check`` on changed Python files (skipped when ruff is not installed or disabled).
2. Reuse catalog integrity, and changed files that re-implement a catalogued component.
3. Likely duplicates of existing code among the changed symbols (warning).
4. Wiki lint; wiki pages whose covered code changed (warning).
5. Behavior cases.

Errors make the command exit 1. Warnings are printed and leave the exit code at 0.
"""

from __future__ import annotations

import subprocess
import sys

from .gitutil import changed_files


def _ruff(config, paths: list[str]) -> list[str]:
    python = [p for p in paths if p.endswith(".py") and (config.root / p).is_file()]
    if not config.check.ruff or not python:
        return []
    try:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--quiet",
                "--output-format",
                "concise",
                *python,
            ],
            capture_output=True,
            cwd=config.root,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode == 0 or "No module named ruff" in (done.stderr or ""):
        return []
    return [line for line in (done.stdout or done.stderr).splitlines() if line.strip()]


def run(
    config, paths: list[str], everything: bool = False, strict: bool = False
) -> tuple[list, list]:
    """Run every step; return ``(errors, warnings)`` as lists of printable lines."""
    from . import cases, wiki
    from .reuse import catalog, duplicates, guard

    errors: list[str] = []
    warnings: list[str] = []

    errors += [f"ruff: {line}" for line in _ruff(config, paths)]

    code = [p for p in paths if guard.in_scope(config, p)]
    if config.reuse.catalog:
        errors += [f"catalog: {line}" for line in catalog.validate(config)]
        for path in catalog.rewrites(config, code):
            errors.append(
                f"reuse: {path} re-implements a catalogued component; "
                "run `pbrain reuse <keywords>` and use the shared one"
            )
    if config.reuse.roots and code:
        for hit in duplicates.find_duplicates(config, code):
            warnings.append(f"duplicate: {hit['new']} ~ {hit['existing']} ({hit['why']})")

    if config.wiki_dir.is_dir():
        findings, _ = wiki.lint_wiki(config)
        for level, name, message in findings:
            if level == "ERROR":
                errors.append(f"wiki: {name}: {message}")
            elif everything:
                warnings.append(f"wiki: {name}: {message}")
        if not everything:
            for page in wiki.pages_covering(config, paths):
                for drift in page.get("drift") or ():
                    warnings.append(f"wiki: {page['page']}: DRIFT: {drift}")

    if config.cases_dir.is_dir():
        case_errors, case_warnings, _ = cases.check(config, strict=strict)
        if case_errors:
            errors.append(f"cases: {case_errors} failing (details above)")
        if case_warnings:
            warnings.append(
                f"cases: {case_warnings} decided case(s) not yet implemented (details above)"
            )
    return errors, warnings


def _run(args, config) -> int:
    paths = [p.replace("\\", "/") for p in args.files] or changed_files(
        config.root, staged=args.staged
    )
    errors, warnings = run(config, paths, everything=args.all, strict=args.strict)
    for line in warnings:
        print(f"WARNING  {line}")
    for line in errors:
        print(f"ERROR    {line}")
    print(
        f"-- pbrain check: {len(paths)} file(s), {len(errors)} error(s), {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "check", help="Verify changed files against the project knowledge."
    )
    parser.add_argument(
        "files", nargs="*", help="Files to check (default: files changed since HEAD)."
    )
    parser.add_argument("--staged", action="store_true", help="Check staged files (pre-commit).")
    parser.add_argument("--all", action="store_true", help="Also report every wiki warning.")
    parser.add_argument("--strict", action="store_true", help="Decided cases that fail are errors.")
    parser.set_defaults(func=_run)
