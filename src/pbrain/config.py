"""Load ``pbrain.toml`` from the project root.

Every key is optional. A project with no config file still gets working defaults, so
``pbrain wiki lint`` and ``pbrain cases check`` run anywhere.

Example::

    project = "my-app"
    extends = ["https://github.com/me/my-knowledge"]

    [wiki]
    dir = "docs/wiki"
    sections = ["## Intent", "## Reality", "## Gaps", "## Related"]

    [reuse]
    catalog = "tools/reuse_catalog.py"
    roots = ["src"]
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "pbrain.toml"


@dataclass(frozen=True)
class WikiConfig:
    dir: str = "docs/wiki"
    # Headings every page body should contain. Projects may translate them.
    sections: tuple[str, ...] = ("## Intent", "## Reality", "## Gaps", "## Related")
    max_lines: int = 80
    max_age_days: int = 180


@dataclass(frozen=True)
class CasesConfig:
    dir: str = "docs/wiki/cases"


@dataclass(frozen=True)
class ReuseConfig:
    # Python file exposing REUSE_CATALOG (a sequence of dicts). Empty = no catalog.
    catalog: str = ""
    # Directories scanned for the code index, relative to the project root.
    roots: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = (".py", ".js", ".html")
    skip_parts: tuple[str, ...] = (
        "migrations",
        "tests",
        "__pycache__",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
    )
    index: str = ".pbrain/reuse_index.sqlite3"
    # JSON file with one-line descriptions per symbol, committed to git. Empty = none.
    summaries: str = ""
    # Extra names too generic to report as duplicates (added to the built-in list).
    common_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckConfig:
    # Run ``ruff check`` on changed Python files when ruff is installed.
    ruff: bool = True


@dataclass(frozen=True)
class Config:
    root: Path
    project: str = ""
    # Git URLs or local paths of shared knowledge repos, searched after the project itself.
    extends: tuple[str, ...] = ()
    wiki: WikiConfig = field(default_factory=WikiConfig)
    cases: CasesConfig = field(default_factory=CasesConfig)
    reuse: ReuseConfig = field(default_factory=ReuseConfig)
    check: CheckConfig = field(default_factory=CheckConfig)

    @property
    def wiki_dir(self) -> Path:
        return self.root / self.wiki.dir

    @property
    def cases_dir(self) -> Path:
        return self.root / self.cases.dir


def find_root(start: Path | None = None) -> Path:
    """Nearest ancestor holding ``pbrain.toml``; else the git top level; else ``start``."""
    start = (start or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            cwd=start,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if done.returncode == 0 and done.stdout.strip():
            return Path(done.stdout.strip()).resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return start


def _section(cls, raw: dict):
    """Build a config dataclass from a TOML table, ignoring unknown keys."""
    known = {}
    for name, spec in cls.__dataclass_fields__.items():
        if name in raw:
            value = raw[name]
            known[name] = tuple(value) if isinstance(spec.default, tuple) else value
    return cls(**known)


def load(root: Path | None = None) -> Config:
    """Read the config of the project at ``root`` (default: discovered from the cwd)."""
    root = find_root(root)
    path = root / CONFIG_NAME
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    extends = raw.get("extends", ())
    if isinstance(extends, str):
        extends = (extends,)
    return Config(
        root=root,
        project=str(raw.get("project", "") or root.name),
        extends=tuple(extends),
        wiki=_section(WikiConfig, raw.get("wiki", {})),
        cases=_section(CasesConfig, raw.get("cases", {})),
        reuse=_section(ReuseConfig, raw.get("reuse", {})),
        check=_section(CheckConfig, raw.get("check", {})),
    )
