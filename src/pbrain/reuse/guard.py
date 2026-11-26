"""Edit guard: block REWRITING a component that the reuse catalog already provides.

``check_edit`` takes the payload of a Claude Code Write/Edit/MultiEdit tool call and returns the
violations. It only reports NEW violations: the number of ``instead_of`` matches must grow
compared with the old content. It never reads stdin and never exits; a hook wrapper does that.
"""

from __future__ import annotations

import os
import re

from ..config import Config
from .catalog import load_catalog
from .store import skipped_file


def relative_path(config: Config, file_path: str) -> str | None:
    """Root-relative posix path of ``file_path``; None when it lies outside the project."""
    absolute = file_path if os.path.isabs(file_path) else os.path.join(config.root, file_path)
    try:
        rel = os.path.relpath(os.path.normpath(absolute), config.root)
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    return None if rel == ".." or rel.startswith("../") else rel


def in_scope(config: Config, rel: str) -> bool:
    """Same scope as the code index: under a root, a known suffix, not in a skipped directory."""
    reuse = config.reuse
    parts = rel.split("/")
    if not any(rel == top or rel.startswith(top.rstrip("/") + "/") for top in reuse.roots):
        return False
    if not rel.endswith(tuple(reuse.suffixes)):
        return False
    if set(reuse.skip_parts).intersection(parts):
        return False
    if skipped_file(config, parts[-1]):
        return False
    return rel != reuse.catalog.replace("\\", "/")


def _changes(config: Config, tool_name, tool_input, rel):
    """List of ``(old content, new content)`` pairs that this write produces."""
    if tool_name == "Write":
        old = ""
        path = config.root / rel
        if path.is_file():
            old = path.read_text(encoding="utf-8", errors="replace")
        return [(old, tool_input.get("content") or "")]
    if tool_name == "Edit":
        return [(tool_input.get("old_string") or "", tool_input.get("new_string") or "")]
    if tool_name == "MultiEdit":
        return [
            (edit.get("old_string") or "", edit.get("new_string") or "")
            for edit in tool_input.get("edits") or []
        ]
    return []


def check_edit(config: Config, tool_name: str, tool_input: dict | None) -> list[str]:
    """Human-readable violations for one tool call; an empty list means the edit is fine."""
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return []
    tool_input = tool_input or {}
    rel = relative_path(config, tool_input.get("file_path") or "")
    if not rel or not in_scope(config, rel):
        return []
    catalog = load_catalog(config)
    if not catalog:
        return []
    pairs = _changes(config, tool_name, tool_input, rel)
    found = []
    for entry in catalog:
        if entry.get("path") == rel:
            continue
        for pattern in entry.get("instead_of") or ():
            try:
                regex = re.compile(pattern)
            except re.error:
                continue
            if any(len(regex.findall(new)) > len(regex.findall(old)) for old, new in pairs):
                found.append((entry, pattern))
    if not found:
        return []
    lines = [f"Reuse-first: {rel} is rewriting a component that is already shared:"]
    for entry, pattern in found:
        symbol = entry.get("symbol") or "-"
        lines.append(
            f"- matches pattern {pattern} -> use {entry['name']} ({entry['path']}, symbol {symbol})"
            " "
            "instead of rewriting it; see `pbrain reuse <keywords>`"
        )
    return lines
