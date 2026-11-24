import os
import subprocess
from datetime import date

from pbrain import wiki
from pbrain.config import Config, WikiConfig

PAGE = """---
title: Invoice totals
type: concept
status: active
trust: agreed
covers:
{covers}
sources:
{sources}
verified: {verified}
---
## Intent
x
## Reality
x
## Gaps
x
## Related
x
"""


def make_config(tmp_path, **wiki_args):
    (tmp_path / "wiki").mkdir(exist_ok=True)
    return Config(root=tmp_path, wiki=WikiConfig(dir="wiki", **wiki_args))


def write_page(tmp_path, name="invoice.md", covers=(), sources=(), verified="2026-01-01"):
    def block(items):
        return "\n".join(f"  - {item}" for item in items)

    text = PAGE.format(covers=block(covers), sources=block(sources), verified=verified)
    path = tmp_path / "wiki" / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_index(tmp_path, *names):
    lines = [f"- [{n}]({n}) - page" for n in names]
    (tmp_path / "wiki" / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "wiki" / "log.md").write_text("# log\n", encoding="utf-8")


def messages(findings, level=None):
    return [m for lv, _, m in findings if level is None or lv == level]


def git(root, *args, date_str=None):
    env = dict(os.environ)
    if date_str:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date_str
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


def init_repo(root):
    git(root, "init", "-q")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")


def commit_all(root, date_str):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change", date_str=date_str)


def test_parse_front_matter_valid_and_broken():
    meta = wiki.parse_front_matter("---\ntitle: A\ncovers:\n  - a.py::f\n---\nbody")
    assert meta == {"title": "A", "covers": ["a.py::f"]}
    assert wiki.parse_front_matter("no front matter") is None
    assert wiki.parse_front_matter("---\ntitle: A\n") is None


def test_missing_wiki_dir(tmp_path):
    config = Config(root=tmp_path)
    assert wiki.lint_wiki(config) == ([], 0)
    assert wiki.pages_covering(config, ["a.py"]) == []


def test_clean_page_has_no_findings(tmp_path):
    config = make_config(tmp_path)
    write_page(tmp_path)
    write_index(tmp_path, "invoice.md")
    findings, count = wiki.lint_wiki(config, today=date(2026, 1, 2))
    assert (findings, count) == ([], 1)


def test_bad_front_matter_and_enum(tmp_path):
    config = make_config(tmp_path)
    (tmp_path / "wiki" / "bad.md").write_text("no header\n", encoding="utf-8")
    page = write_page(tmp_path, "worse.md")
    page.write_text(page.read_text().replace("type: concept", "type: nonsense"), encoding="utf-8")
    write_index(tmp_path, "bad.md", "worse.md")
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 2))
    errors = messages(findings, "ERROR")
    assert any("front matter" in m for m in errors)
    assert any("`type: nonsense`" in m for m in errors)


def test_broken_link_and_orphan(tmp_path):
    config = make_config(tmp_path)
    page = write_page(tmp_path)
    page.write_text(page.read_text() + "\nSee [x](missing.md)\n", encoding="utf-8")
    write_page(tmp_path, "lonely.md")
    write_index(tmp_path, "invoice.md", "ghost.md")
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 2))
    assert ("ERROR", "invoice.md", "broken link: `missing.md`") in findings
    assert ("ERROR", "lonely.md", "ORPHAN: not listed in index.md") in findings
    assert ("ERROR", "index.md", "broken link: `ghost.md`") in findings


def test_subdirectories_and_reserved_skipped(tmp_path):
    config = make_config(tmp_path)
    write_index(tmp_path, "invoice.md")
    write_page(tmp_path)
    (tmp_path / "wiki" / "_inbox").mkdir()
    (tmp_path / "wiki" / "_inbox" / "raw.md").write_text("junk", encoding="utf-8")
    (tmp_path / "wiki" / "SCHEMA.md").write_text("# schema", encoding="utf-8")
    findings, count = wiki.lint_wiki(config, today=date(2026, 1, 2))
    assert count == 1 and findings == []


def test_covers_missing_file_and_symbol(tmp_path):
    config = make_config(tmp_path)
    (tmp_path / "mod.py").write_text("def real():\n    return 1\n", encoding="utf-8")
    write_page(tmp_path, covers=["nope.py", "mod.py::real", "mod.py::ghost"])
    write_index(tmp_path, "invoice.md")
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 2))
    errors = messages(findings, "ERROR")
    assert "covers: `nope.py` does not exist" in errors
    assert "covers: `ghost` not found in `mod.py`" in errors
    assert not any("real" in m for m in errors)


def test_sources_external_ids_are_not_checked(tmp_path):
    config = make_config(tmp_path)
    write_page(
        tmp_path,
        sources=["lsn_ab12cd", "deadbeef", "https://example.com/x", "docs/gone.md"],
    )
    write_index(tmp_path, "invoice.md")
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 2))
    assert messages(findings, "ERROR") == ["sources: `docs/gone.md` does not exist"]


def test_stale_sections_and_length(tmp_path):
    config = make_config(tmp_path, sections=("## Intent", "## Custom"), max_lines=10)
    page = write_page(tmp_path, verified="2025-01-01")
    page.write_text(page.read_text() + "\n" * 20, encoding="utf-8")
    write_index(tmp_path, "invoice.md")
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 1))
    warns = messages(findings, "WARNING")
    assert any(m.startswith("STALE: not confirmed for 365 days") for m in warns)
    assert "missing section `## Custom`" in warns
    assert not any("## Intent" in m for m in warns)
    assert any("lines long" in m for m in warns)
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 1), max_age=400)
    assert not any(m.startswith("STALE") for m in messages(findings))


def drift_repo(tmp_path, covers):
    init_repo(tmp_path)
    (tmp_path / "mod.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
    )
    commit_all(tmp_path, "2026-01-01T10:00:00")
    config = make_config(tmp_path)
    write_page(tmp_path, covers=covers, verified="2026-01-05")
    write_index(tmp_path, "invoice.md")
    commit_all(tmp_path, "2026-01-05T10:00:00")
    return config


def drifted(config):
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 10))
    return [m for m in messages(findings, "WARNING") if m.startswith("DRIFT")]


def edit_and_commit(tmp_path, old, new, when):
    source = (tmp_path / "mod.py").read_text().replace(old, new)
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    commit_all(tmp_path, when)


def test_drift_other_function_changed_is_not_drift(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    assert drifted(config) == []
    edit_and_commit(tmp_path, "return 2", "return 22", "2026-01-08T10:00:00")
    assert drifted(config) == []


def test_drift_declared_function_changed(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    edit_and_commit(tmp_path, "return 1", "return 11", "2026-01-08T10:00:00")
    found = drifted(config)
    assert len(found) == 1 and "function body changed" in found[0]


def test_drift_whole_file_entry(tmp_path):
    config = drift_repo(tmp_path, ["mod.py"])
    edit_and_commit(tmp_path, "return 2", "return 22", "2026-01-08T10:00:00")
    found = drifted(config)
    assert len(found) == 1 and "file changed" in found[0]


def test_changes_before_verified_day_are_not_drift(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    commit_all(tmp_path, "2026-01-01T10:00:00")
    (tmp_path / "mod.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    commit_all(tmp_path, "2026-01-04T10:00:00")
    config = make_config(tmp_path)
    write_page(tmp_path, covers=["mod.py::alpha"], verified="2026-01-05")
    write_index(tmp_path, "invoice.md")
    assert drifted(config) == []


def test_pages_covering_and_confirm(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    write_page(tmp_path, "other.md", covers=["x.py"], verified="2026-01-05")
    edit_and_commit(tmp_path, "return 1", "return 11", "2026-01-08T10:00:00")

    hits = wiki.pages_covering(config, ["mod.py"])
    assert [h["page"] for h in hits] == ["invoice.md"]
    assert hits[0]["covers"] == ["mod.py::alpha"]
    assert hits[0]["title"] == "Invoice totals"
    assert hits[0]["verified"] == "2026-01-05"
    assert len(hits[0]["drift"]) == 1
    assert wiki.pages_covering(config, [".\\x.py"])[0]["page"] == "other.md"

    done = wiki.confirm(config, ["invoice.md", "missing.md"], today=date(2026, 1, 9))
    assert done == ["invoice.md"]
    assert wiki.pages_covering(config, ["mod.py"])[0]["verified"] == "2026-01-09"


def test_pages_covering_skips_inactive(tmp_path):
    config = make_config(tmp_path)
    page = write_page(tmp_path, covers=["mod.py"])
    page.write_text(page.read_text().replace("status: active", "status: deprecated"))
    assert wiki.pages_covering(config, ["mod.py"]) == []


def test_confirm_keeps_line_endings_and_rest(tmp_path):
    config = make_config(tmp_path)
    page = tmp_path / "wiki" / "crlf.md"
    page.write_bytes(b"---\r\ntitle: T\r\nverified: 2026-01-01 # note\r\n---\r\nverified: keep\r\n")
    assert wiki.confirm(config, ["crlf.md"], today=date(2026, 2, 3)) == ["crlf.md"]
    assert page.read_bytes() == (
        b"---\r\ntitle: T\r\nverified: 2026-02-03 # note\r\n---\r\nverified: keep\r\n"
    )


def test_symbol_source_nested():
    text = (
        "class A:\n    @property\n    def b(self):\n        return 1\n\n"
        "    def c(self):\n        pass\n"
    )
    assert wiki.symbol_source(text, "A.b") == "    @property\n    def b(self):\n        return 1"
    assert wiki.symbol_source(text, "A.zzz") is None
    assert wiki.symbol_source("def (", "x") is None


def test_command_lint_exit_codes(tmp_path, capsys):
    args = type("Args", (), {"max_age": None})()
    assert wiki._lint_command(args, Config(root=tmp_path)) == 0
    assert "No wiki directory" in capsys.readouterr().out
    config = make_config(tmp_path)
    write_page(tmp_path)
    assert wiki._lint_command(args, config) == 1


def alpha_drifted(config):
    hits = wiki.pages_covering(config, ["mod.py"])
    return drifted(config), hits[0]["drift"]


def edit_uncommitted(tmp_path, old, new):
    source = (tmp_path / "mod.py").read_text().replace(old, new)
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")


def test_confirm_clears_drift_for_uncommitted_code(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    edit_uncommitted(tmp_path, "return 1", "return 11")
    lint, covering = alpha_drifted(config)
    assert len(lint) == 1 and len(covering) == 1
    assert wiki.confirm(config, ["invoice.md"], today=date(2026, 1, 9)) == ["invoice.md"]
    assert alpha_drifted(config) == ([], [])
    edit_uncommitted(tmp_path, "return 11", "return 12")
    lint, covering = alpha_drifted(config)
    assert len(lint) == 1 and len(covering) == 1


def test_confirmed_page_ignores_other_function_edits(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    edit_uncommitted(tmp_path, "return 1", "return 11")
    wiki.confirm(config, ["invoice.md"], today=date(2026, 1, 9))
    edit_uncommitted(tmp_path, "return 2", "return 22")
    assert alpha_drifted(config) == ([], [])


def test_confirmed_then_committed_next_day_is_not_drift(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    edit_uncommitted(tmp_path, "return 1", "return 11")
    wiki.confirm(config, ["invoice.md"], today=date(2026, 1, 9))
    commit_all(tmp_path, "2026-01-10T10:00:00")
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 10))
    assert [m for m in messages(findings, "WARNING") if m.startswith("DRIFT")] == []
    assert wiki.pages_covering(config, ["mod.py"])[0]["drift"] == []


def test_stale_verified_hash_without_git_drift_gets_generic_message(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    page = tmp_path / "wiki" / "invoice.md"
    page.write_text(
        page.read_text().replace(
            "verified: 2026-01-05", "verified: 2026-01-05\nverified_hash: 0000"
        )
    )
    lint, covering = alpha_drifted(config)
    assert lint == ["DRIFT: covered code changed since the page was confirmed"]
    assert covering == ["covered code changed since the page was confirmed"]


def test_confirm_twice_keeps_one_hash_line(tmp_path):
    config = drift_repo(tmp_path, ["mod.py::alpha"])
    page = tmp_path / "wiki" / "invoice.md"
    for day in (8, 9):
        wiki.confirm(config, ["invoice.md"], today=date(2026, 1, day))
    text = page.read_text(encoding="utf-8")
    assert text.count("verified_hash:") == 1
    assert "verified: 2026-01-09\nverified_hash: " in text
    assert wiki.parse_front_matter(text)["verified_hash"] == wiki.covers_hash(
        config, ["mod.py::alpha"]
    )


def test_confirm_crlf_keeps_crlf_and_covers_less_page_gets_no_hash(tmp_path):
    config = make_config(tmp_path)
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    page = tmp_path / "wiki" / "crlf.md"
    page.write_bytes(
        b"---\r\ntitle: T\r\ncovers:\r\n  - mod.py::f\r\nverified: 2026-01-01\r\n---\r\n"
    )
    wiki.confirm(config, ["crlf.md"], today=date(2026, 2, 3))
    data = page.read_bytes()
    assert b"verified: 2026-02-03\r\nverified_hash: " in data
    assert data.count(b"\n") == data.count(b"\r\n")
    plain = write_page(tmp_path, "plain.md")
    plain.write_text(
        plain.read_text().replace("verified: 2026-01-01", "verified: 2026-01-01\nverified_hash: ab")
    )
    wiki.confirm(config, ["plain.md"], today=date(2026, 2, 3))
    assert "verified_hash" not in plain.read_text()


def test_covers_hash_depends_on_content_name_and_presence(tmp_path):
    config = make_config(tmp_path)
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    base = wiki.covers_hash(config, ["mod.py::f"])
    assert len(base) == 16
    assert wiki.covers_hash(config, ["mod.py::f", "nope.py"]) != base
    assert wiki.covers_hash(config, ["mod.py::g"]) != base
    (tmp_path / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    assert wiki.covers_hash(config, ["mod.py::f"]) != base


def test_confirm_accepts_name_forms_and_paths(tmp_path):
    config = make_config(tmp_path)
    write_page(tmp_path)
    page = tmp_path / "wiki" / "invoice.md"
    forms = ["invoice", "invoice.md", "wiki/invoice.md", "wiki" + chr(92) + "invoice", str(page)]
    for form in forms:
        assert wiki.confirm(config, [form], today=date(2026, 3, 1)) == ["invoice.md"]
    assert wiki.confirm(config, ["nope", "wiki/../invoice.md"], today=date(2026, 3, 1)) == []


def test_confirm_command_reports_missing_and_still_confirms_others(tmp_path, capsys):
    config = make_config(tmp_path)
    write_page(tmp_path)
    args = type("Args", (), {"pages": ["ghost", "invoice"]})()
    assert wiki._confirm_command(args, config) == 1
    out = capsys.readouterr().out
    assert "not found: ghost" in out and "confirmed invoice.md" in out
    assert "verified: " + date.today().isoformat() in (tmp_path / "wiki" / "invoice.md").read_text()


def test_verified_hash_is_not_a_lint_problem(tmp_path):
    config = make_config(tmp_path)
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    write_page(tmp_path, covers=["mod.py::f"])
    write_index(tmp_path, "invoice.md")
    wiki.confirm(config, ["invoice.md"], today=date(2026, 1, 2))
    findings, _ = wiki.lint_wiki(config, today=date(2026, 1, 3))
    assert findings == []
