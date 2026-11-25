"""Contract test: number-formatting cases recorded from the current behavior, run against the
Python filters and against HubFmt in hub-ui.js (under node). Cases where Python and JS differ
today (half-way rounding, None) are pinned on both sides and flagged agree=false; they are not
"fixed" here."""

import json
import pathlib
import shutil
import subprocess

import pytest

from hub_ui.formatting import format_number_vn, format_vnd

HERE = pathlib.Path(__file__).parent
CASES = json.loads((HERE / "number_format_cases.json").read_text(encoding="utf-8"))["cases"]
JS = HERE.parent / "hub_ui" / "static" / "hub_ui" / "hub-ui.js"


def test_python_side():
    for c in CASES:
        assert format_number_vn(c["value"], c["decimals"]) == c["python"]["vn_number"], c
        assert format_vnd(c["value"]) == c["python"]["format_vnd"], c


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_side():
    res = subprocess.run(
        ["node", str(HERE / "run_hubfmt.js"), str(JS)],
        input=json.dumps(CASES), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    out = json.loads(res.stdout)
    assert len(out) == len(CASES)
    for c, got in zip(CASES, out, strict=True):
        assert got == {"number": c["js"]["number"], "vnd": c["js"]["vnd"]}, c


def test_agreeing_cases_match_across_languages():
    agreeing = [c for c in CASES if c["agree"]]
    assert len(agreeing) > 20
    for c in agreeing:
        assert c["python"]["vn_number"] == c["js"]["number"]
        assert c["python"]["format_vnd"] == c["js"]["vnd"]
