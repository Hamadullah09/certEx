"""Search and match keys for registry entries.

A certificate number is printed differently by every office that issues one:
``REG/LHR/2019/88213``, ``REG-LHR-2019-88213``, ``reg lhr 2019 88213``, and in Urdu
with Eastern digits. A name is printed with different spacing, different
transliteration and sometimes an honorific. None of that changes which certificate
or which person is meant, so the register stores two forms of each: the value as
printed, which is what a person reads and what an export contains, and a key, which
is what an index and a duplicate check compare.

The keys here are deliberately aggressive. Their job is to bring candidates
together so a person can decide, not to decide anything themselves - a collision
raises a question, and nothing is ever merged on the strength of a key alone.
"""

from __future__ import annotations

import re
from typing import Final

from certex.pipeline.extract.values import to_western_digits
from certex.pipeline.text.normalize import normalize_text, word_tokens

__all__ = [
    "MAX_KEY_LENGTH",
    "name_key",
    "name_tokens",
    "number_key",
    "shares_a_name",
]

MAX_KEY_LENGTH: Final = 200
"""Keys are indexed, so they are bounded. Nothing legitimate comes close."""

_NON_ALNUM: Final = re.compile(r"[^0-9a-z؀-ۿݐ-ݿ]+")
"""Everything that is not a digit, a Latin letter or an Arabic-script letter."""

_HONORIFICS: Final[frozenset[str]] = frozenset(
    {
        "mr",
        "mrs",
        "miss",
        "ms",
        "dr",
        "prof",
        "hafiz",
        "hafiza",
        "syed",
        "syeda",
        "sheikh",
        "mian",
        "chaudhry",
        "ch",
        "malik",
        "raja",
        "sardar",
        "haji",
        "hajji",
        "alhaj",
        "begum",
        "sahib",
        "sahiba",
        "late",
        "mohtarma",
        "janab",
        "محترم",
        "محترمہ",
        "جناب",
        "سید",
        "سیدہ",
        "حاجی",
        "بیگم",
        "مرحوم",
        "مرحومہ",
    }
)
"""Titles, not names.

Dropped from the key so "Mr Ahmed Ali" and "Ahmed Ali" match. They are kept in the
printed value, because that is what the certificate says. Names that are also
common surnames here - Malik, Raja, Sheikh - are dropped only when they sit at the
front of a name, where they are being used as a title.
"""


def number_key(raw: str | None) -> str:
    """The comparison key for a certificate or registration number.

    Case, separators and digit script are all discarded: ``REG/LHR/2019/88213``,
    ``reg-lhr-2019-88213`` and the same number written with Urdu digits produce one
    key. What remains is the sequence of letters and digits, which is what two
    copies of one certificate always agree on.
    """
    if not raw:
        return ""
    folded = to_western_digits(normalize_text(raw)).casefold()
    return _NON_ALNUM.sub("", folded)[:MAX_KEY_LENGTH]


def name_tokens(raw: str | None) -> tuple[str, ...]:
    """A name as its meaningful words, in order, without titles.

    Keeping the words rather than one string is what makes a partial match
    possible: a search for "Ahmed Ali" should find "Muhammad Ahmed Ali", and a
    reviewer looking at two entries needs to see which words they share.
    """
    if not raw:
        return ()
    folded = to_western_digits(normalize_text(raw)).casefold()
    tokens = [token for token in word_tokens(folded) if token]

    # A title only counts as a title at the front of a name. "Malik" in "Tariq
    # Mahmood Malik" is the family name and has to stay.
    while tokens and tokens[0] in _HONORIFICS:
        tokens.pop(0)
    return tuple(tokens)


def name_key(raw: str | None) -> str:
    """The comparison key for a person's name.

    Words are joined by single spaces, so the key stays readable in a query plan
    and a trigram index over it behaves as a person would expect: a spelling that
    differs by one letter stays close.
    """
    return " ".join(name_tokens(raw))[:MAX_KEY_LENGTH]


def shares_a_name(left: str | None, right: str | None) -> bool:
    """Whether two printed names could be the same person's.

    True when one name's words are all present in the other's - which covers a
    middle name written on one certificate and omitted on another, the single
    commonest reason two entries for one person look different. Deliberately not a
    fuzzy score: this answers "worth asking about", and the asking is a person's
    job.
    """
    first, second = set(name_tokens(left)), set(name_tokens(right))
    if not first or not second:
        return False
    return first <= second or second <= first
