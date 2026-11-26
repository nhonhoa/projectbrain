"""Reuse index: look up reusable components by purpose. Plain code, no LLM.

Two lookup tiers (``format_search``):

1. The catalog (``config.reuse.catalog``): components that are ALREADY shared, registered by
   people or agents.
2. All code under ``config.reuse.roots``: SQLite FTS5 over split names + docstrings/summaries +
   identifiers in the body. A hit here means "it already exists in a first place": move it to the
   shared area and reuse it.

The index lives at ``config.reuse.index`` (not committed) and refreshes itself from file
mtime/size on every call, so there is no manual rebuild step. One-line descriptions for symbols
without a docstring live in ``config.reuse.summaries`` (committed), keyed by source hash: when the
code changes the old description stops applying.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from ..config import Config
from ..text import words
from .catalog import load_catalog
from .extract import extract_symbols

SCHEMA_VERSION = "1"
# Same shape as a typical secret-scanning pre-commit rule: generated descriptions must never
# contain "keyword: value".
_SECRET_LIKE = re.compile(r"(?i)\b(secret|token|api_key|password|client_secret|private_key)\s*[:=]")

# Functions shorter than this many tokens (getters, one-line wrappers) need no description.
MIN_DESCRIBE = 8
# Names dictated by frameworks or convention: sharing a name across files says nothing.
COMMON_NAMES = frozenset(
    "main run get post put patch delete head options dispatch handle setup teardown init "
    "add_arguments save clean setUp tearDown get_queryset get_context_data get_urls ready "
    "index create update list show new edit destroy __init__ __str__ __repr__".split()
)

SCHEMA = """
CREATE TABLE files (path TEXT PRIMARY KEY, sig TEXT NOT NULL);
CREATE TABLE symbols (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL, doc TEXT NOT NULL, described INTEGER NOT NULL,
    norm TEXT NOT NULL, size INTEGER NOT NULL, top INTEGER NOT NULL, fp TEXT NOT NULL,
    src_hash TEXT NOT NULL
);
CREATE INDEX idx_symbols_path ON symbols(path);
CREATE VIRTUAL TABLE symbols_fts USING fts5(name, doc, body);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def common_names(config: Config) -> frozenset[str]:
    """Built-in generic names plus the project's own ``reuse.common_names``."""
    return COMMON_NAMES | frozenset(config.reuse.common_names)


def _connect(config: Config) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite file of the reuse index."""
    path = config.root / config.reuse.index
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _meta(connection, key):
    try:
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["value"] if row else None


def _signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def skipped_file(config: Config, name: str) -> bool:
    """Whether a file name matches ``skip_files``."""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in config.reuse.skip_files)


def source_files(config: Config):
    """Files that enter the index as ``(relative posix path, Path)``, skipping ``skip_parts``."""
    root = config.root
    suffixes = set(config.reuse.suffixes)
    skip = set(config.reuse.skip_parts)
    for top in config.reuse.roots:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if path.suffix not in suffixes or skip.intersection(relative.parts):
                continue
            if skipped_file(config, path.name) or not path.is_file():
                continue
            yield relative.as_posix(), path


def load_summaries(config: Config) -> dict:
    if not config.reuse.summaries:
        return {}
    path = config.root / config.reuse.summaries
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def refresh(config: Config) -> dict:
    """Sync the index with the sources; only changed files (mtime/size) are re-parsed."""
    summaries_file = config.root / config.reuse.summaries if config.reuse.summaries else None
    summaries_sig = _signature(summaries_file) if summaries_file and summaries_file.exists() else ""
    summaries = load_summaries(config)
    with closing(_connect(config)) as connection:
        if (
            _meta(connection, "schema") != SCHEMA_VERSION
            or _meta(connection, "summaries") != summaries_sig
        ):
            # Schema or summaries file changed: rebuild everything, it is cheap.
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name NOT LIKE 'symbols_fts_%'"
            ).fetchall():
                connection.execute(f"DROP TABLE IF EXISTS {name}")
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [("schema", SCHEMA_VERSION), ("summaries", summaries_sig)],
            )
        known = dict(connection.execute("SELECT path, sig FROM files").fetchall())
        seen, changed = set(), 0
        for relative_path, path in source_files(config):
            seen.add(relative_path)
            signature = _signature(path)
            if known.get(relative_path) == signature:
                continue
            changed += 1
            _drop_file(connection, relative_path)
            _index_file(
                connection,
                relative_path,
                path.read_text(encoding="utf-8", errors="replace"),
                summaries,
            )
            connection.execute("INSERT INTO files VALUES (?, ?)", (relative_path, signature))
        for relative_path in set(known) - seen:
            _drop_file(connection, relative_path)
        connection.commit()
        total = connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    return {"files": len(seen), "reindexed_files": changed, "symbols": total}


def _drop_file(connection, relative_path):
    connection.execute(
        "DELETE FROM symbols_fts WHERE rowid IN (SELECT id FROM symbols WHERE path = ?)",
        (relative_path,),
    )
    connection.execute("DELETE FROM symbols WHERE path = ?", (relative_path,))
    connection.execute("DELETE FROM files WHERE path = ?", (relative_path,))


def _index_file(connection, relative_path, source, summaries):
    """Write the symbols of a file to ``symbols`` + FTS, attaching summaries."""
    for symbol in extract_symbols(relative_path, source):
        summary = summaries.get(f"{relative_path}::{symbol['name']}") or {}
        if summary.get("h") != symbol["src_hash"]:
            summary = {}
        doc = symbol["doc"] or summary.get("s", "")
        cursor = connection.execute(
            "INSERT INTO symbols"
            " (path, name, kind, start, end, doc, described, norm, size, top, fp, src_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                relative_path,
                symbol["name"],
                symbol["kind"],
                symbol["start"],
                symbol["end"],
                doc,
                int(bool(doc)),
                symbol["norm"],
                symbol["size"],
                symbol["top"],
                symbol["fp"],
                symbol["src_hash"],
            ),
        )
        described = " ".join(
            words(f"{symbol['doc']} {summary.get('s', '')} {summary.get('k', '')}")
        )
        connection.execute(
            "INSERT INTO symbols_fts (rowid, name, doc, body) VALUES (?, ?, ?, ?)",
            (cursor.lastrowid, " ".join(words(symbol["name"])), described, symbol["text"]),
        )


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _has(query_word, target_words):
    """Whole-word match; long keywords (>= 4 chars) also match by prefix (format/formatter)."""
    return any(
        word == query_word or (len(query_word) >= 4 and word.startswith(query_word))
        for word in target_words
    )


def _query_words(terms):
    return list(dict.fromkeys(word for term in terms for word in words(term) if len(word) > 1))


def search_catalog(entries, terms, kind=None, limit=5):
    """Catalog entries matching the keywords (accent-free); with no keywords, list a whole ``kind``.

    Score: name hit 3, keywords hit 2, use_when hit 1 per keyword. Only entries scoring at least
    half of the best entry are kept: one generic keyword does not drag in the whole catalog.
    """
    query = _query_words(terms)
    entries = [entry for entry in entries if not kind or entry.get("kind") == kind]
    if not query:
        return entries if kind else []
    scored = []
    for entry in entries:
        name, keywords = words(entry["name"]), words(entry.get("keywords", ""))
        use_when = words(entry.get("use_when", ""))
        score = sum(3 * _has(q, name) + 2 * _has(q, keywords) + _has(q, use_when) for q in query)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: -item[0])
    return [entry for score, entry in scored[:limit] if score * 2 >= scored[0][0]]


def search_code(config: Config, terms, limit=6):
    """Symbols in all indexed code that match the keywords best.

    BM25: name weighs more than description, description more than body; functions rank above
    classes (a class is rarely the thing to reuse, yet long class docstrings match everything).
    """
    query = _query_words(terms)
    if not query:
        return []
    refresh(config)
    match = " OR ".join(f'"{word}"*' for word in query)
    with closing(_connect(config)) as connection:
        rows = connection.execute(
            "SELECT s.path, s.name, s.kind, s.start, s.doc FROM symbols_fts"
            " JOIN symbols s ON s.id = symbols_fts.rowid WHERE symbols_fts MATCH ?"
            " ORDER BY bm25(symbols_fts, 6.0, 3.0, 1.0)"
            " * (CASE WHEN s.size > 0 THEN 1.3 ELSE 1.0 END)"
            " LIMIT ?",
            (match, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _entry_line(entry):
    doc = f" -> {entry['doc']}" if entry.get("doc") else ""
    source = f" (from {entry['source']})" if entry.get("source") else ""
    return (
        f"{entry['name']} [{entry.get('kind', '')}] {entry.get('path', '')}"
        f" - {entry.get('use_when', '')}{doc}{source}"
    )


def format_search(config: Config, terms, kind=None, limit=6, extra_entries=()):
    """Lookup result as compact text for an agent: one result per line.

    ``extra_entries`` is an additional catalog (for example from shared knowledge repos); its hits
    are shown after the project's own, with the entry's ``source`` when it has one.
    """
    catalog = search_catalog(load_catalog(config), terms, kind=kind)
    lines = ["# Shared (catalog) - reuse it, do not write another version"]
    lines.extend(_entry_line(entry) for entry in catalog)
    if not catalog:
        lines.append("(none)")
    extra = search_catalog(list(extra_entries), terms, kind=kind) if extra_entries else []
    if extra:
        lines.append("# Shared by the ecosystem (other repositories)")
        lines.extend(_entry_line(entry) for entry in extra)
    if terms:
        registered = {(entry.get("path"), entry.get("symbol")) for entry in catalog}
        code = [
            row
            for row in search_code(config, terms, limit)
            if (row["path"], row["name"]) not in registered
        ]
        lines.append(
            "# Existing code (not registered) - if you reuse it: move it to the shared area "
            "and register it in the catalog"
        )
        for row in code:
            doc = f" - {row['doc'][:90]}" if row["doc"] else ""
            lines.append(f"{row['name']} [{row['kind']}] {row['path']}:{row['start']}{doc}")
        if not code:
            lines.append("(none)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Descriptions for symbols without a docstring (generated once by a cheap model, stored in JSON)
# ---------------------------------------------------------------------------


def undescribed(config: Config):
    """Symbols worth looking up that have neither a docstring/comment nor a valid description."""
    refresh(config)
    with closing(_connect(config)) as connection:
        rows = connection.execute(
            "SELECT path, name, start, end, src_hash FROM symbols"
            " WHERE described = 0 AND (size >= ? OR (size = 0 AND end - start >= 5))"
            " ORDER BY path, start",
            (MIN_DESCRIBE,),
        ).fetchall()
    common = common_names(config)
    return [dict(row) for row in rows if row["name"].rsplit(".", 1)[-1] not in common]


def sanitize_text(text):
    """Turn ``:``/``=`` after a secret-like word into `` -`` so text never looks like a secret."""
    return _SECRET_LIKE.sub(r"\1 -", text)


def import_summaries(config: Config, items) -> int:
    """Merge ``{"path::name": {"s": description, "k": keywords}}`` into the summaries file.

    Each entry is stamped with the current source hash; keys that no longer match a symbol are
    dropped. Returns the number of descriptions written.
    """
    if not config.reuse.summaries:
        raise ValueError("reuse.summaries is not configured")
    refresh(config)
    with closing(_connect(config)) as connection:
        hashes = {
            f"{row['path']}::{row['name']}": row["src_hash"]
            for row in connection.execute("SELECT path, name, src_hash FROM symbols")
        }
    summaries = load_summaries(config)
    written = 0
    for key, value in items.items():
        if key in hashes and value.get("s"):
            summaries[key] = {
                "h": hashes[key],
                "s": sanitize_text(value["s"].strip()),
                "k": sanitize_text(value.get("k", "").strip()),
            }
            written += 1
    summaries = {key: value for key, value in summaries.items() if hashes.get(key) == value["h"]}
    body = ",\n".join(
        f"{json.dumps(key, ensure_ascii=False)}: {json.dumps(summaries[key], ensure_ascii=False)}"
        for key in sorted(summaries)
    )
    target = config.root / config.reuse.summaries
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    return written
