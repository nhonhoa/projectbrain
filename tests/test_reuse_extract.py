from pbrain.reuse.extract import extract_symbols

PY = '''
def format_money(value):
    """Format an amount with thousand separators."""
    return f"{value:,}"


class Report:
    def render(self):
        # build the page
        return 1

    def inner(self):
        def nested():
            return 2
        return nested
'''

JS = """
// Escape html special characters.
function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;");
}
const Hub = {
  fmt(value) {
    return value;
  },
};
const double = (x) => x * 2;
"""


def by_name(symbols):
    return {symbol["name"]: symbol for symbol in symbols}


def test_python_symbols_include_methods_but_not_nested_functions():
    symbols = by_name(extract_symbols("src/app/utils.py", PY))
    assert set(symbols) == {"format_money", "Report", "Report.render", "Report.inner"}
    assert symbols["format_money"]["doc"] == "Format an amount with thousand separators."
    assert symbols["format_money"]["top"] == 1
    assert symbols["Report.render"]["top"] == 0
    assert "build" in symbols["Report.render"]["text"].split()


def test_python_syntax_error_gives_no_symbols():
    assert extract_symbols("a.py", "def broken(:") == []


def test_variable_renaming_keeps_the_fingerprint():
    one = "def f(a):\n    b = a + 1\n    return b\n"
    two = "def g(x):\n    y = x + 5\n    return y\n"
    assert extract_symbols("a.py", one)[0]["fp"] == extract_symbols("a.py", two)[0]["fp"]


def test_javascript_functions_methods_and_arrows():
    symbols = by_name(extract_symbols("static/hub.js", JS))
    assert set(symbols) == {"escapeHtml", "Hub.fmt", "double"}
    assert symbols["escapeHtml"]["doc"] == "Escape html special characters."
    assert symbols["escapeHtml"]["kind"] == "js"


def test_html_script_blocks_keep_line_numbers():
    html = (
        "<p>hi</p>\n<script>\nfunction pick(a) {\n  return a;\n}\n</script>\n"
        "<script src='x.js'></script>"
    )
    symbols = extract_symbols("t/page.html", html)
    assert [(s["name"], s["start"]) for s in symbols] == [("pick", 3)]


def test_unsupported_suffix_is_empty():
    assert extract_symbols("notes.md", "def x(): pass") == []
