"""Is a page's native text layer good enough, or does the page need OCR?

A PDF can carry a text layer that is worse than none: an invisible layer from a
bad scanner OCR, a font whose encoding maps glyphs to nonsense, or a lone "Page 1
of 1" stamped onto an otherwise image-only scan. Trusting such a layer would yield
an empty or garbled row with no error. Three signals are computed per page:

* **usable characters** - letters and digits, in any script.
* **alphanumeric ratio** - usable characters over all visible characters, which
  falls when a broken encoding produces symbol soup.
* **dictionary hit rate** - the share of word tokens that are real words in
  English or Urdu (or are numbers), which falls when the "text" is gibberish.

The page is marked NEEDS_OCR when usable characters fall below
``TEXT_QUALITY_MIN_CHARS`` or the hit rate falls below
``TEXT_QUALITY_MIN_DICT_RATIO``.

The dictionaries are the word lists shipped inside Tesseract's own eng and urd
language data (Apache-2.0), normalised into ``certex/resources``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Final

from certex.pipeline.text.normalize import is_arabic_script, normalize_word, word_tokens

__all__ = ["TextQuality", "assess_text", "dictionary_words"]

_WORDLISTS: Final = ("wordlist_en.txt", "wordlist_ur.txt")
_REASON_FEW_CHARS: Final = "too_few_chars"
_REASON_LOW_DICTIONARY: Final = "low_dictionary_hits"


@dataclass(frozen=True, slots=True)
class TextQuality:
    char_count: int
    alnum_ratio: float
    dict_hit_rate: float
    token_count: int
    needs_ocr: bool
    reason: str | None
    """Machine-readable cause when ``needs_ocr`` is true."""


@lru_cache(maxsize=1)
def dictionary_words() -> frozenset[str]:
    """English and Urdu words, lower-cased and NFKC-normalised. Loaded once per process."""
    words: set[str] = set()
    package = resources.files("certex.resources")
    for name in _WORDLISTS:
        text = package.joinpath(name).read_text(encoding="utf-8")
        words.update(normalize_word(line).lower() for line in text.splitlines() if line)
    words.discard("")
    return frozenset(words)


def _mixes_scripts(token: str) -> bool:
    """Whether a token draws on both the Arabic and the Latin script.

    Nothing written in English or Urdu does. OCR of a noisy page does it constantly -
    an English word comes back with an Urdu letter or an Arabic-Indic digit inside it -
    so this is the clearest signal that a "word" is an artefact.
    """
    arabic = any(is_arabic_script(char) for char in token if char.isalnum())
    latin = any(char.isascii() and char.isalnum() for char in token)
    return arabic and latin


def _is_hit(token: str, dictionary: frozenset[str]) -> bool:
    if _mixes_scripts(token):
        return False
    if token.isdigit():
        # Registration numbers, dates and ids are legitimate content, not noise.
        return True
    if any(char.isdigit() for char in token):
        # Mixed tokens ("BC2019", "UC42") read as identifiers when short enough.
        return len(token) <= 20
    return token.lower() in dictionary


def assess_text(text: str, *, min_chars: int, min_dict_ratio: float) -> TextQuality:
    visible = [char for char in text if not char.isspace()]
    usable = sum(1 for char in visible if char.isalnum())
    alnum_ratio = usable / len(visible) if visible else 0.0

    dictionary = dictionary_words()
    candidates = [
        token
        for token in (normalize_word(raw) for raw in word_tokens(text))
        if len(token) >= 2 and any(char.isalnum() for char in token)
    ]
    hits = sum(1 for token in candidates if _is_hit(token, dictionary))
    hit_rate = hits / len(candidates) if candidates else 0.0

    reason: str | None = None
    if usable < min_chars:
        reason = _REASON_FEW_CHARS
    elif hit_rate < min_dict_ratio:
        reason = _REASON_LOW_DICTIONARY

    return TextQuality(
        char_count=usable,
        alnum_ratio=round(alnum_ratio, 4),
        dict_hit_rate=round(hit_rate, 4),
        token_count=len(candidates),
        needs_ocr=reason is not None,
        reason=reason,
    )
