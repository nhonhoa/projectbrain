import json

from pbrain.config import Config, ReuseConfig
from pbrain.reuse import store

DATES = '''
def parse_date_text(value):
    """Turn a dd/mm/yyyy or yyyy-mm-dd string into a date; empty or invalid gives None."""
    if not value:
        return None
    return value
'''
MONEY = """
def render_amount(value):
    total = 0
    for part in value:
        total += part
    return total
"""
CATALOG = """
REUSE_CATALOG = (
    {"name": "format_money", "kind": "py-util", "path": "src/app/utils.py",
     "symbol": "format_money", "use_when": "Format an amount as text.",
     "keywords": "money amount currency format"},
    {"name": "Modal", "kind": "js", "path": "src/app/modal.js",
     "use_when": "Show a confirmation dialog.", "keywords": "dialog confirm popup"},
)
KINDS = ("py-util", "js")
"""


def make_project(tmp_path, **reuse):
    (tmp_path / "src/app").mkdir(parents=True)
    (tmp_path / "src/app/dates.py").write_text(DATES, encoding="utf-8")
    (tmp_path / "src/app/money.py").write_text(MONEY, encoding="utf-8")
    (tmp_path / "src/app/utils.py").write_text(
        "def format_money(v):\n    return str(v)\n", encoding="utf-8"
    )
    (tmp_path / "src/app/modal.js").write_text("const Modal = {};\n", encoding="utf-8")
    (tmp_path / "src/tests").mkdir()
    (tmp_path / "src/tests/test_x.py").write_text(DATES, encoding="utf-8")
    (tmp_path / "catalog.py").write_text(CATALOG, encoding="utf-8")
    options = {"roots": ("src",), "catalog": "catalog.py", "summaries": "summaries.json"}
    options.update(reuse)
    return Config(root=tmp_path, reuse=ReuseConfig(**options))


def test_empty_roots_gives_empty_index(tmp_path):
    config = Config(root=tmp_path, reuse=ReuseConfig())
    assert store.refresh(config) == {"files": 0, "reindexed_files": 0, "symbols": 0}
    assert store.search_code(config, ["anything"]) == []


def test_refresh_is_incremental_and_skips_tests(tmp_path):
    config = make_project(tmp_path)
    first = store.refresh(config)
    assert first["files"] == 4 and first["reindexed_files"] == 4
    assert store.refresh(config)["reindexed_files"] == 0
    (tmp_path / "src/app/money.py").write_text(MONEY + "\n# changed\n", encoding="utf-8")
    assert store.refresh(config)["reindexed_files"] == 1
    (tmp_path / "src/app/money.py").unlink()
    assert store.refresh(config)["files"] == 3


def test_search_code_finds_function_by_purpose_words_without_accents(tmp_path):
    config = make_project(tmp_path)
    names = [row["name"] for row in store.search_code(config, ["chuỗi", "invalid", "date"])]
    assert names[:1] == ["parse_date_text"]


def test_search_catalog_scores_name_keywords_and_use_when():
    entries = [
        {
            "name": "format_money",
            "kind": "py-util",
            "use_when": "Format an amount.",
            "keywords": "money",
        },
        {"name": "Modal", "kind": "js", "use_when": "Show a dialog.", "keywords": "confirm"},
    ]
    assert [e["name"] for e in store.search_catalog(entries, ["money"])] == ["format_money"]
    assert [e["name"] for e in store.search_catalog(entries, ["dialog"])] == ["Modal"]
    assert store.search_catalog(entries, []) == []
    assert [e["name"] for e in store.search_catalog(entries, [], kind="js")] == ["Modal"]
    # prefix match for long keywords
    assert [e["name"] for e in store.search_catalog(entries, ["forma"])] == ["format_money"]


def test_format_search_lists_catalog_then_unregistered_code(tmp_path):
    config = make_project(tmp_path)
    lines = store.format_search(config, ["money", "format"]).splitlines()
    assert lines[0].startswith("# Shared")
    assert lines[1].startswith("format_money [py-util] src/app/utils.py")
    code_header = next(i for i, line in enumerate(lines) if line.startswith("# Existing code"))
    # the registered symbol is not repeated in the code tier
    assert not any(line.startswith("format_money [py]") for line in lines[code_header:])


def test_format_search_kind_without_terms_lists_group(tmp_path):
    config = make_project(tmp_path)
    text = store.format_search(config, [], kind="js")
    assert "Modal [js]" in text
    assert "# Existing code" not in text


def test_format_search_shows_extra_entries_with_source(tmp_path):
    config = make_project(tmp_path, catalog="")
    extra = [
        {
            "name": "retry",
            "kind": "py-util",
            "path": "lib/retry.py",
            "use_when": "Retry calls.",
            "keywords": "retry backoff",
            "source": "team-knowledge",
        }
    ]
    text = store.format_search(config, ["retry"], extra_entries=extra)
    assert "retry [py-util] lib/retry.py" in text and "(from team-knowledge)" in text
    assert text.index("(none)") < text.index("retry [py-util]")


def test_undescribed_and_import_summaries_roundtrip(tmp_path):
    config = make_project(tmp_path)
    assert [r["name"] for r in store.undescribed(config)] == ["render_amount"]
    key = "src/app/money.py::render_amount"
    items = {key: {"s": "Sum the parts.", "k": "total"}, "x::y": {"s": "z"}}
    assert store.import_summaries(config, items) == 1
    stored = json.loads((tmp_path / "summaries.json").read_text(encoding="utf-8"))
    assert list(stored) == [key]
    assert store.undescribed(config) == []
    assert [r["name"] for r in store.search_code(config, ["sum"])][:1] == ["render_amount"]
    # changing the code invalidates the stored description
    changed = MONEY.replace("total += part", "total += part * 2")
    (tmp_path / "src/app/money.py").write_text(changed, encoding="utf-8")
    assert [r["name"] for r in store.undescribed(config)] == ["render_amount"]


def test_common_names_are_not_reported_as_undescribed(tmp_path):
    body = "def main():\n    a = 1\n    b = 2\n    c = a + b\n    d = c * 2\n    return d, a, b\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "src/cli.py").write_text(body, encoding="utf-8")
    config = Config(root=tmp_path, reuse=ReuseConfig(roots=("src",)))
    assert store.undescribed(config) == []


def test_sanitize_text_rewrites_secret_like_pairs():
    word = "tok" + "en"
    assert store.sanitize_text(f"{word}: abc") == f"{word} - abc"
    assert store.sanitize_text("tokenizer: ok") == "tokenizer: ok"
    pw = "PASS" + "WORD"
    assert store.sanitize_text(f"{pw} = 1") == f"{pw} - 1"
