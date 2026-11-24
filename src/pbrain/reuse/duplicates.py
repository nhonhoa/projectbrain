"""Duplicate detection on the reuse index: compares names and body fingerprints, no LLM.

``find_duplicates`` checks the NEW functions of a diff; ``audit`` scans all indexed code for a
periodic review.
"""

from __future__ import annotations

import subprocess
from contextlib import closing
from pathlib import Path

from ..config import Config
from ..text import words
from .extract import extract_symbols
from .store import _connect, common_names, refresh

# Thresholds in body tokens. Very short functions (getters, one-line wrappers) look alike all the
# time, so: identical bodies count from MIN_EXACT, near-identical ones from MIN_TOKENS.
MIN_EXACT = 12
MIN_TOKENS = 25
SIMILAR_BODY = 0.6
SIMILAR_NAME = 0.67
SHINGLE = 4


def _shingles(norm):
    """Set of SHINGLE consecutive tokens; two sets compared by Jaccard give body similarity."""
    tokens = norm.split()
    return {hash(tuple(tokens[i : i + SHINGLE])) for i in range(max(len(tokens) - SHINGLE + 1, 1))}


def _cached_shingles(symbol, cache):
    """Shingle set of one symbol, computed once per run."""
    if symbol["id"] not in cache:
        cache[symbol["id"]] = _shingles(symbol["norm"])
    return cache[symbol["id"]]


def _all_symbols(connection):
    """Every indexed symbol with its comparison data (name, normalised tokens, fingerprint)."""
    return [
        dict(row)
        for row in connection.execute(
            "SELECT id, path, name, kind, start, norm, size, top, fp FROM symbols"
        )
    ]


def _head_names(root, relative_path):
    """Symbol names of the file at git HEAD; None when untracked (all new)."""
    try:
        done = subprocess.run(
            ["git", "show", f"HEAD:{relative_path}"],
            capture_output=True,
            cwd=root,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return {symbol["name"] for symbol in extract_symbols(relative_path, done.stdout)}


def _compare(new, other, cache, common):
    """Why ``other`` counts as a duplicate of ``new`` (with a strength for ranking), or None."""
    if new["size"] >= MIN_EXACT and new["fp"] == other["fp"]:
        return 1.0, "identical body after ignoring variable names"
    if new["size"] >= MIN_TOKENS and other["size"] >= MIN_TOKENS:
        if 0.5 <= other["size"] / new["size"] <= 2:
            a, b = _cached_shingles(new, cache), _cached_shingles(other, cache)
            similarity = len(a & b) / len(a | b)
            if similarity >= SIMILAR_BODY:
                return similarity, f"body {similarity:.0%} similar"
    if new["top"] and other["top"] and new["kind"] == "py" and new["path"] != other["path"]:
        if new["name"] == other["name"] and new["name"] not in common:
            return 0.5, "same function name in another file"
        a, b = set(words(new["name"])), set(words(other["name"]))
        if len(a) >= 2 and len(a & b) / len(a | b) >= SIMILAR_NAME:
            return 0.4, "similar function name"
    return None


def find_duplicates(config: Config, changed_paths, max_hits=3) -> list[dict]:
    """NEW functions in the changed files that resemble functions already elsewhere.

    "New" means absent from the file at git HEAD. Each finding is one record for the agent to
    decide: reuse / move the old one to the shared area, or justify keeping a private copy.
    """
    refresh(config)
    root = config.root
    findings = []
    with closing(_connect(config)) as connection:
        symbols = _all_symbols(connection)
    common = common_names(config)
    cache = {}
    for raw_path in changed_paths:
        relative_path = Path(raw_path).as_posix()
        old_names = _head_names(root, relative_path)
        for new in symbols:
            if new["path"] != relative_path or (old_names is not None and new["name"] in old_names):
                continue
            hits = []
            for other in symbols:
                if other["id"] == new["id"] or other["kind"] != new["kind"]:
                    continue
                result = _compare(new, other, cache, common)
                if result:
                    hits.append((result[0], other, result[1]))
            for _, other, why in sorted(hits, key=lambda hit: -hit[0])[:max_hits]:
                findings.append(
                    {
                        "new": f"{new['path']}:{new['start']} {new['name']}",
                        "existing": f"{other['path']}:{other['start']} {other['name']}",
                        "why": why,
                    }
                )
    return findings


def audit(config: Config) -> list[dict]:
    """Suspected duplicate clusters over ALL code: identical bodies or same-name functions."""
    refresh(config)
    with closing(_connect(config)) as connection:
        symbols = _all_symbols(connection)
    common = common_names(config)
    groups = {}
    for symbol in symbols:
        if symbol["size"] >= MIN_EXACT:
            groups.setdefault(("identical body", symbol["kind"], symbol["fp"]), []).append(symbol)
        if symbol["top"] and symbol["kind"] == "py" and symbol["name"] not in common:
            groups.setdefault(("same function name", "py", symbol["name"]), []).append(symbol)
    clusters = []
    for (why, _, _), members in groups.items():
        if why == "same function name" and len({member["path"] for member in members}) < 2:
            continue
        if len(members) >= 2:
            clusters.append(
                {"why": why, "members": [f"{m['path']}:{m['start']} {m['name']}" for m in members]}
            )
    return sorted(clusters, key=lambda cluster: -len(cluster["members"]))
