import pytest

from pbrain import ecosystem
from pbrain.config import load

SHARED_CATALOG = r"""
REUSE_CATALOG = (
    {"name": "Banner", "kind": "js", "path": "src/banner.js", "symbol": "Banner",
     "use_when": "Show a notice banner.", "keywords": "notice banner"},
)
"""


def make_shared(root, helpers):
    """A pbrain project to be used as an ecosystem repo."""
    helpers.write(root, "pbrain.toml", '[reuse]\ncatalog = "catalog.py"\nroots = ["src"]\n')
    helpers.write(root, "catalog.py", SHARED_CATALOG)
    helpers.write(root, "src/banner.js", "const Banner = {};\n")
    helpers.write(root, "docs/wiki/index.md", "# Shared index\n")
    return root


def make_consumer(root, helpers, extends):
    entries = ", ".join(f'"{e}"' for e in extends)
    helpers.write(root, "pbrain.toml", f"extends = [{entries}]\n")
    return load(root)


@pytest.fixture
def shared(tmp_path, helpers):
    return make_shared(tmp_path / "shared", helpers)


def test_local_path_source(tmp_path, helpers, shared):
    consumer = tmp_path / "app"
    cfg = make_consumer(consumer, helpers, [shared.as_posix()])
    found = ecosystem.sources(cfg)
    assert [s["name"] for s in found] == ["shared"]
    assert found[0]["path"] == shared
    assert ecosystem.describe(cfg) == [f"shared: {shared / 'docs/wiki/index.md'}"]
    entries = ecosystem.catalog_entries(cfg)
    assert [(e["name"], e["source"]) for e in entries] == [("Banner", "shared")]


def test_relative_local_path_resolves_against_project_root(tmp_path, helpers, shared):
    cfg = make_consumer(tmp_path / "app", helpers, ["../shared"])
    assert [s["name"] for s in ecosystem.sources(cfg)] == ["shared"]


def test_missing_path_gives_message_not_error(tmp_path, helpers, run_cli):
    cfg = make_consumer(tmp_path / "app", helpers, ["../nowhere"])
    assert ecosystem.sources(cfg) == []
    assert ecosystem.describe(cfg) == []
    assert ecosystem.catalog_entries(cfg) == []
    assert any("not found" in m for m in ecosystem.sync(cfg))
    code, out, _ = run_cli("--root", cfg.root, "sync")
    assert code == 0 and "not found" in out


def test_describe_falls_back_to_path_without_index(tmp_path, helpers, shared):
    (shared / "docs/wiki/index.md").unlink()
    cfg = make_consumer(tmp_path / "app", helpers, [shared.as_posix()])
    assert ecosystem.describe(cfg) == [f"shared: {shared}"]


def test_broken_shared_catalog_is_skipped(tmp_path, helpers, shared):
    (shared / "catalog.py").write_text("raise RuntimeError('x')\n", encoding="utf-8")
    cfg = make_consumer(tmp_path / "app", helpers, [shared.as_posix()])
    assert ecosystem.catalog_entries(cfg) == []


def test_reuse_lists_ecosystem_entries_after_project_entries(tmp_path, helpers, shared, run_cli):
    app = tmp_path / "app"
    helpers.write(
        app,
        "pbrain.toml",
        f'extends = ["{shared.as_posix()}"]\n[reuse]\ncatalog = "catalog.py"\nroots = ["src"]\n',
    )
    helpers.write(
        app,
        "catalog.py",
        'REUSE_CATALOG = ({"name": "Notify", "kind": "js", "path": "src/n.js",'
        ' "use_when": "Show a notice.", "keywords": "notice"},)\n',
    )
    helpers.write(app, "src/n.js", "const Notify = {};\n")
    code, out, _ = run_cli("--root", app, "reuse", "notice")
    assert code == 0
    assert out.index("Notify") < out.index("Banner")
    assert "(from shared)" in out


@pytest.fixture
def remote(tmp_path, helpers, monkeypatch):
    """A local git repo used as a git ``extends`` URL, with the cache inside ``tmp_path``."""
    monkeypatch.setenv("PBRAIN_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ecosystem, "_is_url", lambda entry: entry.startswith("file://"))
    root = make_shared(tmp_path / "remote", helpers)
    helpers.init_repo(root)
    helpers.commit_all(root)
    return root, "file:///" + root.as_posix().lstrip("/")


def test_git_url_is_cloned_then_not_pulled_while_fresh(tmp_path, helpers, remote):
    root, url = remote
    cfg = make_consumer(tmp_path / "app", helpers, [url])
    assert any("cloned" in m for m in ecosystem.sync(cfg))
    cached = ecosystem.location(cfg, url)
    assert (cached / "pbrain.toml").is_file()
    assert [s["name"] for s in ecosystem.sources(cfg)] == ["remote"]
    assert [e["source"] for e in ecosystem.catalog_entries(cfg)] == ["remote"]

    helpers.write(root, "docs/wiki/new.md", "# new\n")
    helpers.commit_all(root)
    assert ecosystem.sync(cfg) == []
    assert not (cached / "docs/wiki/new.md").exists()

    assert any("updated" in m for m in ecosystem.sync(cfg, force=True))
    assert (cached / "docs/wiki/new.md").is_file()


def test_stale_cache_is_updated(tmp_path, helpers, remote):
    root, url = remote
    cfg = make_consumer(tmp_path / "app", helpers, [url])
    ecosystem.sync(cfg)
    helpers.write(root, "docs/wiki/new.md", "# new\n")
    helpers.commit_all(root)
    assert any("updated" in m for m in ecosystem.sync(cfg, max_age_hours=0))
    assert (ecosystem.location(cfg, url) / "docs/wiki/new.md").is_file()


def test_failed_clone_reports_and_continues(tmp_path, helpers, remote):
    cfg = make_consumer(tmp_path / "app", helpers, ["file:///nonexistent/repo-x"])
    messages = ecosystem.sync(cfg)
    assert any("could not clone" in m for m in messages)
    assert ecosystem.sources(cfg) == []


def test_url_detection_and_naming():
    assert ecosystem._is_url("https://example.com/a/b.git")
    assert ecosystem._is_url("git@example.com:a/b.git")
    assert not ecosystem._is_url("../local")
    assert ecosystem._name("https://example.com/a/knowledge.git") == "knowledge"
    assert ecosystem._name("git@example.com:a/b.git") == "b"
