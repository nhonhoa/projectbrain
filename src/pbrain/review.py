"""``pbrain review``: a local web page to clear everything that waits for a human decision.

Pending decisions are wiki pages whose trust is not ``agreed``, sections parked in
``<wiki>/_inbox/``, pages whose covered code drifted, and the remaining lint findings.
The functions below are pure (no HTTP) so they can be tested directly; the request handler at
the end is a thin layer over them. Everything runs locally and nothing is ever pushed.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import socket
import subprocess
import threading
import unicodedata
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config as config_module
from . import wiki

INBOX = "_inbox"
MAX_BODY = 2 * 1024 * 1024
DIFF_LINES = 400
KIND_ORDER = ("lint_error", "drift", "inbox", "trust_requested", "lint_warning")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class ReviewError(Exception):
    """A request that cannot be carried out; ``status`` is the HTTP status to answer with."""

    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


# ---------------------------------------------------------------- file helpers


def _inside(base: Path, relative: str, suffix=".md") -> Path:
    """``base / relative`` if it resolves inside ``base`` and has ``suffix``; else ReviewError."""
    raw = str(relative or "").strip()
    if not raw or "\0" in raw:
        raise ReviewError("missing path")
    if Path(raw).is_absolute() or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ReviewError(f"path outside the wiki: {raw}")
    path = (base / raw).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        raise ReviewError(f"path outside the wiki: {raw}") from None
    if suffix and path.suffix.lower() != suffix:
        raise ReviewError(f"not a {suffix} file: {raw}")
    return path


def _page_path(config, page) -> Path:
    path = _inside(config.wiki_dir, page)
    if not path.is_file():
        raise ReviewError(f"no such page: {page}", 404)
    return path


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ReviewError(f"{path.name} is not valid UTF-8") from None


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


# ---------------------------------------------------------------- queue


def _inbox_dir(config) -> Path:
    return config.wiki_dir / INBOX


def split_inbox(text: str):
    """``(preamble_lines, sections)`` of an inbox file; a section is ``(heading, lines)``.

    ``lines`` holds the section body without its heading line. ``## `` inside a fenced code
    block does not start a section.
    """
    preamble, sections, fenced = [], [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _inbox_files(config):
    inbox = _inbox_dir(config)
    return sorted(inbox.glob("*.md")) if inbox.is_dir() else []


def _short(text: str, limit=140) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1] + "..."


def _item_id(*parts) -> str:
    return ":".join(str(p) for p in parts)


def build_queue(config) -> list[dict]:
    """Everything waiting for a decision in one project, most urgent first."""
    wiki_dir = config.wiki_dir
    if not wiki_dir.is_dir():
        return []
    findings, _ = wiki.lint_wiki(config)
    metas = {}
    for page in wiki._page_files(wiki_dir):
        meta = wiki.parse_front_matter(_read(page))
        if meta is not None:
            metas[page.name] = meta
    queue = {kind: [] for kind in KIND_ORDER}

    lint = {}
    for level, name, message in findings:
        meta = metas.get(name, {})
        base = {"page": name, "title": meta.get("title") or name, "detail": message}
        if message.startswith("DRIFT:"):
            digest = hashlib.sha1(message.encode("utf-8")).hexdigest()[:8]
            item = dict(base, kind="drift", covers=wiki._as_list(meta.get("covers")))
            item["id"] = _item_id("drift", name, digest)
            queue["drift"].append(item)
            continue
        # One item per page and severity: a page missing three sections is one decision.
        severity = "error" if level == "ERROR" else "warning"
        item = lint.get((name, severity))
        if item is None:
            item = dict(base, kind="lint", severity=severity, messages=[])
            item["id"] = _item_id("lint", name, severity)
            lint[(name, severity)] = item
            queue[f"lint_{severity}"].append(item)
        item["messages"].append(message)
        item["detail"] = "; ".join(item["messages"])

    for file in _inbox_files(config):
        _, sections = split_inbox(_read(file))
        for heading, lines in sections:
            body = "".join(lines).strip()
            queue["inbox"].append(
                {
                    "id": _item_id("inbox", file.name, heading),
                    "kind": "inbox",
                    "page": f"{INBOX}/{file.name}",
                    "file": file.name,
                    "heading": heading,
                    "title": heading,
                    "detail": _short(body) or file.name,
                    "body": body,
                }
            )

    for name, meta in metas.items():
        trust = str(meta.get("trust") or "")
        # ``inferred`` is a resting state (read back from code), not a decision someone owes.
        if meta.get("status") != "active" or trust != "requested":
            continue
        queue["trust_requested"].append(
            {
                "id": _item_id("trust", name),
                "kind": "trust",
                "page": name,
                "title": meta.get("title") or name,
                "detail": f"trust: {trust} - {meta.get('type', '')}",
                "trust": trust,
            }
        )
    return [item for kind in KIND_ORDER for item in queue[kind]]


# ---------------------------------------------------------------- page actions


def read_page(config, page) -> dict:
    text = _read(_page_path(config, page))
    return {"text": text, "sha": _sha(text), "meta": wiki.parse_front_matter(text) or {}}


def save_page(config, page, text, sha) -> dict:
    path = _page_path(config, page)
    current = _read(path)
    if _sha(current) != sha:
        raise ReviewError(
            "The file changed on disk since you opened it. Reload before saving.",
            409,
            conflict=True,
        )
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if _newline(current) == "\r\n":
        text = text.replace("\n", "\r\n")
    _write(path, text)
    return {"message": f"Saved {path.name}", "sha": _sha(text)}


def _set_front_matter(config, page, key, value) -> dict:
    path = _page_path(config, page)
    text = _read(path)
    lines = text.splitlines(keepends=True)
    end = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), None)
    if not lines or lines[0].strip() != "---" or end is None:
        raise ReviewError(f"{path.name} has no front matter")
    newline = _newline(text)
    pattern = re.compile(rf"^({re.escape(key)}:[ \t]*)[^\r\n#]*?([ \t]*(?:#.*)?)(\r?\n?)$")
    for number in range(1, end):
        match = pattern.match(lines[number])
        if match:
            lines[number] = f"{match.group(1)}{value}{match.group(2)}{match.group(3)}"
            break
    else:
        lines.insert(end, f"{key}: {value}{newline}")
    _write(path, "".join(lines))
    return {"message": f"{path.name}: {key} set to {value}"}


def set_trust(config, page, trust) -> dict:
    if trust not in wiki.ENUMS["trust"]:
        raise ReviewError(f"trust must be one of {sorted(wiki.ENUMS['trust'])}")
    return _set_front_matter(config, page, "trust", trust)


def set_status(config, page, status) -> dict:
    if status not in wiki.ENUMS["status"]:
        raise ReviewError(f"status must be one of {sorted(wiki.ENUMS['status'])}")
    return _set_front_matter(config, page, "status", status)


def confirm_page(config, page) -> dict:
    path = _page_path(config, page)
    if not wiki.confirm(config, [path.name]):
        raise ReviewError(f"could not confirm {path.name}")
    return {"message": f"Confirmed {path.name}"}


def page_diff(config, page) -> str:
    """``git diff`` of the files a page covers, since the commit its drift is measured from."""
    path = _page_path(config, page)
    meta = wiki.parse_front_matter(_read(path)) or {}
    covers = wiki._as_list(meta.get("covers"))
    named = {c.partition("::")[0] for c in covers}
    files = sorted(f for f in named if (config.root / f).is_file())
    if not files:
        return "(this page covers no existing file)"
    git = wiki.Git(config.root)
    verified = str(meta.get("verified", ""))
    base = (git.output("rev-list", "-1", f"--before={verified}T23:59:59", "HEAD") or "").strip()
    out = git.output("diff", base, "--", *files) if base else None
    if out is None:
        parts = []
        for cover in covers:
            file_path, _, symbol = cover.partition("::")
            if file_path not in files:
                continue
            text = (config.root / file_path).read_text(encoding="utf-8", errors="replace")
            source = wiki.symbol_source(text, symbol) if symbol else text
            note = "current source; no baseline commit to diff against"
            parts.append(f"# {cover} ({note})\n{source}")
        out = "\n\n".join(parts)
    elif not out.strip():
        out = f"(no committed or tracked change since {base[:8]}; check untracked files)"
    lines = out.splitlines()
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + [f"... truncated, {len(lines) - DIFF_LINES} more lines"]
    return "\n".join(lines)


# ---------------------------------------------------------------- inbox actions


def _inbox_section(config, file, heading):
    path = _inside(_inbox_dir(config), file)
    if not path.is_file():
        raise ReviewError(f"no such inbox file: {file}", 404)
    text = _read(path)
    preamble, sections = split_inbox(text)
    for index, (name, _) in enumerate(sections):
        if name == heading:
            return path, text, preamble, sections, index
    raise ReviewError(f"no section '{heading}' in {file}", 404)


def _drop_section(path, text, preamble, sections, index) -> None:
    del sections[index]
    if not sections:
        path.unlink()
        return
    out = list(preamble)
    for name, lines in sections:
        out.append(f"## {name}{_newline(text)}")
        out.extend(lines)
    _write(path, "".join(out))


def inbox_discard(config, file, heading) -> dict:
    path, text, preamble, sections, index = _inbox_section(config, file, heading)
    _drop_section(path, text, preamble, sections, index)
    return {"message": f"Discarded '{heading}'"}


def slugify(name: str) -> str:
    name = str(name).strip()
    if name.lower().endswith(".md"):
        name = name[:-3]
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _clean_line(text: str) -> str:
    return re.sub(r"\s+#", " ", " ".join(str(text).split())).strip()


def inbox_to_page(config, file, heading, name, title, type, trust) -> dict:  # noqa: A002
    path, text, preamble, sections, index = _inbox_section(config, file, heading)
    if type not in wiki.ENUMS["type"]:
        raise ReviewError(f"type must be one of {sorted(wiki.ENUMS['type'])}")
    if trust not in wiki.ENUMS["trust"]:
        raise ReviewError(f"trust must be one of {sorted(wiki.ENUMS['trust'])}")
    slug = slugify(name)
    if not slug:
        raise ReviewError("page name is empty")
    filename = f"{slug}.md"
    if filename in wiki.RESERVED:
        raise ReviewError(f"{filename} is reserved")
    target = _inside(config.wiki_dir, filename)
    if target.exists():
        raise ReviewError(f"{filename} already exists", 409)
    title = _clean_line(title) or heading
    body = "".join(sections[index][1]).strip()
    nl = "\n"
    out = [
        "---",
        f"title: {title}",
        f"type: {type}",
        "status: active",
        f"trust: {trust}",
        f"verified: {date.today().isoformat()}",
        "---",
    ]
    for number, section in enumerate(config.wiki.sections):
        out.append(section)
        out.extend(body.splitlines() if number == 0 and body else [])
    config.wiki_dir.mkdir(parents=True, exist_ok=True)
    _write(target, nl.join(out) + nl)

    summary = _short(re.sub(r"[#*`>\[\]]", "", body.splitlines()[0]) if body else title, 100)
    link = f"- [{title}]({filename}) - {summary}"
    index_path = config.wiki_dir / "index.md"
    existing = _read(index_path) if index_path.is_file() else "# Wiki index\n\n"
    sep = _newline(existing)
    if existing and not existing.endswith("\n"):
        existing += sep
    _write(index_path, existing + link + sep)
    _drop_section(path, text, preamble, sections, index)
    return {"message": f"Created {filename}", "page": filename}


def inbox_append(config, file, heading, page, section) -> dict:
    path, text, preamble, sections, index = _inbox_section(config, file, heading)
    target = _page_path(config, page)
    page_text = _read(target)
    nl = _newline(page_text)
    lines = page_text.splitlines(keepends=True)
    wanted = str(section).strip()
    if not wanted.startswith("## "):
        wanted = f"## {wanted}"
    start = next((n for n, line in enumerate(lines) if line.strip() == wanted), None)
    if start is None:
        raise ReviewError(f"{target.name} has no section '{wanted}'")
    stop = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
    last = stop
    while last > start + 1 and not lines[last - 1].strip():
        last -= 1
    if last > 0 and not lines[last - 1].endswith("\n"):
        lines[last - 1] += nl
    body = "".join(sections[index][1]).strip()
    addition = ([nl] if last > start + 1 else []) + [
        f"{row}{nl}" for row in body.splitlines()
    ]
    lines[last:last] = addition
    _write(target, "".join(lines))
    _drop_section(path, text, preamble, sections, index)
    return {"message": f"Appended to {target.name} / {wanted}"}


# ---------------------------------------------------------------- sources

GITHUB_BLOB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/[^/]+/([^?#]+)")
MAX_SOURCE = 1024 * 1024


def _remote_slug(config) -> str:
    """``owner/repo`` of the project's origin remote, lower case; empty when unknown."""
    code, out = _run_git(config, "remote", "get-url", "origin")
    found = re.search(r"[:/]([^/:@]+)/([^/]+?)(?:\.git)?/?$", out.strip()) if code == 0 else None
    return f"{found.group(1)}/{found.group(2)}".lower() if found else ""


def read_source(configs, config, ref) -> dict:
    """The text behind a ``sources``/``covers`` entry, when it is a tracked file of an open project.

    A GitHub blob URL is mapped to the open project whose origin is that repository. Only files
    tracked by git are served, so ignored files (secrets, local data) stay out of reach.
    """
    ref = str(ref or "").strip()
    target, _, symbol = ref.partition("::")
    candidates = [config, *[c for c in configs if c is not config]]
    blob = GITHUB_BLOB.match(target)
    if blob:
        slug = f"{blob.group(1)}/{blob.group(2)}".lower()
        candidates = [c for c in candidates if _remote_slug(c) == slug]
        rel = unquote(blob.group(3))
    elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", target):
        candidates, rel = [], ""
    else:
        rel = target
    for candidate in candidates:
        try:
            path = _inside(candidate.root, rel, suffix=None)
        except ReviewError:
            continue
        if not path.is_file() or path.stat().st_size > MAX_SOURCE:
            continue
        posix = path.relative_to(candidate.root.resolve()).as_posix()
        if _run_git(candidate, "ls-files", "--error-unmatch", "--", posix)[0] != 0:
            continue
        text = _read(path)
        part = wiki.symbol_source(text, symbol) if symbol and path.suffix == ".py" else None
        return {
            "project": candidate.project,
            "path": posix,
            "symbol": symbol if part else "",
            "text": part or text,
            "markdown": path.suffix.lower() in (".md", ".markdown"),
        }
    url = target if re.match(r"^https?://", target) else ""
    raise ReviewError(f"not a tracked file of an open project: {target}", 404, url=url)


# ---------------------------------------------------------------- git


def _wiki_rel(config) -> str:
    try:
        return config.wiki_dir.resolve().relative_to(config.root.resolve()).as_posix() or "."
    except ValueError:
        raise ReviewError("the wiki directory is outside the project root") from None


def _run_git(config, *args):
    try:
        done = subprocess.run(
            ["git", *args],
            capture_output=True,
            cwd=config.root,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"git failed: {error}"
    return done.returncode, (done.stdout + done.stderr).strip()


def git_changes(config) -> list[str]:
    """Changed and untracked files under the wiki directory, as ``XY path`` entries."""
    try:
        done = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-uall", "--", _wiki_rel(config)],
            capture_output=True,
            cwd=config.root,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode != 0:
        return []
    entries = done.stdout.decode("utf-8", errors="replace").split("\0")
    changes, skip = [], False
    for entry in entries:
        if skip:
            skip = False
            continue
        if len(entry) < 4:
            continue
        changes.append(f"{entry[:2].strip() or '?'} {entry[3:]}")
        skip = entry[0] in "RC" or entry[1] in "RC"
    return changes


def commit(config, message) -> dict:
    """Commit only the wiki directory, whatever else is staged. Never pushes."""
    message = str(message or "").strip()
    if not message:
        raise ReviewError("commit message is empty")
    rel = _wiki_rel(config)
    code, out = _run_git(config, "add", "--", rel)
    if code == 0:
        code, out = _run_git(config, "commit", "-m", message, "--", rel)
    return {"ok": code == 0, "message": out or "done", "output": out, "exit": code}


# ---------------------------------------------------------------- actions


def _need(payload, *names):
    missing = [n for n in names if payload.get(n) is None]
    if missing:
        raise ReviewError(f"missing field(s): {', '.join(missing)}")
    return [payload[n] for n in names]


ACTIONS = {
    "set_trust": lambda c, p: set_trust(c, *_need(p, "page", "trust")),
    "set_status": lambda c, p: set_status(c, *_need(p, "page", "status")),
    "confirm": lambda c, p: confirm_page(c, *_need(p, "page")),
    "save_page": lambda c, p: save_page(c, *_need(p, "page", "text", "sha")),
    "inbox_discard": lambda c, p: inbox_discard(c, *_need(p, "file", "heading")),
    "inbox_to_page": lambda c, p: inbox_to_page(
        c, *_need(p, "file", "heading", "name", "title", "type", "trust")
    ),
    "inbox_append": lambda c, p: inbox_append(c, *_need(p, "file", "heading", "page", "section")),
    "commit": lambda c, p: commit(c, *_need(p, "message")),
}


def run_action(config, payload: dict) -> dict:
    handler = ACTIONS.get(payload.get("action"))
    if handler is None:
        raise ReviewError(f"unknown action: {payload.get('action')}")
    result = dict(handler(config, payload))
    result.setdefault("ok", True)
    result.setdefault("message", "done")
    return result


def project_info(configs) -> list[dict]:
    return [
        {"index": i, "name": c.project, "root": str(c.root), "wiki": c.wiki.dir}
        for i, c in enumerate(configs)
    ]


def queue_payload(config) -> dict:
    items = build_queue(config)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {
        "items": items,
        "counts": counts,
        "total": len(items),
        "git_changes": git_changes(config),
        "sections": list(config.wiki.sections),
        "pages": sorted(p.name for p in wiki._page_files(config.wiki_dir))
        if config.wiki_dir.is_dir()
        else [],
    }


# ---------------------------------------------------------------- HTTP


def _template() -> str:
    return resources.files("pbrain").joinpath("templates", "review.html").read_text("utf-8")


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, configs, token=None):
        host = address[0]
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__(address, ReviewHandler)
        self.configs = list(configs)
        self.token = token or secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        port = self.server_address[1]
        names = {*LOCAL_HOSTS, host if ":" not in host else f"[{host}]"}
        self.allowed_hosts = {f"{name}:{port}" for name in names}


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "pbrain-review"

    def log_message(self, format, *args):  # noqa: A002
        pass

    # -- plumbing
    def _send(self, status, body: bytes, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status, message, **extra):
        self._json(status, {"ok": False, "message": message, "error": message, **extra})

    def _host_ok(self):
        return (self.headers.get("Host") or "").lower() in self.server.allowed_hosts

    def _config(self, query):
        try:
            return self.server.configs[int(query.get("project", ["0"])[0])]
        except (ValueError, IndexError):
            raise ReviewError("unknown project", 404) from None

    def _guard(self, handler):
        try:
            handler()
        except ReviewError as error:
            self._error(error.status, str(error), **error.extra)
        except Exception as error:  # never answer with a traceback page
            self._error(500, f"{type(error).__name__}: {error}")

    # -- verbs
    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        self._guard(self._get)

    def _get(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = url.path
        if route == "/":
            html = _template().replace("__PBRAIN_TOKEN__", self.server.token)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if route == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if route == "/api/projects":
            return self._json(200, {"ok": True, "projects": project_info(self.server.configs)})
        config = self._config(query) if route.startswith("/api/") else None
        page = (query.get("page") or [""])[0]
        if route == "/api/queue":
            with self.server.lock:
                return self._json(200, {"ok": True, **queue_payload(config)})
        if route == "/api/page":
            return self._json(200, {"ok": True, **read_page(config, page)})
        if route == "/api/source":
            ref = (query.get("ref") or [""])[0]
            found = read_source(self.server.configs, config, ref)
            return self._json(200, {"ok": True, **found})
        if route == "/api/diff":
            return self._json(200, {"ok": True, "diff": page_diff(config, page)})
        raise ReviewError("not found", 404)

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad Host header")
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {
            f"http://{host}" for host in self.server.allowed_hosts
        }:
            return self._error(HTTPStatus.FORBIDDEN, "bad Origin header")
        if not secrets.compare_digest(
            self.headers.get("X-Pbrain-Token") or "", self.server.token
        ):
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong X-Pbrain-Token")
        self._guard(self._post)

    def _post(self):
        if urlparse(self.path).path != "/api/action":
            raise ReviewError("not found", 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ReviewError("bad Content-Length") from None
        if length > MAX_BODY:
            raise ReviewError("request body too large (limit 2 MB)", 413)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ReviewError("body is not valid JSON") from None
        if not isinstance(payload, dict):
            raise ReviewError("body must be a JSON object")
        try:
            config = self.server.configs[int(payload.get("project", 0))]
        except (ValueError, TypeError, IndexError):
            raise ReviewError("unknown project", 404) from None
        with self.server.lock:
            result = run_action(config, payload)
        self._json(200 if result.get("ok") else 422, result)


# ---------------------------------------------------------------- command


def load_projects(config, extra_roots):
    """The current project plus each extra root; de-duplicated by resolved path."""
    configs, seen = [], set()
    for candidate in [config, *extra_roots]:
        if not isinstance(candidate, config_module.Config):
            root = Path(candidate).resolve()
            if not (root / config_module.CONFIG_NAME).is_file():
                raise ValueError(f"{root} has no {config_module.CONFIG_NAME}")
            candidate = config_module.load(root)
        key = candidate.root.resolve()
        if key not in seen:
            seen.add(key)
            configs.append(candidate)
    return configs


def _command(args, config) -> int:
    import sys

    try:
        configs = load_projects(config, args.roots)
    except (OSError, ValueError) as error:
        print(f"pbrain review: {error}", file=sys.stderr)
        return 2
    try:
        server = ReviewServer((args.host, args.port), configs)
    except OSError as error:
        print(f"pbrain review: cannot listen on {args.host}:{args.port}: {error}", file=sys.stderr)
        return 2
    host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"pbrain review: {url}  ({len(configs)} project(s)). Ctrl+C to stop.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "review", help="Local web page to clear pending wiki decisions with one click."
    )
    parser.add_argument("roots", nargs="*", help="Extra project roots (each has a pbrain.toml).")
    parser.add_argument("--port", type=int, default=8765, help="0 picks a free port.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser.")
    parser.set_defaults(func=_command)

