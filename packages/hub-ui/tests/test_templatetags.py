from django.template import Context, Template
from django.test import RequestFactory

from hub_ui.formatting import format_number_vn, format_vnd
from hub_ui.templatetags import hub_ui as tags


def render(source, **ctx):
    return Template("{% load hub_ui %}" + source).render(Context(ctx))


def test_format_vnd_basic():
    assert format_vnd(1500000) == "1.500.000"
    assert format_vnd(0) == "0"
    assert format_vnd(-1234567) == "-1.234.567"
    assert format_vnd(None) == "0"
    assert format_vnd("abc") == "abc"


def test_format_number_vn_decimals():
    assert format_number_vn(1234567.891, 2) == "1.234.567,89"
    assert format_number_vn(None, 2) == "0,00"


def test_filters_in_templates():
    assert render("{{ v|format_vnd }}", v=1234567) == "1.234.567"
    assert render("{{ v|vn_number:2 }}", v=1234.5) == "1.234,50"
    assert render("{{ v|vn_number }}", v=1234.5) == "1.234"
    assert render("{{ v|vn_number:'x' }}", v=12) == "12"


def test_vn_number_bad_places_falls_back_to_zero():
    assert tags.vn_number(12.7, None) == "13"


def test_query_replace_keeps_other_params():
    request = RequestFactory().get("/?q=abc&page=3")
    out = render('{% query_replace "page" 5 %}', request=request)
    assert "q=abc" in out and "page=5" in out and "page=3" not in out
