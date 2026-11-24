"""``pbrain init``: report what a project already has, then install pbrain step by step.

Without flags it only reports. Each flag installs one level, and stopping at any level
leaves a working setup:

- ``--config``      write ``pbrain.toml`` if missing
- ``--wiki``        create the wiki skeleton (SCHEMA.md, index.md, log.md)
- ``--hooks``       install the runner and register the Claude Code hooks
- ``--pre-commit``  add ``pbrain check --staged`` to the git pre-commit hook
- ``--rules FILE``  write the agent rules between markers in FILE (e.g. CLAUDE.md)
- ``--all``         config, wiki, hooks and pre-commit

Every step is idempotent and never overwrites content it did not write.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

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
roots = []
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
    return lines


def install_config(config) -> str:
    path = config.root / CONFIG_NAME
    if path.exists():
        return f"kept     {CONFIG_NAME}"
    _write(path, CONFIG_TEMPLATE.format(project=config.root.name))
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
        if name == "SCHEMA.md":
            if config.wiki.sections != WikiConfig().sections:
                listed = "\n".join(f"- `{section}`" for section in config.wiki.sections)
                text = (
                    f"{text.rstrip()}\n\n## Page headings of this project\n\n"
                    f"Pages use these headings instead of the ones shown above:\n\n{listed}\n"
                )
        _write(path, text)
        done.append(f"created  {relative}")
    return done


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
    if args.config or args.all:
        steps.append(install_config(config))
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
    parser.add_argument("--all", action="store_true", help="config + wiki + hooks + pre-commit.")
    parser.set_defaults(func=_run)
