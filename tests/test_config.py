from pbrain import config


def test_defaults_without_config_file(tmp_path):
    cfg = config.load(tmp_path)
    assert cfg.root == tmp_path.resolve()
    assert cfg.project == tmp_path.name
    assert cfg.extends == ()
    assert cfg.wiki.dir == "docs/wiki"
    assert cfg.wiki.sections[0] == "## Intent"
    assert cfg.reuse.roots == ()
    assert cfg.check.ruff is True
    assert cfg.wiki_dir == tmp_path.resolve() / "docs/wiki"
    assert cfg.cases_dir == tmp_path.resolve() / "docs/wiki/cases"


def test_reads_values(tmp_path):
    (tmp_path / "pbrain.toml").write_text(
        'project = "demo"\n'
        'extends = ["../a", "https://example.com/k.git"]\n'
        '[wiki]\nsections = ["## One", "## Two"]\nmax_lines = 10\n'
        '[reuse]\nroots = ["src", "lib"]\n'
        "[check]\nruff = false\n",
        encoding="utf-8",
    )
    cfg = config.load(tmp_path)
    assert cfg.project == "demo"
    assert cfg.extends == ("../a", "https://example.com/k.git")
    assert cfg.wiki.sections == ("## One", "## Two")
    assert cfg.wiki.max_lines == 10
    assert cfg.reuse.roots == ("src", "lib")
    assert cfg.check.ruff is False


def test_extends_as_string(tmp_path):
    (tmp_path / "pbrain.toml").write_text('extends = "../shared"\n', encoding="utf-8")
    assert config.load(tmp_path).extends == ("../shared",)


def test_unknown_keys_are_ignored(tmp_path):
    (tmp_path / "pbrain.toml").write_text(
        'mystery = 1\n[wiki]\nbogus = true\ndir = "kb"\n[other]\nx = 1\n', encoding="utf-8"
    )
    cfg = config.load(tmp_path)
    assert cfg.wiki.dir == "kb"
    assert not hasattr(cfg.wiki, "bogus")


def test_find_root_walks_up_to_config(tmp_path):
    (tmp_path / "pbrain.toml").write_text("", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert config.find_root(deep) == tmp_path.resolve()
    assert config.load(deep).root == tmp_path.resolve()


def test_find_root_falls_back_to_git_toplevel(tmp_path, helpers):
    helpers.init_repo(tmp_path)
    deep = tmp_path / "x"
    deep.mkdir()
    assert config.find_root(deep) == tmp_path.resolve()


def test_find_root_without_config_or_git_returns_start(tmp_path):
    assert config.find_root(tmp_path).resolve() == tmp_path.resolve()
