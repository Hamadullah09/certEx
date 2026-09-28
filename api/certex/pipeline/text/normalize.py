"""Text normalisation shared by every text source.

PDF text layers are messier than they look, and none of the mess is visible:

* **Presentation forms.** Word's PDF export stores Urdu as Arabic *presentation
  forms* - one code point per glyph shape - rather than the letters a person types.
* **Shared glyphs.** A font that draws two characters with one glyph cannot tell
  them apart again on extraction. Arial draws the hyphen and the soft hyphen alike,
  so "BC-2020-001122" can come back as "BC<soft hyphen>2020...". Urdu suffers the
  same way: Farsi yeh and Arabic yeh, heh goal and Arabic heh, keheh and Arabic
  kaf, tteh and rnoon share glyphs in common fonts, so a correctly printed Urdu
  name is extracted with look-alike Arabic letters that never equal what a person
  types.
* **Invisible characters.** No-break spaces, zero-width joiners and bidi controls.

This module folds all of it to one canonical form, applied identically to text read
from a document and to text typed by a person, so the two compare equal:

1. NFKC - presentation forms to base letters, ligatures to letters, no-break spaces
   to spaces;
2. Arabic look-alikes to the Urdu letters they stand for. The system reads English
   and Urdu, and these Arabic-only letters never appear in correctly written Urdu;
3. a soft hyphen *between two letters* is a hyphenation hint and is removed; any
   other soft hyphen - between digits, beside a digit, standing alone - can only be
   a drawn hyphen mis-extracted (a PDF text layer holds only what was drawn), so it
   becomes a hyphen and identifiers such as "BC-2020-001122" survive;
4. hyphen look-alikes (U+2010, U+2011, U+2012, U+2212) to the ASCII hyphen;
5. remaining invisible characters are dropped.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

__all__ = [
    "arabic_script_ratio",
    "contains_arabic_script",
    "detect_language",
    "is_arabic_script",
    "normalize_text",
    "normalize_word",
    "word_tokens",
]

_SOFT_HYPHEN: Final = chr(0x00AD)

_INVISIBLE: Final = frozenset(
    chr(code)
    for code in (
        0x200B,  # zero width space
        0x200C,  # zero width non-joiner
        0x200D,  # zero width joiner
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0x202A,  # left-to-right embedding
        0x202B,  # right-to-left embedding
        0x202C,  # pop directional formatting
        0x202D,  # left-to-right override
        0x202E,  # right-to-left override
        0x2066,  # left-to-right isolate
        0x2067,  # right-to-left isolate
        0x2068,  # first strong isolate
        0x2069,  # pop directional isolate
        0xFEFF,  # zero width no-break space / byte order mark
    )
)

_CANONICAL: Final = {
    ord(chr(0x064A)): chr(0x06CC),  # Arabic yeh -> Farsi yeh
    ord(chr(0x0649)): chr(0x06CC),  # alef maksura -> Farsi yeh
    ord(chr(0x0643)): chr(0x06A9),  # Arabic kaf -> keheh
    ord(chr(0x0647)): chr(0x06C1),  # Arabic heh -> heh goal
    ord(chr(0x0629)): chr(0x06C3),  # teh marbuta -> teh marbuta goal
    ord(chr(0x06BB)): chr(0x0679),  # rnoon (Sindhi) -> tteh
    ord(chr(0x2010)): "-",  # hyphen
    ord(chr(0x2011)): "-",  # non-breaking hyphen
    ord(chr(0x2012)): "-",  # figure dash
    ord(chr(0x2212)): "-",  # minus sign
}

_ARABIC_RANGES: Final = (
    (0x0600, 0x06FF),  # Arabic (includes Urdu letters and Extended Arabic-Indic digits)
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

_HYPHENATION_HINT: Final = re.compile(rf"(?<=[^\W\d_]){_SOFT_HYPHEN}(?=[^\W\d_])")
_WHITESPACE_RUN: Final = re.compile(r"\s+")
_TOKEN: Final = re.compile(r"\w+")


def is_arabic_script(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _ARABIC_RANGES)


def contains_arabic_script(text: str) -> bool:
    return any(is_arabic_script(char) for char in text)


def arabic_script_ratio(text: str) -> float:
    """Share of the letters in ``text`` that are Arabic-script (Urdu, Arabic, Persian)."""
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if is_arabic_script(char)) / len(letters)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_CANONICAL)
    folded = _HYPHENATION_HINT.sub("", folded).replace(_SOFT_HYPHEN, "-")
    return "".join(char for char in folded if char not in _INVISIBLE)


def normalize_word(text: str) -> str:
    """Canonicalise one token (see the module docstring), trimming outer whitespace."""
    return _fold(text).strip()


def normalize_text(text: str) -> str:
    """Canonicalise a run of text and collapse whitespace within each line.

    Newlines are preserved - they carry line structure - but runs of spaces and tabs
    within a line collapse to one space.
    """
    lines = [_WHITESPACE_RUN.sub(" ", line).strip() for line in _fold(text).splitlines()]
    return "\n".join(lines).strip()


def word_tokens(text: str) -> list[str]:
    """Alphanumeric tokens, script-agnostic: Latin, Urdu and digits alike."""
    return _TOKEN.findall(text)


def detect_language(text: str) -> str | None:
    """Tesseract-style language tag for a page: ``eng``, ``urd`` or ``eng+urd``.

    Decided by script, which is what matters downstream: a page that is mostly
    Arabic-script is Urdu for this system's purposes, and a bilingual certificate -
    English labels, Urdu names - is both.
    """
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 3:
        return None
    ratio = arabic_script_ratio(text)
    if ratio >= 0.85:
        return "urd"
    if ratio <= 0.05:
        return "eng"
    return "eng+urd"
