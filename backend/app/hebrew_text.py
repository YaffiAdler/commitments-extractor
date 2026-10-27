"""Hebrew text folding for comparing two readings of a name.

Used to compare, never to store: a field's ``value`` and ``raw_value`` are
never folded. Two levels:

* ``strip_marks`` — the same letters as written: Unicode compatibility forms
  decomposed (presentation forms such as U+FB2A become letter + point),
  niqqud and cantillation (combining marks), invisible format characters
  (bidi marks, zero-width spaces) and quote marks (ASCII, geresh,
  gershayim, typographic) removed, whitespace collapsed and trimmed. Final
  letters stay distinct, so a final/regular mix-up is still visible.
* ``comparable_name`` — ``strip_marks`` plus final letters unified with
  their regular forms (ך→כ, ם→מ, ן→נ, ף→פ, ץ→צ).
"""
from __future__ import annotations

import re
import unicodedata

FINAL_LETTERS = "ךםןףץ"
REGULAR_LETTERS = "כמנפצ"
# ASCII ' " `, geresh, gershayim, typographic single/double quotes, primes.
QUOTE_MARKS = "'\"`׳״‘’‚‛“”„‟′″"
# Unicode categories removed: combining marks (niqqud, cantillation) and format characters (bidi marks).
_DROPPED_CATEGORIES = frozenset({"Mn", "Cf"})
_COMPATIBILITY_FORM = "NFKD"
_SPACES = re.compile(r"\s+")
_UNIFY_FINALS = str.maketrans(FINAL_LETTERS, REGULAR_LETTERS)
_DROP_QUOTES = str.maketrans("", "", QUOTE_MARKS)


def strip_marks(text: str | None) -> str:
    """``text`` without niqqud, invisible characters and quotes, whitespace collapsed ("" for None)."""
    decomposed = unicodedata.normalize(_COMPATIBILITY_FORM, text or "")
    letters = "".join(ch for ch in decomposed if unicodedata.category(ch) not in _DROPPED_CATEGORIES)
    return _SPACES.sub(" ", letters.translate(_DROP_QUOTES)).strip()


def unify_final_letters(text: str) -> str:
    return text.translate(_UNIFY_FINALS)


def comparable_name(text: str | None) -> str:
    """``strip_marks`` with final letters unified: equal results = the same name as read."""
    return unify_final_letters(strip_marks(text))
