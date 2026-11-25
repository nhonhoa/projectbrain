"""Vietnamese number formatting: 1.234.567,89 (period = thousands, comma = decimal).

Rounding rule (identical to ``HubFmt`` in hub-ui.js): round half away from zero, applied to the
decimal number as written (``1.005`` -> ``1,01``, ``2.5`` -> ``3``, ``-2.5`` -> ``-3``). Floats are
read through their shortest repr, never through their binary value. A result that rounds to zero
has no sign. Empty / ``None`` / non-numeric input -> ``""``.
"""

import re
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation

_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_MAX_EXP = 400  # exponents beyond this are treated as non-numeric (same limit in hub-ui.js)


def _to_decimal(value):
    """The decimal number as written, or None when it is empty / not a finite number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value)) if value == value and abs(value) != float("inf") else None
    if isinstance(value, Decimal):
        return value if value.is_finite() and abs(value.adjusted()) <= _MAX_EXP else None
    if isinstance(value, str):
        text = value.strip()
        if not _NUMBER_RE.fullmatch(text):
            return None
        exp = re.search(r"[eE]([+-]?\d+)$", text)
        if exp and abs(int(exp.group(1))) > _MAX_EXP:
            return None
        return Decimal(text)
    return None


def format_number_vn(value, decimal_places=0):
    """Format a number the Vietnamese way: 1.234.567,890 (half away from zero, see module doc)."""
    number = _to_decimal(value)
    if number is None:
        return ""
    places = max(0, int(decimal_places))
    context = Context(prec=len(number.as_tuple().digits) + _MAX_EXP + places + 10)
    try:
        rounded = number.quantize(Decimal((0, (1,), -places)), rounding=ROUND_HALF_UP, context=context)
    except InvalidOperation:
        return ""
    text = format(abs(rounded), "f")  # abs: no "-0" / "-0,00"
    sign = "-" if rounded < 0 else ""
    integer, _, fraction = text.partition(".")
    integer = re.sub(r"\B(?=(?:\d{3})+(?!\d))", ".", integer)
    return sign + integer + ("," + fraction if fraction else "")


def format_vnd(value):
    """Currency without decimals. Example: 1.500.000"""
    return format_number_vn(value, decimal_places=0)
