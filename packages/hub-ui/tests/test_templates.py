import pytest
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory


def request():
    return RequestFactory().get("/?q=x")


def test_ui_dialog_renders():
    html = render_to_string("hub_ui/ui_dialog.html")
    assert "window.UIDialog" in html


def test_empty_state_renders():
    ctx = {"es_title": "Nothing here", "es_text": "Pick a period."}
    html = render_to_string("hub_ui/empty_state.html", ctx)
    assert "Nothing here" in html and "Pick a period." in html and "bi-inbox" in html


def test_pagination_renders():
    page = Paginator(list(range(250)), 20).page(3)
    html = render_to_string(
        "hub_ui/pagination.html", {"page_obj": page, "pg_sizes": (20, 50, 100)}, request=request()
    )
    assert "Trang 3/13" in html
    assert "page=4" in html and "q=x" in html
    assert 'data-hub-page-size="page"' in html


def test_pagination_hidden_for_single_page():
    page = Paginator(list(range(5)), 20).page(1)
    html = render_to_string("hub_ui/pagination.html", {"page_obj": page}, request=request())
    assert "<nav" not in html


@pytest.mark.parametrize(
    ("mode", "marker"),
    [("month", "data-period-month"), ("range", "data-period-quick"), ("day", "data-period-value")],
)
def test_period_filter_renders(mode, marker):
    html = render_to_string("hub_ui/period_filter.html", {"pf_mode": mode, "pf_month": 3, "pf_year": 2026})
    assert marker in html and f'data-hub-period="{mode}"' in html


def test_date_range_filter_renders():
    ctx = {"dr_id": "x", "dr_name_from": "a", "dr_name_to": "b"}
    html = render_to_string("hub_ui/date_range_filter.html", ctx)
    assert 'id="x_from"' in html and 'name="b"' in html and "applyQuickDate" in html
