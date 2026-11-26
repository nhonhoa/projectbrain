import json
import threading
import urllib.request

import pytest

from pbrain import review, scan
from pbrain.config import load

PAGE = """---
title: Totals
type: concept
status: active
trust: agreed
verified: 2026-01-01
---
## Intent
x
## Reality
y
## Gaps
z
## Related
w
"""


def make_project(root, name="demo", broken=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pbrain.toml").write_text(f'project = "{name}"\n', encoding="utf-8")
    wiki = root / "docs" / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("- [Totals](totals.md) - totals\n", encoding="utf-8")
    (wiki / "log.md").write_text("# log\n", encoding="utf-8")
    (wiki / "totals.md").write_text(PAGE, encoding="utf-8")
    if broken:
        add_broken(root)
    return root


def add_broken(root):
    wiki = root / "docs" / "wiki"
    (wiki / "bad.md").write_text(
        PAGE.replace("## Intent", "[x](nope.md)\n## Intent").replace("Totals", "Bad"),
        encoding="utf-8",
    )
    (wiki / "index.md").write_text(
        "- [Totals](totals.md) - totals\n- [Bad](bad.md) - bad\n", encoding="utf-8"
    )


def scan_json(run_cli, tmp_path, *roots, state=None, extra=()):
    state = state or tmp_path / "state.json"
    code, out, err = run_cli(
        "--root", roots[0], "scan", *roots, "--state", state, "--no-sync", "--json", *extra
    )
    return code, json.loads(out), err


def test_first_scan_all_new_then_nothing_new(tmp_path, run_cli):
    a = make_project(tmp_path / "a", "alpha", broken=True)
    b = make_project(tmp_path / "b", "beta")
    code, first, _ = scan_json(run_cli, tmp_path, a, b)
    by_name = {p["name"]: p for p in first["projects"]}
    alpha = by_name["alpha"]
    assert alpha["counts"]["lint_error"] >= 1
    assert sorted(alpha["new"]) == sorted(i for ids in alpha["ids"].values() for i in ids)
    assert alpha["new_counts"]["lint_error"] >= 1
    assert code == 1  # new lint errors on the first scan
    assert first["new_blocking"] >= 1

    code, second, _ = scan_json(run_cli, tmp_path, a, b)
    assert all(not p["new"] and not p["resolved"] for p in second["projects"])
    assert code == 0


def test_new_error_exits_1_and_resolved_reported(tmp_path, run_cli):
    a = make_project(tmp_path / "a")
    scan_json(run_cli, tmp_path, a)
    add_broken(a)
    code, data, _ = scan_json(run_cli, tmp_path, a)
    entry = data["projects"][0]
    assert code == 1 and entry["new"] and not entry["resolved"]
    assert any(i.startswith("lint:bad.md") for i in entry["new"])

    scan_json(run_cli, tmp_path, a)  # baseline including the error
    (a / "docs" / "wiki" / "bad.md").unlink()
    (a / "docs" / "wiki" / "index.md").write_text("- [Totals](totals.md) - t\n", encoding="utf-8")
    code, data, _ = scan_json(run_cli, tmp_path, a)
    entry = data["projects"][0]
    assert code == 0 and not entry["new"]
    assert any(i.startswith("lint:bad.md") for i in entry["resolved"])


@pytest.mark.parametrize("content", [None, "{not json", "[]", '{"projects": 3}'])
def test_missing_or_corrupt_state_is_first_scan(tmp_path, run_cli, content):
    a = make_project(tmp_path / "a", broken=True)
    state = tmp_path / "state.json"
    if content is not None:
        state.write_text(content, encoding="utf-8")
    assert scan.load_state(state) == {}
    code, data, _ = scan_json(run_cli, tmp_path, a, state=state)
    assert code == 1 and data["projects"][0]["new"]
    assert scan.load_state(state)["projects"]  # rewritten with valid content


def test_roots_file_comments_bom_crlf_and_missing_config(tmp_path, run_cli):
    a = make_project(tmp_path / "a", "alpha")
    b = make_project(tmp_path / "b", "beta")
    nope = tmp_path / "nope"
    nope.mkdir()
    text = f"# projects\r\n\r\n{a}\r\n   \r\n{nope}\r\n# {b}\r\n{b}\r\n"
    roots_file = tmp_path / "roots.txt"
    roots_file.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    code, out, err = run_cli(
        "scan", "--roots-file", roots_file, "--state", tmp_path / "s.json", "--no-sync", "--json"
    )
    data = json.loads(out)
    assert code == 0
    assert [p["name"] for p in data["projects"]] == ["alpha", "beta"]
    assert any("nope" in w and "pbrain.toml" in w for w in data["warnings"])
    assert "warning" in err


def test_explicit_root_without_config_is_usage_error(tmp_path, run_cli):
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, err = run_cli("scan", empty, "--state", tmp_path / "s.json", "--no-sync")
    assert code == 2 and "pbrain.toml" in err


def test_text_output(tmp_path, run_cli):
    a = make_project(tmp_path / "a", "alpha", broken=True)
    code, out, _ = run_cli("scan", a, "--state", tmp_path / "s.json", "--no-sync")
    assert code == 1
    assert "alpha" in out and "new" in out and "lint_error" in out


def test_review_api_reports_scan(tmp_path, run_cli):
    a = make_project(tmp_path / "a", "alpha", broken=True)
    state = tmp_path / "state.json"
    scan_json(run_cli, tmp_path, a, state=state)
    cfg = load(a)
    server = review.ReviewServer(("127.0.0.1", 0), [cfg], state_path=state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/projects"
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        assert data["scanned_at"]
        ids = {i["id"] for i in review.build_queue(cfg)}
        assert data["projects"][0]["new"] and set(data["projects"][0]["new"]) <= ids
    finally:
        server.shutdown()


def test_review_api_without_state(tmp_path):
    a = make_project(tmp_path / "a")
    info = review.project_info([load(a)], review.scan_state(tmp_path / "missing.json"))
    assert info[0]["new"] == []
