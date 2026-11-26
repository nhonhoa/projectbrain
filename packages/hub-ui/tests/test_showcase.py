"""The showcase page: access rules, and the guard that every shared control appears on it."""

import pathlib
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import resolve

from hub_ui import views

PKG = pathlib.Path(__file__).resolve().parent.parent / "hub_ui"
TEMPLATES = PKG / "templates" / "hub_ui"
SHOWCASE = TEMPLATES / "showcase.html"

# Public JS modules (window.X). A new one must be added here AND shown on the showcase page.
JS_MODULES = [
    "HubFmt", "HubNumber", "HubState", "HubTabs", "HubApi", "HubDom", "HubFilterBar", "HubDate",
    "HubPeriod", "HubTable", "HubPager", "SearchCombobox", "UIDialog",
]


def get(path="/showcase/", staff=None):
    request = RequestFactory().get(path)
    request.user = SimpleNamespace(is_active=True, is_staff=bool(staff)) if staff is not None else None
    return views.showcase(request)


@override_settings(DEBUG=True)
def test_renders_with_debug():
    response = get()
    html = response.content.decode()
    assert response.status_code == 200
    for asset in ("hub-ui-base.css", "hub-ui.css", "hub-ui.js", "search-combobox.js"):
        assert f"hub_ui/{asset}" in html
    assert "1.234.567,89" in html  # server-side formatting ran
    assert "Trang 3/13" in html and "window.UIDialog" in html


@override_settings(DEBUG=True)
def test_width_parameter():
    assert "max-width: 768px" in get("/showcase/?w=768").content.decode()
    assert "max-width: " not in get("/showcase/?w=abc").content.decode().split('id="showcase-frame"')[1][:120]


@override_settings(DEBUG=False)
def test_404_when_not_debug_and_not_staff():
    with pytest.raises(Http404):
        get()
    with pytest.raises(Http404):
        get(staff=False)


@override_settings(DEBUG=False)
def test_staff_allowed_without_debug():
    assert get(staff=True).status_code == 200


def test_urls_resolve():
    match = resolve("/showcase/", urlconf="hub_ui.urls")
    assert match.view_name == "hub_ui:showcase" or match.url_name == "showcase"


def test_every_template_is_included_in_showcase():
    source = SHOWCASE.read_text(encoding="utf-8")
    included = set(re.findall(r'\{%\s*include\s+"hub_ui/([\w.]+)"', source))
    shared = {p.name for p in TEMPLATES.glob("*.html")} - {"showcase.html"}
    assert shared, "no shared templates found"
    missing = shared - included
    assert not missing, f"add these templates to showcase.html: {sorted(missing)}"


@pytest.mark.parametrize("name", JS_MODULES)
def test_every_js_module_is_on_showcase(name):
    source = SHOWCASE.read_text(encoding="utf-8")
    script = source[source.rindex("<script>"):]  # the demo script, not the page header comments
    assert re.search(rf"\b{name}\b", script), f"{name} is not exercised in the showcase script"


def test_js_module_list_matches_window_exports():
    exported = set()
    for js in (PKG / "static" / "hub_ui").glob("*.js"):
        exported |= set(re.findall(r"window\.(\w+)\s*=", js.read_text(encoding="utf-8")))
    dialog = (TEMPLATES / "ui_dialog.html").read_text(encoding="utf-8")
    exported |= set(re.findall(r"window\.(\w+)\s*=", dialog))
    new = exported - set(JS_MODULES)
    assert not new, f"new public JS modules must be added to the showcase and JS_MODULES: {sorted(new)}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@override_settings(DEBUG=True)
def test_showcase_script_blocks_parse(tmp_path):
    html = get().content.decode()
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert blocks
    for i, code in enumerate(blocks):
        target = tmp_path / f"block{i}.js"
        target.write_text(code, encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
