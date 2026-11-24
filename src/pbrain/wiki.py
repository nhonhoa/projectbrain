"""Lint and maintain the project knowledge wiki (conventions: templates/wiki/SCHEMA.md).

Errors (exit code 1): bad front matter, ``covers``/``sources`` paths that do not exist, broken
links, pages missing from ``index.md``. Warnings: pages that drifted from the code, pages not
confirmed for too long, missing body sections, pages that are too long.

Drift is measured by content, not by commit date: the last commit up to the end of the
``verified`` day is the baseline, compared with the file as it is now. A ``covers`` entry such
as ``src/app/billing.py::Invoice.total`` compares only that function body, so editing another
function in the same file does not flag the page. An entry without ``::`` compares the whole file.

``wiki confirm`` also stores ``verified_hash``, a fingerprint of the covered code at that moment.
A page whose fingerprint still matches is not drifted, even if the code is uncommitted.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from datetime import date
from pathlib import Path

RESERVED = {"index.md", "log.md", "SCHEMA.md"}
ENUMS = {
    "type": {"concept", "feature", "rule", "decision", "contract", "gotcha"},
    "status": {"active", "superseded", "deprecated"},
    "trust": {"agreed", "requested", "inferred"},
}
REQUIRED = ("title", "type", "status", "trust", "verified")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def parse_front_matter(text):
    """Read the leading ``---`` block (YAML subset: ``key: value`` and ``- item`` lists)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    meta, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        line = line.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(item.group(1).strip().strip("\"'"))
            continue
        pair = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if pair:
            key, value = pair.group(1), pair.group(2).strip().strip("\"'")
            meta[key] = value if value else []
    return None


class Git:
    """Runs git commands in one repository, caching results for the life of the object."""

    def __init__(self, root):
        self.root = root
        self._cache = {}

    def output(self, *args):
        """Stdout of a git command; None if it fails or git is unavailable."""
        if args not in self._cache:
            try:
                done = subprocess.run(
                    ["git", *args],
                    capture_output=True,
                    cwd=self.root,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                self._cache[args] = done.stdout if done.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                self._cache[args] = None
        return self._cache[args]

    def changed_since(self, base):
        """Paths that differ between commit ``base`` and the working tree, untracked included.

        None when git cannot tell; callers then compare file contents one by one.
        """
        tracked = self.output("diff", "--name-only", "-z", base, "--")
        untracked = self.output("ls-files", "--others", "--exclude-standard", "-z")
        if tracked is None or untracked is None:
            return None
        return {name for name in (tracked + untracked).split("\0") if name}


def symbol_source(text, symbol):
    """Source of ``Class.method`` in Python text, with decorators; None if absent or invalid."""
    try:
        body = ast.parse(text).body
    except (SyntaxError, ValueError):
        return None
    node = None
    for name in symbol.split("."):
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        node = next((n for n in body if isinstance(n, kinds) and n.name == name), None)
        if node is None:
            return None
        body = node.body
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return "\n".join(text.splitlines()[start - 1 : node.end_lineno])


def has_symbol(text, file_path, symbol):
    """Whether the file defines ``symbol``; non-Python files only get a name search."""
    if file_path.endswith(".py"):
        return symbol_source(text, symbol) is not None
    return symbol.rsplit(".", 1)[-1] in text


def normalized(text):
    """Drop line-ending differences and trailing whitespace before comparing."""
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def drift(git, cover, verified):
    """Compare one ``covers`` entry with the last commit up to ``verified``; describe the drift."""
    file_path, _, symbol = cover.partition("::")
    before = f"--before={verified}T23:59:59"
    base = (git.output("rev-list", "-1", before, "HEAD") or "").strip()
    if not base:
        return None
    changed = git.changed_since(base)
    if changed is not None and file_path.replace("\\", "/") not in changed:
        return None  # one git call answers for every unchanged file
    current = (git.root / file_path).read_text(encoding="utf-8", errors="replace")
    old = git.output("show", f"{base}:{file_path}")
    by_symbol = bool(symbol) and file_path.endswith(".py")
    if by_symbol:
        current, old = symbol_source(current, symbol), old and symbol_source(old, symbol)
    if current is None:
        return None
    if old is None:
        return f"`{cover}` did not exist at commit {base[:8]} (verified baseline {verified})"
    if normalized(current) != normalized(old):
        what = "function body" if by_symbol else "file"
        return f"`{cover}`: {what} changed since commit {base[:8]} (verified baseline {verified})"
    return None


def _as_list(value):
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def covers_hash(config, covers):
    """Fingerprint (16 hex chars) of the current content of every ``covers`` entry.

    Each entry contributes its name and its normalized source (function body for
    ``file.py::symbol``, whole file otherwise); an unreadable entry contributes ``<missing>``.
    """
    digest = hashlib.sha256()
    for cover in sorted(covers):
        file_path, _, symbol = cover.partition("::")
        try:
            content = (config.root / file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = None
        if content is not None and symbol and file_path.endswith(".py"):
            content = symbol_source(content, symbol)
        text = "<missing>" if content is None else normalized(content)
        digest.update(f"{cover}\0{text}\0".encode())
    return digest.hexdigest()[:16]


def page_drift(config, git, covers, verified, stored_hash, shown=None):
    """Drift messages for the ``covers`` entries of one page (only ``shown`` ones if given).

    A page whose ``verified_hash`` equals the current fingerprint is not drifted. Otherwise git
    describes what changed; a stored hash that no longer matches always yields a message.
    """
    if stored_hash and stored_hash == covers_hash(config, covers):
        return []
    found = []
    for cover in covers if shown is None else shown:
        if (config.root / cover.partition("::")[0]).is_file():
            changed = drift(git, cover, verified)
            if changed:
                found.append(changed)
    if stored_hash and not found:
        found.append("covered code changed since the page was confirmed")
    return found


def _is_external_id(source):
    """A source with no ``/`` and no file extension (commit hash, lesson id) is not a path."""
    file_part = source.partition("::")[0]
    return "/" not in file_part and "\\" not in file_part and "." not in file_part


def lint_page(config, page, max_age, today, git):
    """Return ``(errors, warnings)`` for one wiki page."""
    root = config.root
    errors, warnings = [], []
    text = page.read_text(encoding="utf-8")
    meta = parse_front_matter(text)
    if meta is None:
        return ["missing or malformed front matter block"], warnings

    for field in REQUIRED:
        if not meta.get(field):
            errors.append(f"missing field `{field}`")
    for field, allowed in ENUMS.items():
        value = meta.get(field)
        if value and value not in allowed:
            errors.append(f"`{field}: {value}` is not one of {sorted(allowed)}")
    if meta.get("status") == "superseded" and not meta.get("superseded_by"):
        errors.append("status is superseded but `superseded_by` is missing")

    verified = None
    try:
        verified = date.fromisoformat(str(meta.get("verified", "")))
    except ValueError:
        if meta.get("verified"):
            errors.append(f"`verified: {meta['verified']}` is not a YYYY-MM-DD date")

    covers = _as_list(meta.get("covers"))
    sources = _as_list(meta.get("sources"))
    for field, paths in (("covers", covers), ("sources", sources)):
        for path in paths:
            if field == "sources" and (URL.match(path) or _is_external_id(path)):
                continue
            file_path, _, symbol = path.partition("::")
            target = root / file_path
            if not target.exists():
                errors.append(f"{field}: `{path}` does not exist")
            elif symbol and not has_symbol(
                target.read_text(encoding="utf-8", errors="replace"), file_path, symbol
            ):
                errors.append(f"{field}: `{symbol}` not found in `{file_path}`")

    if verified and meta.get("status") == "active":
        stored = str(meta.get("verified_hash") or "")
        for changed in page_drift(config, git, covers, verified.isoformat(), stored):
            warnings.append(f"DRIFT: {changed}")
        age = (today - verified).days
        if age > max_age:
            warnings.append(f"STALE: not confirmed for {age} days (limit {max_age})")

    for target in LINK.findall(text):
        if URL.match(target) or target.startswith("mailto:"):
            continue
        if not (page.parent / target).resolve().exists():
            errors.append(f"broken link: `{target}`")

    for section in config.wiki.sections:
        if section not in text:
            warnings.append(f"missing section `{section}`")
    line_count = text.count("\n") + 1
    if line_count > config.wiki.max_lines:
        warnings.append(f"{line_count} lines long (keep under {config.wiki.max_lines})")
    return errors, warnings


def _page_files(wiki):
    return sorted(p for p in wiki.glob("*.md") if p.name not in RESERVED)


def lint_wiki(config, today=None, max_age=None):
    """Lint the wiki directory; return ``(findings, page_count)``.

    A finding is ``(level, page_name, message)`` with level ``"ERROR"`` or ``"WARNING"``.
    """
    wiki = config.wiki_dir
    if not wiki.is_dir():
        return [], 0
    today = today or date.today()
    max_age = config.wiki.max_age_days if max_age is None else max_age
    git = Git(config.root)
    findings = []
    pages = _page_files(wiki)
    index = wiki / "index.md"
    indexed = set()
    if index.exists():
        indexed = {Path(t).name for t in LINK.findall(index.read_text(encoding="utf-8"))}
        for target in sorted(indexed):
            if not (wiki / target).exists():
                findings.append(("ERROR", "index.md", f"broken link: `{target}`"))
    else:
        findings.append(("ERROR", "index.md", "index file is missing"))
    if not (wiki / "log.md").exists():
        findings.append(("WARNING", "log.md", "log file is missing"))

    for page in pages:
        errors, warnings = lint_page(config, page, max_age, today, git)
        if page.name not in indexed:
            errors.append("ORPHAN: not listed in index.md")
        findings += [("ERROR", page.name, message) for message in errors]
        findings += [("WARNING", page.name, message) for message in warnings]
    return findings, len(pages)


def _normalize_path(path):
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def pages_covering(config, paths):
    """Active pages whose ``covers`` entries point into any of ``paths`` (relative to the root)."""
    wiki = config.wiki_dir
    if not wiki.is_dir():
        return []
    wanted = {_normalize_path(p) for p in paths}
    git = Git(config.root)
    result = []
    for page in _page_files(wiki):
        meta = parse_front_matter(page.read_text(encoding="utf-8")) or {}
        if meta.get("status") != "active":
            continue
        matched = [c for c in _as_list(meta.get("covers")) if c.partition("::")[0] in wanted]
        if not matched:
            continue
        verified = str(meta.get("verified", ""))
        try:
            date.fromisoformat(verified)
        except ValueError:
            verified = ""
        found = []
        if verified:
            stored = str(meta.get("verified_hash") or "")
            all_covers = _as_list(meta.get("covers"))
            found = page_drift(config, git, all_covers, verified, stored, shown=matched)
        result.append(
            {
                "page": page.name,
                "title": meta.get("title", ""),
                "covers": matched,
                "drift": found,
                "verified": verified,
            }
        )
    return result


def resolve_page(config, name):
    """File name of a wiki page given as ``x``, ``x.md`` or a path to it; None if not found."""
    raw = str(name).strip().replace("\\", "/")
    if not raw:
        return None
    options = [raw] if raw.endswith(".md") else [raw, raw + ".md"]
    wiki = config.wiki_dir.resolve()
    for option in options:
        path = Path(option)
        if "/" in option:
            path = path if path.is_absolute() else config.root / path
        else:
            path = config.wiki_dir / option
        if path.is_file() and path.resolve().parent == wiki:
            return path.name
    return None


def confirm(config, page_names, today=None):
    """Mark pages as verified today and fingerprint their ``covers``; return the file names.

    Sets ``verified`` and writes ``verified_hash`` right after it (dropped when the page has no
    ``covers``). Names may be given with or without ``.md`` or as a path into the wiki directory.
    """
    today = (today or date.today()).isoformat()
    changed = []
    for given in page_names:
        name = resolve_page(config, given)
        if name is None:
            continue
        page = config.wiki_dir / name
        raw = page.read_bytes().decode("utf-8")
        lines = raw.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            continue
        end = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), None)
        if end is None:
            continue
        for number in range(1, end):
            match = re.match(
                r"^(verified:[ \t]*)[^\r\n#]*?([ \t]*(?:#.*)?)(\r?\n?)$", lines[number]
            )
            if not match:
                continue
            newline = match.group(3) or "\n"
            lines[number] = f"{match.group(1)}{today}{match.group(2)}{newline}"
            covers = _as_list((parse_front_matter(raw) or {}).get("covers"))
            extra = [f"verified_hash: {covers_hash(config, covers)}{newline}"] if covers else []
            kept = [n for n in range(1, end) if not lines[n].startswith("verified_hash:")]
            head = [lines[n] for n in kept]
            at = kept.index(number) + 1
            lines = [lines[0]] + head[:at] + extra + head[at:] + lines[end:]
            page.write_bytes("".join(lines).encode("utf-8"))
            changed.append(name)
            break
    return changed


def _lint_command(args, config):
    if not config.wiki_dir.is_dir():
        print(f"No wiki directory at {config.wiki.dir}; nothing to lint.")
        return 0
    findings, page_count = lint_wiki(config, max_age=args.max_age)
    for level, name, message in findings:
        print(f"{level}  {name}: {message}")
    error_count = sum(1 for level, _, _ in findings if level == "ERROR")
    print(f"-- {page_count} pages, {error_count} errors, {len(findings) - error_count} warnings")
    return 1 if error_count else 0


def _touching_command(args, config):
    hits = pages_covering(config, args.files)
    if not hits:
        print("No active wiki page covers these files.")
        return 0
    for hit in hits:
        print(f"{hit['page']}  {hit['title']}  (verified {hit['verified'] or '?'})")
        print(f"    covers: {', '.join(hit['covers'])}")
        for line in hit["drift"]:
            print(f"    DRIFT: {line}")
    return 0


def _confirm_command(args, config):
    done = confirm(config, args.pages)
    for name in done:
        print(f"confirmed {name}")
    missing = [p for p in args.pages if resolve_page(config, p) not in done]
    for name in missing:
        print(f"not found: {name}")
    return 1 if missing else 0


def add_parser(subparsers):
    parser = subparsers.add_parser("wiki", help="Lint and maintain the knowledge wiki.")
    commands = parser.add_subparsers(dest="wiki_command", required=True)

    lint = commands.add_parser("lint", help="Check wiki pages against the schema and the code.")
    lint.add_argument("--max-age", type=int, default=None, help="Days before a page is stale.")
    lint.set_defaults(func=_lint_command)

    touching = commands.add_parser("touching", help="List pages covering the given files.")
    touching.add_argument("files", nargs="+", help="Paths relative to the project root.")
    touching.set_defaults(func=_touching_command)

    confirm_parser = commands.add_parser(
        "confirm", help="Set `verified` to today and fingerprint the covered code."
    )
    confirm_parser.add_argument("pages", nargs="+", help="Page names or paths into the wiki.")
    confirm_parser.set_defaults(func=_confirm_command)
