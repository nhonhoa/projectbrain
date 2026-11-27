"""The machine-wide project registry (~/.pbrain/projects.json) and `pbrain projects`."""

import json

import pytest

from pbrain import registry


def make_project(root, name="demo"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pbrain.toml").write_text(f'project = "{name}"\n', encoding="utf-8")
    return root


def test_home_follows_pbrain_home(pbrain_home):
    assert registry.home() == pbrain_home
    assert registry.path() == pbrain_home / "projects.json"


def test_add_is_idempotent_and_keeps_added(tmp_path):
    root = make_project(tmp_path / "a", "alpha")
    entry, added = registry.add(root)
    assert added and entry["name"] == "alpha" and entry["root"] == str(root.resolve())
    again, added = registry.add(root / ".")
    assert not added and again["added"] == entry["added"]
    data = json.loads(registry.path().read_text(encoding="utf-8"))
    assert data["version"] == 1 and len(data["projects"]) == 1


def test_add_refuses_root_without_config(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(ValueError, match="pbrain.toml"):
        registry.add(tmp_path / "plain")
    assert registry.load() == []


def test_remove_prune_and_roots(tmp_path):
    a = make_project(tmp_path / "a", "a")
    b = make_project(tmp_path / "b", "b")
    registry.add(a)
    registry.add(b)
    (b / "pbrain.toml").unlink()
    assert registry.roots() == [a.resolve()]
    assert len(registry.roots(include_missing=True)) == 2
    gone = registry.prune()
    assert [g["name"] for g in gone] == ["b"]
    assert registry.remove(a) and not registry.remove(a)
    assert registry.load() == []


def test_load_tolerates_garbage(pbrain_home):
    registry.path().write_text("{not json", encoding="utf-8")
    assert registry.load() == []
    registry.path().write_text(
        json.dumps({"projects": [{"root": "X"}, {"root": "X"}, 3, {}]}),
        encoding="utf-8",
    )
    assert registry.load() == [{"root": "X"}]


def test_stale_lock_is_broken(tmp_path, pbrain_home):
    import os
    import time

    lock = registry.path().with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock, (old, old))
    registry.add(make_project(tmp_path / "a"))
    assert not lock.exists() and len(registry.load()) == 1


def test_remember_never_raises_and_skips_unconfigured(tmp_path):
    from pbrain.config import load

    plain = tmp_path / "plain"
    plain.mkdir()
    assert registry.remember(load(plain)) is False
    root = make_project(tmp_path / "p", "proj")
    assert registry.remember(load(root)) is True
    assert registry.remember(load(root)) is False  # already seen today: no write


def test_projects_command(tmp_path, run_cli):
    root = make_project(tmp_path / "p", "proj")
    code, out, _ = run_cli("projects", "add", root)
    assert code == 0 and "added: proj" in out
    code, out, _ = run_cli("projects", "list")
    assert "proj" in out and "missing" not in out
    (root / "pbrain.toml").unlink()
    code, out, _ = run_cli("projects", "list", "--json")
    assert json.loads(out)["projects"][0]["status"] == "missing"
    code, out, _ = run_cli("projects", "prune")
    assert "1 project(s) pruned" in out
    code, _, err = run_cli("projects", "add", tmp_path / "nowhere")
    assert code == 2 and "pbrain.toml" in err


def test_projects_add_defaults_to_current_project(project, run_cli):
    code, out, _ = run_cli("--root", project, "projects", "add")
    assert code == 0 and "added: demo" in out
    code, out, _ = run_cli("--root", project, "projects", "remove")
    assert "removed" in out and registry.load() == []


def test_init_registers_project(tmp_path, helpers, run_cli):
    helpers.init_repo(tmp_path)
    code, out, _ = run_cli("--root", tmp_path, "init", "--config")
    assert code == 0 and "registry: added" in out
    assert "autostart off" in out  # PBRAIN_AUTOSERVE=0 in tests
    assert registry.find(tmp_path)


def test_session_start_hook_registers(project, run_cli, stdin):
    stdin("{}")
    code, _, _ = run_cli("--root", project, "hook", "session-start")
    assert code == 0 and registry.find(project)["name"] == "demo"


def test_scan_all_uses_registry(project, tmp_path, run_cli):
    registry.add(project)
    ghost = make_project(tmp_path / "ghost", "ghost")
    registry.add(ghost)
    (ghost / "pbrain.toml").unlink()
    state = tmp_path / "state.json"
    code, out, err = run_cli("scan", "--all", "--no-sync", "--state", state)
    assert "demo" in out and "skipped" in err and "ghost" in err
    data = json.loads(state.read_text(encoding="utf-8"))
    assert list(data["projects"]) == [str(project.resolve())]
