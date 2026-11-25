"""JS logic that can be checked under node without a browser: HubPager page math / markup and
UIDialog text handling (message and detail must be set as text, never parsed as HTML).
DOM-heavy behaviour (clicks, SearchCombobox, real rendering) is not covered here."""

import json
import pathlib
import shutil
import subprocess

import pytest
from django.core.paginator import Paginator

HERE = pathlib.Path(__file__).parent
STATIC = HERE.parent / "hub_ui" / "static" / "hub_ui"
DIALOG = HERE.parent / "hub_ui" / "templates" / "hub_ui" / "ui_dialog.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def js():
    proc = subprocess.run(
        ["node", str(HERE / "run_pager_dialog.js"), str(STATIC / "hub-ui.js"), str(DIALOG)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_range_math(js):
    got = {tuple(c["args"]): c["r"] for c in js["ranges"]}
    r = got[(0, 50, 1)]
    assert (r["pages"], r["page"], r["from"], r["to"]) == (1, 1, 0, 0)
    r = got[(51, 50, 2)]
    assert (r["pages"], r["from"], r["to"], r["offset"]) == (2, 51, 51, 50)
    assert got[(50, 50, 1)]["pages"] == 1 and got[(51, 50, 1)]["pages"] == 2
    assert got[(51, 50, 99)]["page"] == 2          # clamped to the last page
    assert got[(120, 50, -4)]["page"] == 1         # clamped to the first page
    assert got[(120, 50, 3)]["to"] == 120
    assert got[(7, 0, 1)]["pageSize"] == 1         # nonsense size never divides by zero


def test_page_list_matches_django_elided_range(js):
    for case in js["lists"]:
        pag = Paginator(list(range(case["pages"])), 1)
        expected = [("…" if n == pag.ELLIPSIS else n) for n in pag.get_elided_page_range(case["page"])]
        assert case["list"] == expected, case


def test_html_visibility_and_classes(js):
    assert js["html"]["single"] == ""
    # total not above the first page size: no bar (same rule as pagination.html)
    assert js["html"]["singleWithSizes"] == ""
    html = js["html"]["multi"]
    for token in ('class="page-item active"', "Trang 2/3", "51–100 / 120 dòng", 'data-page="3"',
                  "dòng/trang", "data-hub-pager-size", 'aria-label="Phân trang"'):
        assert token in html, token
    assert "data-hub-page-size" not in html       # that attribute is the server pager's URL-redirect handler


def test_dialog_message_is_text_and_keeps_newlines(js):
    d = js["dialog"]
    assert d["message"] == '<img src=x onerror=alert(1)> & "q"\nline two'
    assert d["messageInnerHtmlTouched"] is False   # textContent only, no HTML parsing
    assert d["detail"] == "<b>why</b>\nmore" and d["detailHidden"] is False
    assert d["detailHiddenWhenAbsent"] is True
    assert d["whiteSpaceInTemplate"] is True
    assert d["confirmResult"] is False
