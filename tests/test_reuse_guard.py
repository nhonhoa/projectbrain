from pbrain.config import Config, ReuseConfig
from pbrain.reuse.guard import check_edit

BAD = "function show" + "Notice(msg) { alert(msg); }"
CATALOG = r"""
REUSE_CATALOG = (
    {"name": "Notify", "kind": "js", "path": "src/static/notify.js", "symbol": "Notify",
     "use_when": "Show a notice.", "keywords": "notice",
     "instead_of": (r"function\s+showNotice\s*\(",)},
)
"""
NEW_JS = "src/static/zz_new.js"


def config(tmp_path):
    (tmp_path / "src/static").mkdir(parents=True)
    (tmp_path / "catalog.py").write_text(CATALOG, encoding="utf-8")
    return Config(root=tmp_path, reuse=ReuseConfig(roots=("src",), catalog="catalog.py"))


def write(path, content):
    return "Write", {"file_path": path, "content": content}


def test_new_file_rewriting_a_shared_component_is_blocked(tmp_path):
    violations = check_edit(config(tmp_path), *write(NEW_JS, BAD))
    assert violations and "Notify" in violations[1]
    assert "pbrain reuse" in violations[1]


def test_absolute_windows_style_path_is_normalised(tmp_path):
    cfg = config(tmp_path)
    absolute = str(tmp_path / NEW_JS).replace("/", "\\")
    assert check_edit(cfg, *write(absolute, BAD))


def test_edit_keeping_existing_match_is_allowed(tmp_path):
    edit = {"file_path": "src/zz.js", "old_string": BAD, "new_string": BAD + "\n// more"}
    assert check_edit(config(tmp_path), "Edit", edit) == []


def test_edit_adding_a_new_match_is_blocked(tmp_path):
    edit = {"file_path": "src/zz.js", "old_string": "x", "new_string": BAD}
    assert check_edit(config(tmp_path), "Edit", edit)


def test_multiedit_adding_a_new_match_is_blocked(tmp_path):
    edits = [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": BAD}]
    payload = {"file_path": "src/zz.js", "edits": edits}
    assert check_edit(config(tmp_path), "MultiEdit", payload)


def test_write_over_existing_match_is_allowed(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / "src/old.js").write_text(BAD, encoding="utf-8")
    assert check_edit(cfg, *write("src/old.js", BAD + "\nvar x = 1;")) == []


def test_out_of_scope_files_are_ignored(tmp_path):
    cfg = config(tmp_path)
    assert check_edit(cfg, *write("docs/zz.js", BAD)) == []
    assert check_edit(cfg, *write("src/zz.txt", BAD)) == []
    assert check_edit(cfg, *write("src/tests/zz.js", BAD)) == []
    assert check_edit(cfg, *write("catalog.py", BAD)) == []
    assert check_edit(cfg, *write("../outside/zz.js", BAD)) == []


def test_component_own_path_is_allowed(tmp_path):
    cfg = config(tmp_path)
    assert check_edit(cfg, *write("src/static/notify.js", "function showNotice() {}")) == []
    assert check_edit(cfg, *write("src/other.js", "function showNotice() {}"))


def test_garbage_input_never_raises(tmp_path):
    cfg = config(tmp_path)
    assert check_edit(cfg, "Write", None) == []
    assert check_edit(cfg, "Read", {"file_path": NEW_JS}) == []
    assert check_edit(Config(root=tmp_path), *write(NEW_JS, BAD)) == []
