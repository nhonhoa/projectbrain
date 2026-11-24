import json
import sys

import pytest

from pbrain import cases

MODULE = "pbcheck_mod"


@pytest.fixture
def module_cleanup():
    yield
    sys.modules.pop(MODULE, None)


def write_case(helpers, root, expected):
    helpers.write(root, f"{MODULE}.py", "def double(x):\n    return x * 2\n")
    spec = {
        "format": cases.FORMAT,
        "python": {"module": MODULE},
        "cases": [{"id": "c1", "fn": "double", "args": [2], "expected": expected}],
    }
    helpers.write(root, "docs/wiki/cases/a.json", json.dumps(spec))


def test_clean_project_passes(project, run_cli):
    code, out, _ = run_cli("--root", project, "check")
    assert code == 0
    assert "0 error(s), 0 warning(s)" in out


def test_wrong_observed_case_fails(project, helpers, run_cli, module_cleanup):
    write_case(helpers, project, expected=5)
    code, out, _ = run_cli("--root", project, "check")
    assert code == 1
    assert "ERROR" in out and "cases" in out


def test_correct_observed_case_passes(project, helpers, run_cli, module_cleanup):
    write_case(helpers, project, expected=4)
    code, _, _ = run_cli("--root", project, "check")
    assert code == 0


def test_broken_wiki_link_fails(project, helpers, run_cli):
    page = project / "docs/wiki/totals.md"
    page.write_text(
        page.read_text(encoding="utf-8") + "\nSee [gone](missing.md).\n", encoding="utf-8"
    )
    code, out, _ = run_cli("--root", project, "check")
    assert code == 1
    assert "ERROR" in out and "wiki: totals.md" in out


def test_drift_is_warning_only(drift, run_cli):
    code, out, _ = run_cli("--root", drift, "check")
    assert code == 0
    assert "WARNING  wiki: totals.md: DRIFT" in out
    assert "1 warning(s)" in out


def test_drift_not_reported_for_unrelated_file(project, helpers, run_cli):
    helpers.write(project, "src/other.py", "x = 1\n")
    code, out, _ = run_cli("--root", project, "check")
    assert code == 0 and "DRIFT" not in out


def test_instead_of_pattern_fails(guarded, helpers, run_cli, bad_js):
    helpers.write(guarded, "src/zz.js", bad_js)
    code, out, _ = run_cli("--root", guarded, "check")
    assert code == 1
    assert "ERROR    reuse: src/zz.js" in out


def test_explicit_files_are_checked(guarded, helpers, run_cli, bad_js):
    helpers.write(guarded, "src/zz.js", bad_js)
    helpers.commit_all(guarded)
    assert run_cli("--root", guarded, "check")[0] == 0
    assert run_cli("--root", guarded, "check", "src/zz.js")[0] == 1


def test_staged_only_considers_index(guarded, helpers, run_cli, bad_js):
    helpers.write(guarded, "src/zz.js", bad_js)
    code, out, _ = run_cli("--root", guarded, "check", "--staged")
    assert code == 0 and "0 file(s)" in out
    helpers.git(guarded, "add", "src/zz.js")
    code, out, _ = run_cli("--root", guarded, "check", "--staged")
    assert code == 1 and "1 file(s)" in out


def test_ruff_reports_errors_when_enabled(project, helpers, run_cli):
    pytest.importorskip("ruff", reason="ruff is not installed")
    helpers.write(project, "pbrain.toml", 'project = "demo"\n')
    helpers.write(project, "src/unused.py", "import os\n")
    code, out, _ = run_cli("--root", project, "check")
    assert code == 1
    assert "ruff:" in out


def test_ruff_can_be_disabled(project, helpers, run_cli):
    helpers.write(project, "src/unused.py", "import os\n")
    code, out, _ = run_cli("--root", project, "check")
    assert code == 0
    assert "ruff" not in out


def test_changed_files_outside_git_is_empty(tmp_path):
    from pbrain.gitutil import changed_files

    assert changed_files(tmp_path) == []
    assert changed_files(tmp_path, staged=True) == []


def test_changed_files_lists_modified_and_untracked(project, helpers):
    from pbrain.gitutil import changed_files

    helpers.write(project, "src/app.py", "def total(x):\n    return 0\n")
    helpers.write(project, "src/new.py", "y = 1\n")
    assert changed_files(project) == ["src/app.py", "src/new.py"]
    helpers.git(project, "add", "src/new.py")
    assert changed_files(project, staged=True) == ["src/new.py"]
