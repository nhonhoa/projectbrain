"""The reuse catalog: a Python file listing components that are already shared.

The file is loaded BY PATH (never imported as part of a package) and may define:

``REUSE_CATALOG``
    A sequence of entry dicts: ``name`` (unique), ``kind``, ``path``, ``use_when``, ``keywords``
    and optionally ``symbol``, ``doc``, ``instead_of`` (regexes of code that re-implements it).
``KINDS``
    Optional tuple of allowed ``kind`` values.
``CATALOG_EXEMPT``
    Optional mapping ``{path: reason}`` of shared-area files deliberately left out of the catalog.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

from ..config import Config

_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")


def load_catalog_file(path) -> dict:
    """Load a catalog module by file path; returns entries, kinds (or None) and exemptions."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location("_pbrain_reuse_catalog", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load catalog file {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    kinds = getattr(module, "KINDS", None)
    return {
        "entries": [dict(entry) for entry in getattr(module, "REUSE_CATALOG", ())],
        "kinds": tuple(kinds) if kinds is not None else None,
        "exempt": dict(getattr(module, "CATALOG_EXEMPT", {}) or {}),
    }


def load_catalog(config: Config) -> list[dict]:
    """Entries of the project catalog; ``[]`` if unconfigured, missing or broken."""
    if not config.reuse.catalog:
        return []
    path = config.root / config.reuse.catalog
    if not path.is_file():
        return []
    try:
        return load_catalog_file(path)["entries"]
    except Exception:  # noqa: BLE001 - a broken catalog must not break lookups or the edit guard
        return []


def _python_symbol_exists(source: str, symbol: str) -> bool:
    """``name`` or ``Class.method`` defined at module level, or ``name`` as a string literal."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    # A string literal counts too: route names and similar identifiers live in strings.
    if any(isinstance(n, ast.Constant) and n.value == symbol for n in ast.walk(tree)):
        return True
    owner, _, member = symbol.rpartition(".")
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if not owner and symbol in names:
            return True
        if owner and isinstance(node, ast.ClassDef) and node.name == owner:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if child.name == member:
                        return True
                elif isinstance(child, ast.Assign):
                    if any(isinstance(t, ast.Name) and t.id == member for t in child.targets):
                        return True
    return False


def _symbol_exists(path: Path, symbol: str) -> bool:
    source = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py" and _DOTTED.match(symbol):
        return _python_symbol_exists(source, symbol)
    return symbol in source


def validate(config: Config) -> list[str]:
    """Problems that make the catalog stale or malformed; an empty list means healthy."""
    if not config.reuse.catalog:
        return []
    path = config.root / config.reuse.catalog
    if not path.is_file():
        return [f"catalog file not found: {config.reuse.catalog}"]
    try:
        loaded = load_catalog_file(path)
    except Exception as error:  # noqa: BLE001 - report any load failure as a problem
        return [f"catalog file cannot be loaded: {error}"]
    entries, kinds = loaded["entries"], loaded["kinds"]
    problems = []
    names = [entry.get("name") for entry in entries]
    problems += [
        f"duplicate name: {name}" for name in sorted({n for n in names if names.count(n) > 1})
    ]
    for entry in entries:
        name = entry.get("name", "?")
        if kinds is not None and entry.get("kind") not in kinds:
            problems.append(f"{name}: unknown kind {entry.get('kind')!r}")
        if not entry.get("use_when") or not entry.get("keywords"):
            problems.append(f"{name}: missing use_when/keywords")
        elif not entry["keywords"].isascii():
            problems.append(f"{name}: keywords must be ASCII (strip accents)")
        target = config.root / entry.get("path", "")
        if not entry.get("path") or not target.is_file():
            problems.append(f"{name}: file not found: {entry.get('path')}")
        elif entry.get("symbol") and not _symbol_exists(target, entry["symbol"]):
            problems.append(f"{name}: {entry['symbol']!r} not found in {entry['path']}")
        if entry.get("doc") and not (config.root / entry["doc"].split("#")[0]).is_file():
            problems.append(f"{name}: documentation file not found: {entry['doc']}")
        for pattern in entry.get("instead_of") or ():
            try:
                re.compile(pattern)
            except re.error as error:
                problems.append(f"{name}: invalid instead_of regex {pattern!r}: {error}")
    return problems


def rewrites(config: Config, paths) -> list[str]:
    """Files among ``paths`` (relative to the root) matching some entry's ``instead_of`` pattern.

    An entry's own ``path`` and the catalog file itself are never reported.
    """
    entries = load_catalog(config)
    own = config.reuse.catalog.replace("\\", "/")
    hits = []
    for raw in paths:
        relative = Path(raw).as_posix()
        if relative == own:
            continue
        file = config.root / relative
        if not file.is_file():
            continue
        source = file.read_text(encoding="utf-8", errors="replace")
        for entry in entries:
            if entry.get("path") == relative:
                continue
            for pattern in entry.get("instead_of") or ():
                try:
                    found = re.search(pattern, source)
                except re.error:
                    continue
                if found:
                    hits.append(relative)
                    break
            else:
                continue
            break
    return hits
