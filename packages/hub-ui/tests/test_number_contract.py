"""Contract test: one list of expected outputs (tests/number_format_cases.json), run against the
Python filters and against HubFmt in hub-ui.js (under node). Both sides must produce exactly the
same text: half away from zero on the decimal as written, no "-0", empty/non-numeric -> "".
There is deliberately no per-side exception list: any disagreement fails a test."""

import json
import pathlib
import shutil
import subprocess
from decimal import Decimal

import pytest

from hub_ui.formatting import format_number_vn, format_vnd

HERE = pathlib.Path(__file__).parent
CASES = json.loads((HERE / "number_format_cases.json").read_text(encoding="utf-8"))["cases"]
JS = HERE.parent / "hub_ui" / "static" / "hub_ui" / "hub-ui.js"


def test_cases_cover_the_rounding_rule():
    assert len(CASES) > 80
    numbers = {(c["value"], c["decimals"]): c["number"] for c in CASES}
    assert numbers[(2.5, 0)] == "3"
    assert numbers[(1.005, 2)] == "1,01"
    assert numbers[(-0.5, 0)] == "-1"
    assert numbers[(None, 0)] == ""


def test_python_side():
    for c in CASES:
        assert format_number_vn(c["value"], c["decimals"]) == c["number"], c
        assert format_vnd(c["value"]) == c["vnd"], c


def test_python_decimal_and_int_inputs():
    assert format_number_vn(Decimal("1.005"), 2) == "1,01"
    assert format_number_vn(Decimal("-2.5"), 0) == "-3"
    assert format_number_vn(Decimal("-0.001"), 2) == "0,00"
    assert format_number_vn(Decimal("NaN"), 2) == ""
    assert format_number_vn(Decimal("12345678901234567890.125"), 2) == "12.345.678.901.234.567.890,13"
    assert format_number_vn(2**70, 0) == "1.180.591.620.717.411.303.424"
    assert format_vnd(Decimal("0.5")) == "1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_side():
    res = subprocess.run(
        ["node", str(HERE / "run_hubfmt.js"), str(JS)],
        input=json.dumps(CASES), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    out = json.loads(res.stdout)
    assert len(out) == len(CASES)
    for c, got in zip(CASES, out, strict=True):
        assert got == {"number": c["number"], "vnd": c["vnd"], "decimal": c["decimal"]}, c
