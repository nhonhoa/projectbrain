import json
import sys
from datetime import date, datetime
from decimal import Decimal

import pytest

from pbrain import cases
from pbrain.config import CasesConfig, Config

MODULE = """
import os
from decimal import Decimal

def double(x):
    return x * 2

def money(value):
    return Decimal(value) + Decimal("0.5")

def boom():
    raise ValueError("nope")

def env_value():
    return os.environ.get("PBRAIN_TEST_KEY")
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pbtarget.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "cases").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "decision.md").write_text("# d\n", encoding="utf-8")
    yield Config(root=tmp_path, cases=CasesConfig(dir="cases"))
    sys.modules.pop("pbtarget", None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def write_spec(config, cases_list, name="a.json", **extra):
    spec = {"format": cases.FORMAT, "python": {"module": "pbtarget"}, "cases": cases_list}
    spec.update(extra)
    path = config.cases_dir / name
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_encode_decode_roundtrip():
    value = {
        "d": date(2025, 4, 1),
        "dt": datetime(2025, 4, 1, 8, 30),
        "n": Decimal("1.50"),
        "l": [1, (2, 3)],
        "inf": float("inf"),
    }
    encoded = cases.encode(value)
    assert encoded["d"] == {"$date": "2025-04-01"}
    assert encoded["n"] == {"$decimal": "1.50"}
    json.dumps(encoded)
    decoded = cases.decode(encoded)
    assert decoded["d"] == date(2025, 4, 1)
    assert decoded["dt"] == datetime(2025, 4, 1, 8, 30)
    assert decoded["n"] == Decimal("1.50") and str(decoded["n"]) == "1.50"
    assert decoded["l"] == [1, [2, 3]]
    assert decoded["inf"] == float("inf")


def test_encode_rejects_unknown_type():
    with pytest.raises(TypeError):
        cases.encode(object())


def test_same():
    assert cases.same(1, 1.0)
    assert not cases.same(True, 1)
    assert not cases.same(1, True)
    assert cases.same([1, {"a": 2}], [1.0, {"a": 2}])
    assert not cases.same([1], [1, 2])
    assert not cases.same({"a": 1}, {"b": 1})
    assert not cases.same("1", 1)


def test_record_fills_expected_and_keeps_decided(project):
    path = write_spec(
        project,
        [
            {"id": "c1", "fn": "double", "args": [4]},
            {"id": "c2", "fn": "money", "args": ["1.25"]},
            {"id": "c3", "fn": "boom"},
            {
                "id": "c4",
                "fn": "double",
                "args": [1],
                "origin": "decided",
                "expected": 99,
                "decision": "../wiki/decision.md",
            },
        ],
    )
    assert cases.record(project, path) == 0
    spec = json.loads(path.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in spec["cases"]}
    assert by_id["c1"]["expected"] == 8 and by_id["c1"]["origin"] == "observed"
    assert by_id["c2"]["expected"] == {"$decimal": "1.75"}
    assert by_id["c3"]["expected"] == {"$error": "ValueError"}
    assert by_id["c4"]["expected"] == 99
    assert "date" in spec["recorded"]


def test_refresh_reports_changed(project, capsys):
    path = write_spec(project, [{"id": "c1", "fn": "double", "args": [4], "expected": 7}])
    assert cases.record(project, path) == 0
    assert json.loads(path.read_text())["cases"][0]["expected"] == 7
    assert cases.record(project, path, refresh=True) == 0
    assert "CHANGED  a.json: c1" in capsys.readouterr().out
    assert json.loads(path.read_text())["cases"][0]["expected"] == 8


def test_check_observed_wrong_is_error(project, capsys):
    path = write_spec(
        project,
        [
            {"id": "ok", "fn": "double", "args": [2], "expected": 4},
            {"id": "bad", "fn": "double", "args": [2], "expected": 5},
        ],
    )
    assert cases.check(project) == (1, 0, 2)
    assert "ERROR  a.json: bad" in capsys.readouterr().out
    assert cases.check(project, [path]) == (1, 0, 2)


def test_check_decided_warning_then_strict_error(project):
    write_spec(
        project,
        [
            {
                "id": "d",
                "fn": "double",
                "args": [2],
                "origin": "decided",
                "expected": 5,
                "decision": "../wiki/decision.md",
            }
        ],
    )
    assert cases.check(project) == (0, 1, 1)
    assert cases.check(project, strict=True) == (1, 0, 1)


def test_decided_pointing_to_missing_page_is_error(project, capsys):
    write_spec(
        project,
        [
            {
                "id": "d",
                "fn": "double",
                "args": [2],
                "origin": "decided",
                "expected": 4,
                "decision": "../wiki/missing.md",
            }
        ],
    )
    errors, _, _ = cases.check(project)
    assert errors == 1
    assert "must point `decision` to a real page" in capsys.readouterr().out


def test_missing_expected_and_bad_target(project, capsys):
    write_spec(project, [{"id": "c", "fn": "double", "args": [1]}], target="does/not/exist.py")
    errors, _, _ = cases.check(project)
    assert errors == 1
    assert "does not exist" in capsys.readouterr().out
    write_spec(project, [{"id": "c", "fn": "double", "args": [1]}])
    assert cases.check(project)[0] == 1
    assert "run `record` first" in capsys.readouterr().out


def test_env_and_setup(project):
    (project.root / "pbsetup.py").write_text(
        "import os\n\ndef init():\n    os.environ['PBRAIN_TEST_KEY'] = 'from-setup'\n",
        encoding="utf-8",
    )
    spec = {
        "format": cases.FORMAT,
        "python": {
            "module": "pbtarget",
            "env": {"PBRAIN_TEST_KEY": "from-env"},
            "setup": "pbsetup:init",
        },
        "cases": [{"id": "e", "fn": "env_value", "expected": "from-setup"}],
    }
    path = project.cases_dir / "env.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    try:
        assert cases.check(project, [path]) == (0, 0, 1)
    finally:
        sys.modules.pop("pbsetup", None)


def test_env_sets_default_only(project, monkeypatch):
    monkeypatch.delenv("PBRAIN_TEST_KEY", raising=False)
    spec = {
        "format": cases.FORMAT,
        "python": {"module": "pbtarget", "env": {"PBRAIN_TEST_KEY": "v"}},
        "cases": [{"id": "e", "fn": "env_value", "expected": "v"}],
    }
    path = project.cases_dir / "env.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    try:
        assert cases.check(project, [path]) == (0, 0, 1)
    finally:
        monkeypatch.delenv("PBRAIN_TEST_KEY", raising=False)


def test_command_without_case_files(tmp_path, capsys):
    config = Config(root=tmp_path)
    args = type("Args", (), {"files": [], "strict": False})()
    assert cases._check_command(args, config) == 0
    assert "No behavior case files" in capsys.readouterr().out


def test_record_resolves_path_under_root(project, tmp_path, monkeypatch):
    path = write_spec(project, [{"id": "c1", "fn": "double", "args": [2]}])
    monkeypatch.chdir(tmp_path.parent)
    assert cases.record(project, "cases/" + path.name) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["cases"][0]["expected"] == 4
