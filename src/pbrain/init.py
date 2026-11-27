"""``pbrain init``: report what a project already has, then install pbrain step by step.

Without flags it only reports. Each flag installs one level, and stopping at any level
leaves a working setup:

- ``--config``      write ``pbrain.toml`` if missing
- ``--wiki``        create the wiki skeleton (SCHEMA.md, index.md, log.md)
- ``--hooks``       install the runner and register the Claude Code hooks
- ``--pre-commit``  add ``pbrain check --staged`` to the git pre-commit hook
- ``--rules FILE``  write the agent rules between markers in FILE (e.g. CLAUDE.md)
- ``--all``         config, wiki, hooks and pre-commit
- no flags on a terminal (or ``--yes`` anywhere): a short wizard that detects the project (source
  roots, rules file, git) and asks one yes/no question per step; the default is the common choice
  (everything on, Enter accepts). ``--yes`` takes every default without asking.
- ``--extends URL`` link the project to a shared knowledge repo (repeatable): writes ``extends``,
  fetches the repo and fills in the wiki headings and cases directory from its ``pbrain.toml``.
  On a terminal, ``--config``/``--all`` ask for a repo when none is set (``--no-extends`` skips).

Every step is idempotent and never overwrites content it did not write.
"""

from __future__ import annotations

import json
import os
import re
import sys
from importlib import resources
from pathlib import Path

from . import config as config_module
from . import ecosystem
from .config import CONFIG_NAME, WikiConfig

RUNNER = ".claude/hooks/pbrain_run.py"
SETTINGS = ".claude/settings.json"
MARK_START = "<!-- pbrain:start -->"
MARK_END = "<!-- pbrain:end -->"
HOOK_START = "# >>> pbrain >>>"
HOOK_END = "# <<< pbrain <<<"
RUN = 'python "$CLAUDE_PROJECT_DIR"/' + RUNNER

HOOKS = {
    "PreToolUse": {"matcher": "Write|Edit|MultiEdit", "command": f"{RUN} hook pre-tool-use"},
    "SessionStart": {"matcher": "", "command": f"{RUN} hook session-start"},
    "Stop": {"matcher": "", "command": f"{RUN} hook stop"},
}

PRE_COMMIT_BLOCK = f"""{HOOK_START}
# Verify staged files against the project knowledge. Does nothing if pbrain is not installed.
python "$(git rev-parse --show-toplevel)/{RUNNER}" check --staged || exit 1
{HOOK_END}
"""

CONFIG_TEMPLATE = """# pbrain configuration. Every key is optional.
project = "{project}"

# Shared knowledge repos (git URLs or local paths), searched after this project.
# extends = ["https://github.com/you/your-knowledge"]

[wiki]
dir = "docs/wiki"

[reuse]
# Python file exposing REUSE_CATALOG; leave empty until the project has shared components.
catalog = ""
# Directories to index for `pbrain reuse` and duplicate detection.
roots = {roots}
"""


def _template(name: str) -> str:
    return resources.files("pbrain").joinpath("templates", name).read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def report(config) -> list[str]:
    """What exists in the project that pbrain can use or ingest."""
    root = config.root
    lines = [f"Project root: {root}"]
    checks = (
        (CONFIG_NAME, "pbrain config"),
        (config.wiki.dir, "wiki"),
        (config.cases.dir, "behavior cases"),
        (RUNNER, "hook runner"),
        (SETTINGS, "Claude Code settings"),
        ("CLAUDE.md", "agent rules file"),
        ("AGENTS.md", "agent rules file"),
        ("docs", "existing docs (source to ingest)"),
        ("conductor", "conductor tracks (source to ingest)"),
        (".git/hooks/pre-commit", "git pre-commit hook"),
    )
    for relative, label in checks:
        state = "found" if (root / relative).exists() else "missing"
        lines.append(f"  {state:8} {relative}  ({label})")
    lines.append(f"  extends: {', '.join(config.extends) or 'none'}")
    lines.append(
        "Nothing was changed. Use --config, --wiki, --hooks, --pre-commit, --rules or --all."
    )
    if not config.extends:
        lines.append("To share knowledge with other projects: pbrain init --extends <git URL>.")
    return lines


def install_config(config, roots=()) -> str:
    path = config.root / CONFIG_NAME
    if path.exists():
        return f"kept     {CONFIG_NAME}"
    _write(
        path,
        CONFIG_TEMPLATE.format(project=config.root.name, roots=_toml_value(list(roots))),
    )
    return f"created  {CONFIG_NAME}"


def install_wiki(config) -> list[str]:
    done = []
    for name in ("SCHEMA.md", "index.md", "log.md"):
        path = config.wiki_dir / name
        relative = path.relative_to(config.root).as_posix()
        if path.exists():
            done.append(f"kept     {relative}")
            continue
        text = _template(f"wiki/{name}")
        shared = _shared_schema(config) if name == "SCHEMA.md" else None
        if shared is not None:
            text = shared
        elif name == "SCHEMA.md":
            if config.wiki.sections != WikiConfig().sections:
                listed = "\n".join(f"- `{section}`" for section in config.wiki.sections)
                text = (
                    f"{text.rstrip()}\n\n## Page headings of this project\n\n"
                    f"Pages use these headings instead of the ones shown above:\n\n{listed}\n"
                )
        _write(path, text)
        done.append(f"created  {relative}")
    return done


def _shared_schema(config) -> str | None:
    """SCHEMA.md of the first ``extends`` repo that has one, so projects follow its conventions."""
    for _source, source_config in ecosystem.source_configs(config):
        candidate = source_config.wiki_dir / "SCHEMA.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def _toml_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _ensure_key(text: str, table: str, key: str, value) -> tuple[str, bool]:
    """Add ``key = value`` under ``[table]`` unless the key is already there (never overwrites)."""
    lines = text.split("\n")
    header = f"[{table}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    entry = f"{key} = {_toml_value(value)}"
    if start is None:
        return text.rstrip("\n") + f"\n\n{header}\n{entry}\n", True
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines)
    )
    if any(re.match(rf"\s*{re.escape(key)}\s*=", line) for line in lines[start + 1 : end]):
        return text, False
    lines.insert(start + 1, entry)
    return "\n".join(lines), True


def _ensure_extends(text: str, entry: str) -> tuple[str, bool]:
    """Add a top-level ``extends`` line; an existing uncommented one is left alone."""
    lines = text.split("\n")
    first_table = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), None)
    top = lines if first_table is None else lines[:first_table]
    if any(re.match(r"\s*extends\s*=", line) for line in top):
        return text, False
    new = f"extends = {_toml_value([entry])}"
    for i, line in enumerate(top):
        if re.match(r"#\s*extends\s*=", line):
            lines[i] = new
            return "\n".join(lines), True
    if first_table is None:
        lines += ["", new, ""] if text.strip() else [new, ""]
    else:
        lines[first_table:first_table] = [new, ""]
    return "\n".join(lines), True


def link_extends(config, entry: str):
    """Connect the project to a shared knowledge repo; return ``(new config, messages)``.

    Writes ``extends`` into ``pbrain.toml`` (creating it if needed), fetches the repo, then adds
    the wiki headings, page limits and cases directory of the shared repo for every key the
    project has not set itself. Nothing already in ``pbrain.toml`` is overwritten.
    """
    path = config.root / CONFIG_NAME
    created = not path.exists()
    text = (
        CONFIG_TEMPLATE.format(project=config.root.name, roots="[]")
        if created
        else path.read_text(encoding="utf-8")
    )
    text, added = _ensure_extends(text, entry)
    messages = [f"created  {CONFIG_NAME}"] if created else []
    if added:
        messages.append(f"added    extends = {entry}")
    elif entry not in config.extends:
        messages.append(f"kept     extends in {CONFIG_NAME} (already set; add {entry} by hand)")
    _write(path, text)
    config = config_module.load(config.root)
    messages += ecosystem.sync(config, force=True)
    shared = [c for s, c in ecosystem.source_configs(config) if s["entry"] == entry]
    if not shared:
        messages.append("skipped  shared defaults: the shared repo is not available (retry sync)")
        return config, messages
    theirs, defaults = shared[0], WikiConfig()
    wanted = [("wiki", "sections", list(theirs.wiki.sections))]
    for key in ("max_lines", "max_age_days"):
        if getattr(theirs.wiki, key) != getattr(defaults, key):
            wanted.append(("wiki", key, getattr(theirs.wiki, key)))
    if theirs.cases.dir == f"{theirs.wiki.dir}/cases":
        # Same layout as the shared repo (cases next to the wiki), but in this project's wiki.
        wanted.append(("cases", "dir", f"{config.wiki.dir}/cases"))
    for table, key, value in wanted:
        text, changed = _ensure_key(text, table, key, value)
        if changed:
            messages.append(f"added    [{table}] {key} (from the shared repo)")
    _write(path, text)
    return config_module.load(config.root), messages


def register_hint(config, entry: str) -> list[str]:
    """Tell the owner of the shared repo which line adds this project to its scan list."""
    line = str(config.root)
    for source, _config in ecosystem.source_configs(config):
        if source["entry"] == entry and (source["path"] / "review_projects.txt").is_file():
            return [
                f"note     to scan this project with {source['name']}, its owner adds this line "
                f"to review_projects.txt (pbrain never edits that repo): {line}"
            ]
    return [
        "note     to include this project in a scheduled scan, add this line to the roots-file "
        f"of the shared repo (pbrain scan --roots-file): {line}"
    ]


def _interactive() -> bool:
    """True only when a person can answer: a terminal on both ends and not a CI run."""
    if os.environ.get("CI"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


SKIP_DIRS = {
    "node_modules",
    "venv",
    "env",
    "dist",
    "build",
    "docs",
    "tests",
    "test",
    "scripts",
    "migrations",
    "static",
    "templates",
    "data",
    "certs",
    "site-packages",
}


def detect(root: Path) -> dict:
    """Facts about the project that decide the wizard defaults (read-only, no network)."""
    roots = []
    src = root / "src"
    if src.is_dir():
        roots.append("src")
    for child in sorted(root.iterdir()):
        name = child.name
        if not child.is_dir() or name.startswith((".", "_")) or name in SKIP_DIRS:
            continue
        if name.endswith((".egg-info", ".bak")) or name == "src":
            continue
        if (child / "__init__.py").exists() or (child / "manage.py").exists():
            roots.append(name)
        elif any(
            grandchild.is_dir() and (grandchild / "__init__.py").exists()
            for grandchild in child.iterdir()
            if not grandchild.name.startswith(".")
        ):
            roots.append(name)
    rules = next((n for n in ("CLAUDE.md", "AGENTS.md") if (root / n).exists()), "CLAUDE.md")
    return {
        "roots": roots,
        "rules": rules,
        "git": (root / ".git").is_dir(),
        "docs": (root / "docs").is_dir(),
    }


def _ask(question: str, default: str = "y") -> bool:
    hint = "Y/n" if default == "y" else "y/N"
    answer = input(f"{question} [{hint}] ").strip().lower()
    return default == "y" if not answer else answer.startswith("y")


def _wizard(config, assume_yes: bool, no_extends: bool):
    """Ask what to install; return ``(args-like choices, config)``. Defaults are the common case."""
    facts = detect(config.root)
    choice = {
        "config": True,
        "wiki": True,
        "hooks": True,
        "pre_commit": facts["git"],
        "rules": facts["rules"],
        "roots": facts["roots"],
        "extends": None,
    }
    if assume_yes:
        return choice
    print(f"pbrain init: {config.root}")
    print("Enter accepts the default in [brackets].")
    print()
    choice["config"] = _ask(f"Create {CONFIG_NAME} (project settings)?")
    if choice["config"] and not (config.root / CONFIG_NAME).exists():
        shown = ", ".join(facts["roots"]) or "none found"
        answer = input(f"  Code folders to index for `pbrain reuse` [{shown}]: ").strip()
        if answer:
            choice["roots"] = [part.strip() for part in answer.split(",") if part.strip()]
    choice["wiki"] = _ask(f"Create the wiki skeleton ({config.wiki.dir}/)?")
    choice["hooks"] = _ask(
        "Install Claude Code hooks (.claude/)? They remind the agent to read the "
        "wiki and run `pbrain check`."
    )
    if facts["git"]:
        choice["pre_commit"] = _ask("Add `pbrain check --staged` to the git pre-commit hook?")
    else:
        print("  (no git repository: skipping the pre-commit hook)")
    if _ask(f"Write the agent rules into {facts['rules']}?"):
        answer = input(f"  File [{facts['rules']}]: ").strip()
        choice["rules"] = answer or facts["rules"]
    else:
        choice["rules"] = None
    if not config.extends and not no_extends:
        answer = input("Shared knowledge repo (git URL or path, Enter to skip): ").strip()
        choice["extends"] = answer or None
    return choice


def install_hooks(config) -> list[str]:
    done = []
    runner = config.root / RUNNER
    text = _template("pbrain_run.py")
    if not runner.exists() or runner.read_text(encoding="utf-8") != text:
        _write(runner, text)
        done.append(f"wrote    {RUNNER}")
    settings_path = config.root / SETTINGS
    try:
        settings = (
            json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
        )
    except ValueError:
        return [*done, f"skipped  {SETTINGS}: not valid JSON, register the hooks by hand"]
    hooks = settings.setdefault("hooks", {})
    changed = False
    for event, spec in HOOKS.items():
        groups = hooks.setdefault(event, [])
        commands = [h.get("command", "") for g in groups for h in g.get("hooks", [])]
        if any(RUNNER in command for command in commands):
            continue
        group = {"hooks": [{"type": "command", "command": spec["command"]}]}
        if spec["matcher"]:
            group = {"matcher": spec["matcher"], **group}
        groups.append(group)
        changed = True
    if changed:
        _write(settings_path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        done.append(f"updated  {SETTINGS}")
    return done or [f"kept     {SETTINGS}"]


def install_pre_commit(config) -> str:
    hooks_dir = config.root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return "skipped  pre-commit: no .git/hooks directory"
    path = hooks_dir / "pre-commit"
    if not path.exists():
        _write(path, "#!/usr/bin/env bash\n" + PRE_COMMIT_BLOCK)
        try:
            path.chmod(0o755)
        except OSError:
            pass
        return "created  .git/hooks/pre-commit"
    text = path.read_text(encoding="utf-8", errors="replace")
    if HOOK_START in text:
        return "kept     .git/hooks/pre-commit"
    lines = text.split("\n")
    at = 1 if lines and lines[0].startswith("#!") else 0
    lines[at:at] = PRE_COMMIT_BLOCK.rstrip("\n").split("\n")
    _write(path, "\n".join(lines))
    return "updated  .git/hooks/pre-commit (pbrain block added after the first line)"


def install_rules(config, file_name: str) -> str:
    path = config.root / file_name
    block = _template("agent_rules.md").format(wiki_dir=config.wiki.dir, cases_dir=config.cases.dir)
    block = f"{MARK_START}\n{block.rstrip()}\n{MARK_END}"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if MARK_START in text and MARK_END in text:
        head, rest = text.split(MARK_START, 1)
        new = head + block + rest.split(MARK_END, 1)[1]
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block + "\n"
    if new == text:
        return f"kept     {file_name}"
    _write(path, new)
    return f"updated  {file_name}"


def _run(args, config) -> int:
    steps = []
    flags = (
        args.config,
        args.wiki,
        args.hooks,
        args.pre_commit,
        args.rules,
        args.all,
        args.extends,
    )
    roots = ()
    wizard = args.yes or (not any(flags) and _interactive())
    if wizard:
        choice = _wizard(config, args.yes, args.no_extends)
        args.config, args.wiki = choice["config"], choice["wiki"]
        args.hooks, args.pre_commit = choice["hooks"], choice["pre_commit"]
        args.rules = args.rules or choice["rules"]
        roots = choice["roots"]
        if choice["extends"]:
            args.extends = [choice["extends"]]
    entries = list(args.extends or [])
    if (
        not entries
        and not args.no_extends
        and not wizard
        and (args.config or args.all)
        and not config.extends
        and _interactive()
    ):
        answer = input("Link to a shared knowledge repo? Git URL or path (Enter to skip): ")
        if answer.strip():
            entries.append(answer.strip())
    for entry in entries:
        config, messages = link_extends(config, entry)
        steps += messages + register_hint(config, entry)
    if args.config or args.all:
        steps.append(install_config(config, roots))
    if args.wiki or args.all:
        steps += install_wiki(config)
    if args.hooks or args.all:
        steps += install_hooks(config)
    if args.pre_commit or args.all:
        steps.append(install_pre_commit(config))
    if args.rules:
        steps.append(install_rules(config, args.rules))
    for line in steps or report(config):
        print(line)
    return 0


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("init", help="Report on, then install pbrain into a project.")
    parser.add_argument("--config", action="store_true", help=f"Write {CONFIG_NAME} if missing.")
    parser.add_argument("--wiki", action="store_true", help="Create the wiki skeleton.")
    parser.add_argument("--hooks", action="store_true", help="Install the Claude Code hooks.")
    parser.add_argument("--pre-commit", action="store_true", help="Add pbrain to git pre-commit.")
    parser.add_argument(
        "--rules", metavar="FILE", help="Write agent rules into FILE between markers."
    )
    parser.add_argument(
        "--extends",
        metavar="URL",
        action="append",
        help="Link to a shared knowledge repo (git URL or path): write extends, sync, and take "
        "the wiki headings and cases directory from it. Can be repeated.",
    )
    parser.add_argument(
        "--no-extends",
        action="store_true",
        help="Do not ask about a shared knowledge repo on a terminal.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Run the wizard without asking: detect the project and take every default.",
    )
    parser.add_argument("--all", action="store_true", help="config + wiki + hooks + pre-commit.")
    parser.set_defaults(func=_run)
