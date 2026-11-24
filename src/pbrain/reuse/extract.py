"""Extract symbols from source code for the reuse index (Python, JS files and <script> blocks).

Each symbol yields two things: text to search by PURPOSE (split name, docstring/comments,
identifiers in the body) and a normalised token sequence (variable names and literal values
removed) used to detect duplicate bodies. Pure parsing: no LLM, no database.
"""

from __future__ import annotations

import ast
import hashlib
import re

from ..text import words

PY_COMMENT = re.compile(r"#\s*(.+)$", re.M)
PY_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)

SCRIPT_BLOCK = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
JS_COMMENT = re.compile(r"/\*.*?\*/|(?:^|(?<=\s))//[^\n]*", re.S | re.M)
JS_LEXEME = re.compile(
    r"""[A-Za-z_$][\w$]*|\d[\w.]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|[^\s\w]"""
)
JS_KEYWORDS = frozenset(
    "async await break case catch class const continue default delete do else export extends false "
    "finally for function if import in instanceof let new null of return static super switch this "
    "throw true try typeof undefined var void while yield".split()
)
JS_FUNC = re.compile(
    r"""^(?P<indent>[ \t]*)(?:
        (?:async\s+)?function\s*\*?\s*(?P<func>[A-Za-z_$][\w$]*)\s*\(
        | (?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?
            (?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)
        | (?:async\s+)?(?P<method>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$
    )""",
    re.X,
)
JS_OWNER = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:const|let|var)\s+|window\.)"
    r"(?P<owner>[A-Z][\w$]*)\s*=\s*[^;]*[{(]\s*$"
)
JS_MAX_LINES = 400

__all__ = ["extract_symbols", "words"]


def extract_symbols(relative_path: str, source: str) -> list[dict]:
    """Symbols of one file chosen by its suffix; unsupported suffixes give an empty list."""
    if relative_path.endswith(".py"):
        return _python_symbols(source)
    if relative_path.endswith(".js"):
        return _js_symbols(source, 0)
    if relative_path.endswith(".html"):
        symbols = []
        for block in SCRIPT_BLOCK.finditer(source):
            symbols.extend(_js_symbols(block.group(1), source.count("\n", 0, block.start(1))))
        return symbols
    return []


def _symbol(name, kind, start, end, doc, text, tokens, top, source):
    """Standard index record: accent-free search text plus a fingerprint of the body."""
    norm = " ".join(tokens)
    return {
        "name": name,
        "kind": kind,
        "start": start,
        "end": end,
        "doc": " ".join((doc or "").split()),
        "text": " ".join(dict.fromkeys(words(text))),
        "norm": norm,
        "size": len(tokens),
        "top": int(top),
        "fp": hashlib.sha1(norm.encode()).hexdigest()[:16] if tokens else "",
        "src_hash": hashlib.sha1(source.encode()).hexdigest()[:12],
    }


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _python_symbols(source):
    """Module-level functions/classes and one level of methods (nested functions are skipped)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    symbols = []

    def add(node, owner=""):
        segment = "\n".join(lines[node.lineno - 1 : node.end_lineno])
        doc = ast.get_docstring(node) or ""
        body = node.body[1:] if doc else node.body
        if isinstance(node, ast.ClassDef):
            # Only member names for a class: stuffing the whole body in would make every
            # class match every query.
            tokens = []
            text = " ".join(getattr(child, "name", "") for child in body)
        else:
            tokens = [token for stmt in body for token in _py_tokens(stmt)]
            text = " ".join(_py_identifiers(node)) + " " + " ".join(PY_COMMENT.findall(segment))
        symbols.append(
            _symbol(
                f"{owner}.{node.name}" if owner else node.name,
                "py",
                node.lineno,
                node.end_lineno,
                doc.split("\n\n")[0],
                text,
                tokens,
                not owner and not isinstance(node, ast.ClassDef),
                segment,
            )
        )

    for node in tree.body:
        if isinstance(node, PY_FUNC):
            add(node)
        elif isinstance(node, ast.ClassDef):
            add(node)
            for child in node.body:
                if isinstance(child, PY_FUNC):
                    add(child, node.name)
    return symbols


def _py_identifiers(node):
    """Variable, attribute and argument names plus short strings: material for word search."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            yield child.id
        elif isinstance(child, ast.Attribute):
            yield child.attr
        elif isinstance(child, ast.arg):
            yield child.arg
        elif (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and len(child.value) <= 60
        ):
            yield child.value


def _py_tokens(node):
    """Walk the tree in source order, keep attribute/called names, drop variables."""
    label = type(node).__name__
    if isinstance(node, ast.Attribute):
        label += "." + node.attr
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        label += ":" + node.func.id
    elif isinstance(node, ast.Constant):
        label += ":" + type(node.value).__name__
    yield label
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, ast.expr_context):
            yield from _py_tokens(child)


# ---------------------------------------------------------------------------
# JavaScript (.js files and <script> blocks in templates)
# ---------------------------------------------------------------------------


def _js_symbols(source, line_offset):
    """Named functions of a JS block: ``function f()``, ``const f = () =>``, object-literal methods.

    Detected with regexes plus brace counting (no full parser): enough for lookup and duplicates.
    """
    lines = source.split("\n")
    symbols = []
    owner, owner_indent = "", -1
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        if owner and line.strip() and indent <= owner_indent:
            owner, owner_indent = "", -1
        owner_match = JS_OWNER.match(line)
        if owner_match:
            owner, owner_indent = owner_match.group("owner"), indent
        match = JS_FUNC.match(line)
        if not match:
            continue
        name = match.group("func") or match.group("var") or match.group("method")
        if name in JS_KEYWORDS:
            continue
        is_method = bool(match.group("method"))
        if is_method and not owner:
            continue
        end = _js_block_end(lines, index)
        body = "\n".join(lines[index : end + 1])
        symbols.append(
            _symbol(
                f"{owner}.{name}" if is_method else name,
                "js",
                line_offset + index + 1,
                line_offset + end + 1,
                _js_leading_comment(lines, index),
                body,
                _js_tokens(body),
                not is_method,
                body,
            )
        )
    return symbols


def _js_block_end(lines, start):
    """Index of the line closing the ``{...}`` block opened at ``start`` (``start`` if none)."""
    if "{" not in lines[start]:
        return start
    depth, opened = 0, False
    last = min(len(lines), start + JS_MAX_LINES)
    for index in range(start, last):
        for char in JS_COMMENT.sub(" ", lines[index]):
            if char == "{":
                depth, opened = depth + 1, True
            elif char == "}":
                depth -= 1
        if opened and depth <= 0:
            return index
    return last - 1


def _js_leading_comment(lines, start):
    """Comment lines directly above a JS function, used as its description."""
    comment = []
    for line in reversed(lines[max(0, start - 8) : start]):
        stripped = line.strip()
        if not stripped.startswith(("//", "*", "/*")):
            break
        comment.append(stripped.strip("/* "))
    return " ".join(reversed(comment))


def _js_tokens(body):
    """Normalised JS tokens: keep keywords and property names; names N, strings S, numbers 0."""
    tokens, previous = [], ""
    for token in JS_LEXEME.findall(JS_COMMENT.sub(" ", body)):
        first = token[0]
        if first in "\"'`":
            tokens.append("S")
        elif first.isdigit():
            tokens.append("0")
        elif first.isalpha() or first in "_$":
            tokens.append(token if token in JS_KEYWORDS or previous == "." else "N")
        else:
            tokens.append(token)
        previous = token
    return tokens
