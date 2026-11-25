"""The 0.3.0 CSS split must lose nothing from 0.2.0 and keep hub-ui.css free of global restyling."""

import pathlib
import re

HERE = pathlib.Path(__file__).parent
STATIC = HERE.parent / "hub_ui" / "static" / "hub_ui"
OLD = HERE / "fixtures" / "hub-ui-0.2.0.css"      # frozen copy of the 0.2.0 stylesheet


def rules(css):
    """{(at-rule context, single selector): declarations} for every rule. Selector lists are split so a
    rule that moved between files in pieces is still found. Declarations are whitespace-normalised."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = {}
    stack = []          # at-rule contexts
    for m in re.finditer(r"([^{};]*)\{|\}", css):
        if m.group(0) == "}":
            if stack:
                stack.pop()
            continue
        head = " ".join(m.group(1).split())
        if head.startswith("@"):
            stack.append((head, "at"))
            continue
        end = css.index("}", m.end())
        decl = " ".join(css[m.end():end].split())
        ctx = " / ".join(h for h, k in stack if k == "at")
        if ctx.startswith("@keyframes"):
            out[(ctx, head)] = decl
        else:
            for sel in head.split(","):
                out[(ctx, " ".join(sel.split()))] = decl
        stack.append((head, "rule"))
    return out


def test_every_020_selector_lives_in_one_of_the_new_files():
    old = rules(OLD.read_text(encoding="utf-8"))
    base = rules((STATIC / "hub-ui-base.css").read_text(encoding="utf-8"))
    comp = rules((STATIC / "hub-ui.css").read_text(encoding="utf-8"))
    assert len(old) > 150
    missing = [k for k in old if k not in base and k not in comp]
    assert not missing, missing


def test_declarations_survive_apart_from_var_fallbacks():
    def strip(d):
        return re.sub(r"var\((--hub-[a-z0-9-]+), [^)]*\)", lambda m: f"var({m.group(1)})", d)

    old = rules(OLD.read_text(encoding="utf-8"))
    both = {}
    for name in ("hub-ui-base.css", "hub-ui.css"):
        both.update(rules((STATIC / name).read_text(encoding="utf-8")))
    for key, decl in old.items():
        if key[1] == ":root":
            continue          # tokens moved to the base file; components carry fallbacks instead
        assert strip(both[key]) == decl, key


def test_components_file_only_styles_package_classes():
    comp = rules((STATIC / "hub-ui.css").read_text(encoding="utf-8"))
    own = re.compile(
        r"\.(hub-|sc-|gt-|num$)|^#(order-wizard|print-dialog|signature-studio|signature-editor|ow-note)"
    )
    stray = [k for k in comp if not k[0].startswith("@keyframes") and not own.search(k[1])]
    assert not stray, stray
    # Nothing in it may define a token: it falls back to its own defaults.
    assert (":root" not in {k[1] for k in comp}) and "html" not in {k[1] for k in comp}


def test_components_use_token_fallbacks():
    text = (STATIC / "hub-ui.css").read_text(encoding="utf-8")
    assert not re.search(r"var\(--hub-[a-z0-9-]+\)", text), "every --hub-* var needs a fallback"


def test_base_file_carries_the_opinionated_look():
    base = rules((STATIC / "hub-ui-base.css").read_text(encoding="utf-8"))
    sels = {k[1] for k in base}
    for s in (":root", "html", "body", ".navbar", ".nav-link", ".btn-primary", ".card", ".dropdown-menu"):
        assert s in sels, s
