from pbrain.config import Config, ReuseConfig
from pbrain.reuse import catalog

UTILS = """
LIMIT = 3


def format_money(value):
    return str(value)


class Money:
    def show(self):
        return 1
"""
CATALOG = r"""
KINDS = ("py-util", "js")
CATALOG_EXEMPT = {"src/app/other.js": "internal only"}
REUSE_CATALOG = (
    {"name": "format_money", "kind": "py-util", "path": "src/app/utils.py",
     "symbol": "format_money",
     "use_when": "Format an amount.", "keywords": "money format",
     "instead_of": (r"def\s+fmt_money\(",)},
    {"name": "Money.show", "kind": "py-util", "path": "src/app/utils.py", "symbol": "Money.show",
     "use_when": "Show money.", "keywords": "money show"},
    {"name": "LIMIT", "kind": "py-util", "path": "src/app/utils.py", "symbol": "LIMIT",
     "use_when": "Limit.", "keywords": "limit"},
    {"name": "Modal", "kind": "js", "path": "src/app/modal.js", "symbol": "Modal",
     "use_when": "Dialog.", "keywords": "dialog", "doc": "docs/modal.md#top"},
)
"""


def project(tmp_path, catalog_text=CATALOG):
    (tmp_path / "src/app").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/modal.md").write_text("# modal", encoding="utf-8")
    (tmp_path / "src/app/utils.py").write_text(UTILS, encoding="utf-8")
    (tmp_path / "src/app/modal.js").write_text("const Modal = { open() {} };", encoding="utf-8")
    (tmp_path / "reuse_catalog.py").write_text(catalog_text, encoding="utf-8")
    return Config(root=tmp_path, reuse=ReuseConfig(roots=("src",), catalog="reuse_catalog.py"))


def test_load_catalog_file_reads_entries_kinds_and_exempt(tmp_path):
    config = project(tmp_path)
    loaded = catalog.load_catalog_file(tmp_path / "reuse_catalog.py")
    assert len(loaded["entries"]) == 4
    assert loaded["kinds"] == ("py-util", "js")
    assert loaded["exempt"] == {"src/app/other.js": "internal only"}
    assert len(catalog.load_catalog(config)) == 4


def test_load_catalog_without_config_or_file(tmp_path):
    assert catalog.load_catalog(Config(root=tmp_path)) == []
    missing = Config(root=tmp_path, reuse=ReuseConfig(catalog="nope.py"))
    assert catalog.load_catalog(missing) == []
    assert catalog.validate(Config(root=tmp_path)) == []


def test_load_catalog_without_optional_names(tmp_path):
    (tmp_path / "c.py").write_text("X = 1\n", encoding="utf-8")
    loaded = catalog.load_catalog_file(tmp_path / "c.py")
    assert loaded == {"entries": [], "kinds": None, "exempt": {}}


def test_healthy_catalog_has_no_problems(tmp_path):
    assert catalog.validate(project(tmp_path)) == []


def test_validate_reports_every_kind_of_problem(tmp_path):
    bad = CATALOG.replace(
        "REUSE_CATALOG = (",
        "REUSE_CATALOG = (\n"
        ' {"name": "format_money", "kind": "weird", "path": "src/app/gone.py",\n'
        '  "use_when": "x", "keywords": "k"},\n'
        ' {"name": "Ghost", "kind": "js", "path": "src/app/modal.js", "symbol": "Nowhere",\n'
        '  "use_when": "x", "keywords": "k"},\n'
        ' {"name": "Pyghost", "kind": "py-util", "path": "src/app/utils.py",'
        ' "symbol": "Money.gone",\n'
        '  "use_when": "x", "keywords": "k", "instead_of": ("(",)},',
    )
    problems = "\n".join(catalog.validate(project(tmp_path, bad)))
    for expected in (
        "duplicate name: format_money",
        "unknown kind 'weird'",
        "file not found: src/app/gone.py",
        "'Nowhere' not found",
        "'Money.gone' not found",
        "invalid instead_of regex",
    ):
        assert expected in problems


def test_validate_reports_missing_doc_and_broken_file(tmp_path):
    config = project(tmp_path)
    (tmp_path / "docs/modal.md").unlink()
    assert any("documentation file not found" in p for p in catalog.validate(config))
    (tmp_path / "reuse_catalog.py").write_text("def (", encoding="utf-8")
    assert catalog.validate(config)[0].startswith("catalog file cannot be loaded")


def test_rewrites_finds_files_with_instead_of_patterns(tmp_path):
    config = project(tmp_path)
    (tmp_path / "src/app/dup.py").write_text("def fmt_money(v):\n    return v\n", encoding="utf-8")
    (tmp_path / "src/app/clean.py").write_text("x = 1\n", encoding="utf-8")
    paths = [
        "src/app/dup.py",
        "src/app/clean.py",
        "src/app/utils.py",
        "reuse_catalog.py",
        "gone.py",
    ]
    assert catalog.rewrites(config, paths) == ["src/app/dup.py"]
    # an entry's own path is never reported
    (tmp_path / "src/app/utils.py").write_text(
        "def fmt_money(v):\n    return v\n", encoding="utf-8"
    )
    assert catalog.rewrites(config, ["src/app/utils.py"]) == []
