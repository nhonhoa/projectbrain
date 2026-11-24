"""Small text helpers shared by every pbrain module (standard library only)."""

from __future__ import annotations

import re
import unicodedata

WORD = re.compile(r"[^\W_]+")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def remove_accents(text: str | None) -> str:
    """Lowercase ``text``, strip diacritics (``d`` for the stroked d) and drop punctuation.

    Only ``a-z``, ``0-9`` and spaces survive: ``"Tiếng Việt!"`` becomes ``"tieng viet"``.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = stripped.replace("đ", "d").replace("Đ", "D").lower()
    return re.sub(r"[^a-z0-9 ]", "", lowered)


def words(text: str | None) -> list[str]:
    """Split ``text`` into lowercase, accent-free words: ``parseDateNow_x`` -> parse date now x."""
    out = []
    for chunk in WORD.findall(text or ""):
        for part in CAMEL.split(chunk):
            word = remove_accents(part)
            if word:
                out.append(word)
    return out
