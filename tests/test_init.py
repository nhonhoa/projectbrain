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


# --- init --extends / interactive prompt -------------------------------------------------


SHARED_TOML = (
    'project = "shared"\n[wiki]\ndir = "wiki"\n'
    'sections = ["## Ý định", "## Thực tế"]\nmax_lines = 60\n[cases]\ndir = "wiki/cases"\n'
)


def make_shared(tmp_path, helpers, schema=True):
    shared = tmp_path / "shared"
    helpers.write(shared, "pbrain.toml", SHARED_TOML)
    if schema:
        helpers.write(shared, "wiki/SCHEMA.md", "# Shared schema\n")
    helpers.write(shared, "review_projects.txt", "# roots\n")
    return shared


def test_extends_creates_config_with_shared_defaults(tmp_path, run_cli, helpers):
    shared = make_shared(tmp_path, helpers)
    app = tmp_path / "app"
    app.mkdir()
    code, out, _ = run_cli("--root", app, "init", "--extends", shared, "--no-extends")
    assert code == 0
    config = load(app)
    assert config.extends == (str(shared),)
    assert config.wiki.sections == ("## Ý định", "## Thực tế")
    assert config.wiki.max_lines == 60
    assert config.wiki.dir == "docs/wiki"
    assert config.cases.dir == "docs/wiki/cases"
    assert "review_projects.txt" in out and str(app) in out
    assert not (app / "docs").exists()  # only the config step ran


def test_extends_with_all_copies_shared_schema(tmp_path, run_cli, helpers):
    shared = make_shared(tmp_path, helpers)
    app = tmp_path / "app"
    app.mkdir()
    helpers.init_repo(app)
    run_cli("--root", app, "init", "--all", "--extends", shared)
    assert (app / "docs/wiki/SCHEMA.md").read_text(encoding="utf-8") == "# Shared schema\n"


def test_extends_never_overwrites_existing_config(tmp_path, run_cli, helpers):
    shared = make_shared(tmp_path, helpers)
    app = tmp_path / "app"
    original = 'project = "mine"\n\n[wiki]\ndir = "notes"\nsections = ["## A"]\n'
    helpers.write(app, "pbrain.toml", original)
    run_cli("--root", app, "init", "--extends", shared)
    config = load(app)
    assert config.extends == (str(shared),)
    assert config.wiki.sections == ("## A",)  # kept
    assert config.wiki.dir == "notes"
    assert config.wiki.max_lines == 60  # missing key filled in
    assert config.cases.dir == "notes/cases"
    text = (app / "pbrain.toml").read_text(encoding="utf-8")
    assert 'project = "mine"' in text and text.count("sections") == 1


def test_extends_keeps_existing_extends_and_is_idempotent(tmp_path, run_cli, helpers):
    shared = make_shared(tmp_path, helpers)
    app = tmp_path / "app"
    helpers.write(app, "pbrain.toml", 'extends = ["elsewhere"]\n')
    _, out, _ = run_cli("--root", app, "init", "--extends", shared)
    assert load(app).extends == ("elsewhere",)
    assert "by hand" in out
    run_cli("--root", app, "init", "--extends", shared)
    first = (app / "pbrain.toml").read_bytes()
    run_cli("--root", app, "init", "--extends", shared)
    assert (app / "pbrain.toml").read_bytes() == first


def test_extends_unavailable_repo_still_writes_extends(tmp_path, run_cli, helpers):
    app = tmp_path / "app"
    app.mkdir()
    code, out, _ = run_cli("--root", app, "init", "--extends", tmp_path / "nope")
    assert code == 0
    assert len(load(app).extends) == 1
    assert "not available" in out


def test_no_tty_never_prompts(tmp_path, run_cli, monkeypatch):
    def boom(*_a):
        raise AssertionError("must not prompt")

    monkeypatch.setattr("builtins.input", boom)
    app = tmp_path / "app"
    app.mkdir()
    code, _, _ = run_cli("--root", app, "init", "--config")  # pytest: stdin is not a TTY
    assert code == 0 and load(app).extends == ()


def test_tty_prompt_links_repo(tmp_path, run_cli, helpers, monkeypatch):
    shared = make_shared(tmp_path, helpers)
    monkeypatch.setattr(init, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a: str(shared))
    app = tmp_path / "app"
    app.mkdir()
    run_cli("--root", app, "init", "--config")
    assert load(app).extends == (str(shared),)


def test_tty_prompt_skippable_and_disabled_by_flag(tmp_path, run_cli, monkeypatch):
    monkeypatch.setattr(init, "_interactive", lambda: True)
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr("builtins.input", lambda *_a: "")
    run_cli("--root", app, "init", "--config")
    assert load(app).extends == ()
    monkeypatch.setattr("builtins.input", lambda *_a: (_ for _ in ()).throw(AssertionError))
    run_cli("--root", app, "init", "--all", "--no-extends")
    assert load(app).extends == ()


def test_ci_env_is_not_interactive(monkeypatch):
    monkeypatch.setenv("CI", "1")
    assert init._interactive() is False


def _make_python_project(root, helpers):
    helpers.init_repo(root)
    (root / "myapp").mkdir()
    (root / "myapp" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")


def test_detect_finds_roots_rules_and_git(tmp_path, helpers):
    _make_python_project(tmp_path, helpers)
    facts = init.detect(tmp_path)
    assert facts["roots"] == ["myapp"]
    assert facts["rules"] == "CLAUDE.md"
    assert facts["git"] is True


def test_yes_takes_every_default_without_asking(tmp_path, run_cli, helpers, monkeypatch):
    _make_python_project(tmp_path, helpers)
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("asked")))
    code, _, _ = run_cli("--root", tmp_path, "init", "--yes")
    assert code == 0
    for name in (
        "pbrain.toml",
        "docs/wiki/index.md",
        ".claude/settings.json",
        ".git/hooks/pre-commit",
    ):
        assert (tmp_path / name).is_file(), name
    assert 'roots = ["myapp"]' in (tmp_path / "pbrain.toml").read_text(encoding="utf-8")
    assert init.MARK_START in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_wizard_enter_accepts_and_n_declines(tmp_path, run_cli, helpers, monkeypatch):
    _make_python_project(tmp_path, helpers)
    monkeypatch.setattr(init, "_interactive", lambda: True)
    # config=Enter, roots=Enter, wiki=Enter, hooks=n, pre-commit=Enter, rules=n, extends=Enter
    answers = iter(["", "", "", "n", "", "n", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    code, _, _ = run_cli("--root", tmp_path, "init")
    assert code == 0
    assert (tmp_path / "pbrain.toml").is_file()
    assert (tmp_path / "docs/wiki/SCHEMA.md").is_file()
    assert (tmp_path / ".git/hooks/pre-commit").is_file()
    assert not (tmp_path / ".claude").exists()
    assert init.MARK_START not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_wizard_custom_roots(tmp_path, run_cli, helpers, monkeypatch):
    _make_python_project(tmp_path, helpers)
    monkeypatch.setattr(init, "_interactive", lambda: True)
    answers = iter(["", "app, lib", "n", "n", "n", "n", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    run_cli("--root", tmp_path, "init")
    assert 'roots = ["app", "lib"]' in (tmp_path / "pbrain.toml").read_text(encoding="utf-8")
