import json

from pbrain import init
from pbrain.config import load


def tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".git" not in p.parts)


def test_no_flags_only_reports(tmp_path, run_cli):
    code, out, _ = run_cli("--root", tmp_path, "init")
    assert code == 0
    assert "missing" in out
    assert tree(tmp_path) == []


def test_all_creates_everything(tmp_path, run_cli, helpers):
    helpers.init_repo(tmp_path)
    code, _, _ = run_cli("--root", tmp_path, "init", "--all")
    assert code == 0
    for name in (
        "pbrain.toml",
        "docs/wiki/SCHEMA.md",
        "docs/wiki/index.md",
        "docs/wiki/log.md",
        ".claude/hooks/pbrain_run.py",
        ".claude/settings.json",
        ".git/hooks/pre-commit",
    ):
        assert (tmp_path / name).is_file(), name
    settings = json.loads((tmp_path / ".claude/settings.json").read_text(encoding="utf-8"))
    assert set(settings["hooks"]) == {"PreToolUse", "SessionStart", "Stop"}
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit"


def test_all_is_idempotent(tmp_path, run_cli, helpers):
    helpers.init_repo(tmp_path)
    run_cli("--root", tmp_path, "init", "--all")
    files = [
        "pbrain.toml",
        ".claude/settings.json",
        ".claude/hooks/pbrain_run.py",
        ".git/hooks/pre-commit",
        "docs/wiki/SCHEMA.md",
    ]
    first = {f: (tmp_path / f).read_bytes() for f in files}
    names = tree(tmp_path)
    run_cli("--root", tmp_path, "init", "--all")
    assert {f: (tmp_path / f).read_bytes() for f in files} == first
    assert tree(tmp_path) == names
    settings = json.loads((tmp_path / ".claude/settings.json").read_text(encoding="utf-8"))
    assert all(len(groups) == 1 for groups in settings["hooks"].values())


def test_existing_settings_are_preserved(tmp_path, run_cli):
    settings = {
        "model": "x",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
    }
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(settings), encoding="utf-8")
    run_cli("--root", tmp_path, "init", "--hooks")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["model"] == "x"
    commands = [h["command"] for g in result["hooks"]["Stop"] for h in g["hooks"]]
    assert commands[0] == "echo hi"
    assert len(commands) == 2
    assert "PreToolUse" in result["hooks"] and "SessionStart" in result["hooks"]


def test_invalid_settings_json_is_not_overwritten(tmp_path, run_cli):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    code, out, _ = run_cli("--root", tmp_path, "init", "--hooks")
    assert code == 0
    assert path.read_text(encoding="utf-8") == "{ not json"
    assert "skipped" in out


def test_pre_commit_created_when_missing(tmp_path, helpers):
    helpers.init_repo(tmp_path)
    init.install_pre_commit(load(tmp_path))
    text = (tmp_path / ".git/hooks/pre-commit").read_text(encoding="utf-8")
    assert text.startswith("#!")
    assert init.HOOK_START in text and "check --staged" in text


def test_pre_commit_without_git_is_skipped(tmp_path):
    (tmp_path / "pbrain.toml").write_text("", encoding="utf-8")
    assert init.install_pre_commit(load(tmp_path)).startswith("skipped")


def test_pre_commit_block_inserted_after_shebang(tmp_path, helpers):
    helpers.init_repo(tmp_path)
    path = tmp_path / ".git/hooks/pre-commit"
    path.write_text("#!/bin/sh\necho first\necho second\n", encoding="utf-8")
    cfg = load(tmp_path)
    init.install_pre_commit(cfg)
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "#!/bin/sh"
    assert lines[1] == init.HOOK_START
    assert lines[lines.index(init.HOOK_END) + 1 :][:2] == ["echo first", "echo second"]
    once = path.read_text(encoding="utf-8")
    init.install_pre_commit(cfg)
    assert path.read_text(encoding="utf-8") == once
    assert once.count(init.HOOK_START) == 1


def test_rules_file_created(tmp_path, run_cli):
    run_cli("--root", tmp_path, "init", "--rules", "CLAUDE.md")
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith(init.MARK_START)
    assert text.rstrip().endswith(init.MARK_END)
    assert "docs/wiki/index.md" in text


def test_rules_appended_to_existing_content(tmp_path, run_cli):
    (tmp_path / "CLAUDE.md").write_text("# Mine\n\nKeep this.\n", encoding="utf-8")
    run_cli("--root", tmp_path, "init", "--rules", "CLAUDE.md")
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nKeep this.\n")
    assert text.index(init.MARK_START) > text.index("Keep this.")


def test_rules_rerun_replaces_only_marked_block(tmp_path, run_cli):
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        f"before\n{init.MARK_START}\nOLD RULES\n{init.MARK_END}\nafter\n", encoding="utf-8"
    )
    run_cli("--root", tmp_path, "init", "--rules", "CLAUDE.md")
    result = path.read_text(encoding="utf-8")
    assert result.startswith("before\n") and result.endswith("\nafter\n")
    assert "OLD RULES" not in result
    assert result.count(init.MARK_START) == 1
    run_cli("--root", tmp_path, "init", "--rules", "CLAUDE.md")
    assert path.read_text(encoding="utf-8") == result


def test_fresh_wiki_passes_lint(tmp_path, run_cli):
    run_cli("--root", tmp_path, "init", "--wiki")
    code, out, _ = run_cli("--root", tmp_path, "wiki", "lint")
    assert code == 0
    assert "0 errors" in out
    assert "ERROR" not in out


def test_custom_sections_flow_into_schema(tmp_path):
    (tmp_path / "pbrain.toml").write_text('[wiki]\nsections = ["## Why", "## Now"]\n')
    init.install_wiki(load(tmp_path))
    schema = (tmp_path / "docs/wiki/SCHEMA.md").read_text(encoding="utf-8")
    assert "`## Why`" in schema and "`## Now`" in schema
