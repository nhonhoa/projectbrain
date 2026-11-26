"""Behavior cases: recorded input/output pairs for pure functions (conventions: SCHEMA.md).

A case file is JSON: a list of argument/expected-result pairs for functions in one module. The
file holds only data, so an implementation in any language can replay it; this module is the
Python runner.

``record`` runs the current code to fill ``expected`` for cases that have none (origin
``observed``); ``--refresh`` rewrites every ``observed`` case and reports which ones changed.
``decided`` cases are never overwritten. ``check`` replays everything: a wrong ``observed`` case
is an error (exit code 1); a wrong ``decided`` case means the code does not follow the decision
yet, which is only a warning unless ``--strict`` is given.

Python block of a case file::

    {"module": "pkg.mod", "env": {"KEY": "VALUE"}, "setup": "pkg.mod:callable"}

``env`` values are set with ``os.environ.setdefault`` before importing, and ``setup`` (optional)
is called once before the target module is imported. The shortcut
``{"django_settings": "x.settings"}`` sets ``DJANGO_SETTINGS_MODULE`` and calls ``django.setup()``.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

FORMAT = "pbrain-cases/1"
ORIGINS = {"observed", "decided"}
EPSILON = 1e-9


def encode(value):
    """Python value to language-neutral JSON; dates and decimals get a ``$...`` tag."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$float": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((encode(item) for item in value), key=lambda item: json.dumps(item))
    raise TypeError(f"cannot store a {type(value).__name__} in a case file")


def decode(value):
    """Inverse of ``encode``; builds fresh objects on every call so a tested function cannot
    corrupt the case."""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1:
            ((tag, raw),) = value.items()
            if tag == "$date":
                return date.fromisoformat(raw)
            if tag == "$datetime":
                return datetime.fromisoformat(raw)
            if tag == "$decimal":
                return Decimal(raw)
            if tag == "$float":
                return float(raw)
        return {key: decode(item) for key, item in value.items()}
    return value


def same(left, right):
    """Deep comparison of two encoded values; numbers compare by value, bool is not a number."""
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right or abs(left - right) < EPSILON
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
    return type(left) is type(right) and left == right


def _resolve_callable(spec):
    module_name, _, attr = spec.partition(":")
    target = importlib.import_module(module_name)
    for name in attr.split(".") if attr else ():
        target = getattr(target, name)
    return target


def load_module(spec):
    """Import the module of a case file, after applying `env`, the django shortcut and `setup`."""
    python = spec.get("python") or {}
    for key, value in (python.get("env") or {}).items():
        os.environ.setdefault(str(key), str(value))
    if python.get("django_settings"):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", python["django_settings"])
        import django

        django.setup()
    if python.get("setup"):
        _resolve_callable(python["setup"])()
    return importlib.import_module(python["module"])


def run_case(module, case):
    """Run one case on the current code; errors become ``{"$error": <exception class name>}``."""
    target = module
    for name in case["fn"].split("."):
        target = getattr(target, name)
    try:
        return encode(target(*decode(case.get("args", [])), **decode(case.get("kwargs", {}))))
    except Exception as error:  # noqa: BLE001 - the tested function's errors are recorded behavior
        return {"$error": type(error).__name__}


def validate(config, path, spec):
    """Check the structure of a case file; return a list of problems."""
    problems = []
    if spec.get("format") != FORMAT:
        problems.append(f"`format` must be `{FORMAT}`")
    if not (spec.get("python") or {}).get("module"):
        problems.append("missing `python.module`")
    if spec.get("target") and not (config.root / spec["target"]).exists():
        problems.append(f"`target: {spec['target']}` does not exist")
    seen = set()
    for case in spec.get("cases", []):
        case_id = case.get("id") or "(missing id)"
        if case_id in seen:
            problems.append(f"{case_id}: duplicate id")
        seen.add(case_id)
        if not case.get("fn"):
            problems.append(f"{case_id}: missing `fn`")
        origin = case.get("origin", "observed")
        if origin not in ORIGINS:
            problems.append(f"{case_id}: `origin: {origin}` is not one of {sorted(ORIGINS)}")
        if origin == "decided":
            if "expected" not in case:
                problems.append(f"{case_id}: a `decided` case needs a hand-written `expected`")
            decision = case.get("decision")
            if not decision or not (path.parent / decision).exists():
                problems.append(f"{case_id}: a `decided` case must point `decision` to a real page")
    return problems


def read_spec(path):
    return json.loads(path.read_text(encoding="utf-8"))


def head_commit(root):
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            cwd=root,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


def _prepare_path(config):
    root = str(config.root)
    if root not in sys.path:
        sys.path.insert(0, root)


def resolve(config, path):
    """A case file from the command line: as typed when it exists, else under the project root."""
    path = Path(path)
    if path.is_absolute() or path.exists():
        return path
    return config.root / path


def record(config, path, refresh=False):
    """Fill ``expected`` from the current code; return the exit code."""
    path = resolve(config, path)
    _prepare_path(config)
    spec = read_spec(path)
    problems = validate(config, path, spec)
    if problems:
        for problem in problems:
            print(f"ERROR  {path.name}: {problem}")
        return 1
    module = load_module(spec)
    added, changed = 0, []
    for case in spec["cases"]:
        origin = case.setdefault("origin", "observed")
        if origin == "decided" or ("expected" in case and not refresh):
            continue
        got = run_case(module, case)
        if "expected" not in case:
            added += 1
        elif not same(case["expected"], got):
            changed.append(case["id"])
        case["expected"] = got
    if added or changed:
        spec["recorded"] = {"commit": head_commit(config.root), "date": date.today().isoformat()}
        text = json.dumps(spec, ensure_ascii=False, indent=1) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    for case_id in changed:
        print(f"CHANGED  {path.name}: {case_id}")
    print(f"-- {path.name}: {len(spec['cases'])} cases, {added} added, {len(changed)} changed")
    return 0


def case_files(config, paths=None):
    """Explicit paths, or every ``*.json`` in the configured cases directory."""
    if paths:
        return [resolve(config, p) for p in paths]
    if not config.cases_dir.is_dir():
        return []
    return sorted(config.cases_dir.glob("*.json"))


def check(config, paths=None, strict=False):
    """Replay case files; print each problem and return ``(errors, warnings, total_cases)``."""
    files = case_files(config, paths)
    _prepare_path(config)
    errors = warnings = total = 0
    for path in files:
        spec = read_spec(path)
        problems = validate(config, path, spec)
        for problem in problems:
            print(f"ERROR  {path.name}: {problem}")
        errors += len(problems)
        if problems:
            continue
        module = load_module(spec)
        for case in spec["cases"]:
            total += 1
            if "expected" not in case:
                print(f"ERROR  {path.name}: {case['id']}: no `expected` yet, run `record` first")
                errors += 1
                continue
            got = run_case(module, case)
            if same(case["expected"], got):
                continue
            show = json.dumps({"expected": case["expected"], "actual": got}, ensure_ascii=False)
            if case.get("origin") == "decided" and not strict:
                decision = case["decision"]
                print(
                    f"WARNING  {path.name}: {case['id']}: not following decision {decision} {show}"
                )
                warnings += 1
            else:
                print(f"ERROR  {path.name}: {case['id']}: {show}")
                errors += 1
    return errors, warnings, total


def _record_command(args, config):
    return record(config, args.file, args.refresh)


def _check_command(args, config):
    files = case_files(config, args.files)
    if not files:
        print(f"No behavior case files found in {config.cases.dir}.")
        return 0
    errors, warnings, total = check(config, args.files, args.strict)
    print(f"-- {len(files)} files, {total} cases, {errors} errors, {warnings} warnings")
    return 1 if errors else 0


def add_parser(subparsers):
    parser = subparsers.add_parser("cases", help="Record and replay behavior cases.")
    commands = parser.add_subparsers(dest="cases_command", required=True)

    record_parser = commands.add_parser("record", help="Fill expected from the current code.")
    record_parser.add_argument("file")
    record_parser.add_argument(
        "--refresh", action="store_true", help="Also rewrite existing observed cases."
    )
    record_parser.set_defaults(func=_record_command)

    check_parser = commands.add_parser("check", help="Replay case files.")
    check_parser.add_argument("files", nargs="*")
    check_parser.add_argument(
        "--strict", action="store_true", help="A wrong decided case is an error too."
    )
    check_parser.set_defaults(func=_check_command)
