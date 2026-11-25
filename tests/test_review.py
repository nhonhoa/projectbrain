import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import date

import pytest

from pbrain import review, wiki
from pbrain.config import load

INBOX_TEXT = """Preamble line, not an item.

## First idea
Body one.

## Second idea
Body two.
"""


def page_text(trust="agreed", status="active", nl="\n", title="Totals"):
    lines = [
        "---",
        f"title: {title}",
        "type: concept",
        f"status: {status}",
        f"trust: {trust}",
        f"verified: {date.today().isoformat()}",
        "---",
        "## Intent",
        "x",
        "## Reality",
        "y",
        "## Gaps",
        "z",
        "## Related",
        "w",
    ]
    return nl.join(lines) + nl


def raw(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


@pytest.fixture
def cfg(tmp_path):
    raw(tmp_path / "pbrain.toml", 'project = "demo"\n')
    config = load(tmp_path)
    raw(config.wiki_dir / "index.md", "- [Totals](totals.md) - totals\n")
    raw(config.wiki_dir / "log.md", "# log\n")
    raw(config.wiki_dir / "totals.md", page_text())
    return config


def test_queue_kinds_and_order(cfg):
    wd = cfg.wiki_dir
    raw(wd / "req.md", page_text("requested", title="Req"))
    raw(wd / "inf.md", page_text("inferred", title="Inf"))
    raw(wd / "old.md", page_text("inferred", status="deprecated", title="Old"))
    raw(wd / "_inbox" / "notes.md", INBOX_TEXT)
    raw(wd / "broken.md", page_text().replace("## Intent", "[x](nope.md)\n## Intent"))
    items = review.build_queue(cfg)
    kinds = [(i["kind"], i.get("severity") or i.get("trust")) for i in items]
    assert kinds[0] == ("lint", "error")  # broken link and orphans first
    rest = [k for k in kinds if k[0] != "lint"]
    assert rest == [
        ("inbox", None),
        ("inbox", None),
        ("trust", "requested"),
        ("trust", "inferred"),
    ]
    assert not [i for i in items if i["page"] == "old.md" and i["kind"] == "trust"]
    assert all(i["id"] for i in items)
    assert len({i["id"] for i in items}) == len(items)
    # Several findings on one page at one severity are a single item.
    lint = [(i["page"], i["severity"]) for i in items if i["kind"] == "lint"]
    assert len(lint) == len(set(lint))
    assert all(i["messages"] for i in items if i["kind"] == "lint")


def test_drift_item_carries_covers(tmp_path, helpers):
    helpers.init_repo(tmp_path)
    helpers.write(tmp_path, "src/app.py", "def total(x):\n    return x + 1\n")
    helpers.write(tmp_path, "pbrain.toml", 'project = "demo"\n')
    config = load(tmp_path)
    text = page_text().replace("verified:", "covers:\n  - src/app.py::total\nverified:")
    raw(config.wiki_dir / "totals.md", text.replace(date.today().isoformat(), "2020-01-01"))
    raw(config.wiki_dir / "index.md", "- [Totals](totals.md) - t\n")
    raw(config.wiki_dir / "log.md", "# log\n")
    helpers.commit_all(tmp_path, "2019-12-31T12:00:00")
    helpers.write(tmp_path, "src/app.py", "def total(x):\n    return x + 2\n")
    items = [i for i in review.build_queue(config) if i["kind"] == "drift"]
    assert len(items) == 1
    assert items[0]["covers"] == ["src/app.py::total"]
    diff = review.page_diff(config, "totals.md")
    assert "x + 2" in diff


def test_inbox_not_linted_as_pages(cfg):
    raw(cfg.wiki_dir / "_inbox" / "notes.md", "no front matter here\n## A\nb\n")
    findings, count = wiki.lint_wiki(cfg)
    assert count == 1
    assert not any("_inbox" in name or name == "notes.md" for _, name, _ in findings)


def test_inbox_splitting_and_discard(cfg):
    path = cfg.wiki_dir / "_inbox" / "notes.md"
    raw(path, INBOX_TEXT)
    items = [i for i in review.build_queue(cfg) if i["kind"] == "inbox"]
    assert [i["heading"] for i in items] == ["First idea", "Second idea"]
    assert items[0]["body"] == "Body one."
    review.inbox_discard(cfg, "notes.md", "First idea")
    assert path.read_text(encoding="utf-8") == (
        "Preamble line, not an item.\n\n## Second idea\nBody two.\n"
    )
    review.inbox_discard(cfg, "notes.md", "Second idea")
    assert not path.exists()


def test_set_trust_and_status_keep_bytes_and_crlf(cfg):
    path = cfg.wiki_dir / "totals.md"
    original = page_text("inferred", nl="\r\n")
    raw(path, original)
    review.set_trust(cfg, "totals.md", "agreed")
    assert path.read_bytes().decode() == original.replace("trust: inferred", "trust: agreed")
    review.set_status(cfg, "totals.md", "deprecated")
    assert b"status: deprecated\r\n" in path.read_bytes()
    assert b"\r\n" in path.read_bytes() and b"\n" not in path.read_bytes().replace(b"\r\n", b"")
    with pytest.raises(review.ReviewError):
        review.set_trust(cfg, "totals.md", "bogus")


def test_set_trust_adds_missing_line_in_lf_file(cfg):
    path = cfg.wiki_dir / "totals.md"
    raw(path, page_text().replace("trust: agreed\n", ""))
    review.set_trust(cfg, "totals.md", "requested")
    text = path.read_text(encoding="utf-8")
    assert text.count("trust: requested\n") == 1
    assert wiki.parse_front_matter(text)["trust"] == "requested"


def test_save_conflict_and_crlf(cfg):
    path = cfg.wiki_dir / "totals.md"
    raw(path, page_text(nl="\r\n"))
    loaded = review.read_page(cfg, "totals.md")
    result = review.save_page(cfg, "totals.md", loaded["text"].replace("x", "xx"), loaded["sha"])
    assert result["sha"] != loaded["sha"]
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"")
    with pytest.raises(review.ReviewError) as caught:
        review.save_page(cfg, "totals.md", "other", loaded["sha"])  # stale sha
    assert caught.value.status == 409 and caught.value.extra["conflict"]


BAD_PATHS = ["../x.md", "..\\x.md", "/etc/passwd.md", "C:/x.md", "sub/../../x.md"]


@pytest.mark.parametrize("bad", BAD_PATHS)
def test_path_traversal_rejected(cfg, tmp_path, bad):
    raw(tmp_path / "x.md", page_text())
    for call in (
        lambda: review.read_page(cfg, bad),
        lambda: review.save_page(cfg, bad, "t", "s"),
        lambda: review.set_trust(cfg, bad, "agreed"),
    ):
        with pytest.raises(review.ReviewError):
            call()
    raw(cfg.wiki_dir / "_inbox" / "n.md", INBOX_TEXT)
    with pytest.raises(review.ReviewError):
        review.inbox_discard(cfg, bad, "First idea")


def test_inbox_to_page(cfg):
    raw(cfg.wiki_dir / "_inbox" / "n.md", INBOX_TEXT)
    result = review.inbox_to_page(
        cfg, "n.md", "First idea", "Ý Tưởng Mới!", "New idea", "decision", "requested"
    )
    assert result["page"] == "y-tuong-moi.md"
    text = (cfg.wiki_dir / "y-tuong-moi.md").read_text(encoding="utf-8")
    meta = wiki.parse_front_matter(text)
    assert meta["trust"] == "requested" and meta["status"] == "active"
    assert meta["verified"] == date.today().isoformat()
    assert "## Intent\nBody one.\n## Reality\n" in text
    index = (cfg.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert "[New idea](y-tuong-moi.md)" in index and index.startswith("- [Totals]")
    headings = [i["heading"] for i in review.build_queue(cfg) if i["kind"] == "inbox"]
    assert headings == ["Second idea"]
    findings, _ = wiki.lint_wiki(cfg)
    assert not [f for f in findings if f[1] == "y-tuong-moi.md" and f[0] == "ERROR"]
    with pytest.raises(review.ReviewError):  # never overwrites
        review.inbox_to_page(cfg, "n.md", "Second idea", "totals", "T", "concept", "inferred")
    assert "Body two." in (cfg.wiki_dir / "_inbox" / "n.md").read_text(encoding="utf-8")


def test_inbox_append(cfg):
    raw(cfg.wiki_dir / "_inbox" / "n.md", INBOX_TEXT)
    review.inbox_append(cfg, "n.md", "First idea", "totals.md", "## Reality")
    text = (cfg.wiki_dir / "totals.md").read_text(encoding="utf-8")
    assert "## Reality\ny\n\nBody one.\n## Gaps\n" in text
    assert "First idea" not in (cfg.wiki_dir / "_inbox" / "n.md").read_text(encoding="utf-8")
    with pytest.raises(review.ReviewError):
        review.inbox_append(cfg, "n.md", "Second idea", "totals.md", "## Nope")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_commit_only_wiki_files(tmp_path, helpers):
    helpers.init_repo(tmp_path)
    raw(tmp_path / "pbrain.toml", 'project = "demo"\n')
    config = load(tmp_path)
    raw(config.wiki_dir / "index.md", "- [T](totals.md) - t\n")
    raw(config.wiki_dir / "totals.md", page_text("inferred"))
    raw(tmp_path / "other.txt", "one\n")
    helpers.commit_all(tmp_path)
    review.set_trust(config, "totals.md", "agreed")
    raw(tmp_path / "other.txt", "two\n")
    helpers.git(tmp_path, "add", "other.txt")  # staged by someone else
    assert any("totals.md" in c for c in review.git_changes(config))
    result = review.commit(config, "docs: review")
    assert result["ok"], result
    names = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=tmp_path, capture_output=True, encoding="utf-8",
    ).stdout.split()
    assert names == ["docs/wiki/totals.md"]
    assert review.git_changes(config) == []
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
    ).stdout.split()
    assert staged == ["other.txt"]


def test_load_projects_dedup_and_bad_root(cfg, tmp_path):
    assert len(review.load_projects(cfg, [str(cfg.root), str(cfg.root)])) == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        review.load_projects(cfg, [str(empty)])


def test_cli_bad_root_exits_2(cfg, run_cli, tmp_path):
    empty = tmp_path / "nowhere"
    empty.mkdir()
    code, _, err = run_cli("--root", cfg.root, "review", empty, "--no-open")
    assert code == 2 and "pbrain.toml" in err


def test_http_smoke(cfg):
    raw(cfg.wiki_dir / "req.md", page_text("requested", title="Req"))
    server = review.ReviewServer(("127.0.0.1", 0), [cfg])
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    def call(path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    try:
        status, html = call("/")
        assert status == 200 and "pbrain review" in html and server.token in html
        status, body = call("/api/queue?project=0")
        queue = json.loads(body)
        assert status == 200 and queue["counts"]["trust"] == 1
        post = {"project": 0, "action": "set_trust", "page": "req.md", "trust": "agreed"}
        status, _ = call("/api/action", post)
        assert status == 403
        good = {"X-Pbrain-Token": server.token}
        status, body = call("/api/action", post, {**good, "Origin": "http://evil.example"})
        assert status == 403
        status, body = call("/api/action", post, good)
        assert status == 200 and json.loads(body)["ok"]
        assert "trust: agreed" in (cfg.wiki_dir / "req.md").read_text(encoding="utf-8")
        status, body = call("/api/action", {**post, "page": "../x.md"}, good)
        assert status == 400 and not json.loads(body)["ok"]
        status, _ = call("/api/page?project=0&page=../x.md")
        assert status == 400
        status, _ = call("/", headers={"Host": "evil.example"})
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()
