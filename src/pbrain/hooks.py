"""Agent hooks: ``pbrain hook <event>`` reads the hook payload (JSON) on stdin.

Events follow Claude Code's hook contract:

- ``pre-tool-use``  exit 2 with a message on stderr blocks a Write/Edit that rewrites a
  catalogued shared component.
- ``session-start`` prints context: shared knowledge sources and wiki pages that need review.
- ``stop``          once per session, asks the agent to reconcile wiki pages whose covered
  code changed in the working tree.

Hooks fail open. Any error, missing git, or broken config ends with exit code 0 and no
output, so a hook can never block the real work by mistake.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import ecosystem
from .gitutil import changed_files

STATE_DIR = ".pbrain/state"
MAX_LISTED = 12


def _payload() -> dict:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pre_tool_use(config, payload: dict) -> int:
    from .reuse import guard

    violations = guard.check_edit(config, payload.get("tool_name"), payload.get("tool_input") or {})
    if not violations:
        return 0
    sys.stderr.write("\n".join(violations) + "\n")
    sys.stderr.flush()
    return 2


def session_start(config, payload: dict) -> int:
    from . import wiki

    lines = []
    try:  # keep this project on the machine's review page, and the page running
        from . import registry, serve

        registry.remember(config)
        serve.ensure_server()
    except Exception:  # noqa: BLE001 - hooks fail open
        pass
    try:
        ecosystem.sync(config)
        shared = ecosystem.describe(config)
    except Exception:  # noqa: BLE001
        shared = []
    if shared:
        lines.append("Shared knowledge (read its index before inventing a shared rule or control):")
        lines += [f"- {line}" for line in shared]
    if config.wiki_dir.is_dir():
        findings, page_count = wiki.lint_wiki(config)
        flagged = sorted(
            {name for _, name, message in findings if "DRIFT" in message or "STALE" in message}
        )
        index = (config.wiki_dir / "index.md").relative_to(config.root).as_posix()
        lines.append(
            f"Project wiki: {index} ({page_count} pages). It holds decisions, business rules and "
            "gotchas that the code does not show; check it before changing a covered area."
        )
        if flagged:
            shown = ", ".join(flagged[:MAX_LISTED]) + (" ..." if len(flagged) > MAX_LISTED else "")
            lines.append(f"Wiki pages needing review ({len(flagged)}): {shown}")
    if lines:
        print("\n".join(lines))
    return 0


def _state_file(config, payload: dict) -> Path:
    session = "".join(
        c for c in str(payload.get("session_id") or "default") if c.isalnum() or c in "-_"
    )
    return config.root / STATE_DIR / f"{session or 'default'}.json"


def stop(config, payload: dict) -> int:
    from . import wiki

    if payload.get("stop_hook_active") or not config.wiki_dir.is_dir():
        return 0
    changed = changed_files(config.root)
    if not changed:
        return 0
    drifting = [page for page in wiki.pages_covering(config, changed) if page.get("drift")]
    if not drifting:
        return 0
    state_file = _state_file(config, payload)
    try:
        reminded = set(json.loads(state_file.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        reminded = set()
    fresh = [page for page in drifting if page["page"] not in reminded]
    if not fresh:
        return 0
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(sorted(reminded | {page["page"] for page in fresh})), encoding="utf-8"
        )
    except OSError:
        pass
    wiki_dir = config.wiki_dir.relative_to(config.root).as_posix()
    lines = [
        "pbrain: code covered by these wiki pages changed in the working tree:",
        *[
            f"- {wiki_dir}/{page['page']}: {'; '.join(page['drift'])}"
            for page in fresh[:MAX_LISTED]
        ],
        "If the change is yours: re-read each page. "
        "Still accurate -> `pbrain wiki confirm <page>`. "
        "Intent changed with the user's agreement -> update the page (or add a decision page). "
        "Code now differs from the intent -> record it under the page's gaps section and tell the "
        "user. If the change is not yours, say so and carry on.",
    ]
    print(json.dumps({"decision": "block", "reason": "\n".join(lines)}, ensure_ascii=False))
    return 0


EVENTS = {"pre-tool-use": pre_tool_use, "session-start": session_start, "stop": stop}


def _run(args, config) -> int:
    try:
        return int(EVENTS[args.event](config, _payload()) or 0)
    except Exception:  # noqa: BLE001 - hooks fail open
        return 0


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("hook", help="Run an agent hook (payload on stdin).")
    parser.add_argument("event", choices=sorted(EVENTS))
    parser.set_defaults(func=_run)
