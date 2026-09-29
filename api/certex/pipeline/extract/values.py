"""Turning what is printed into what is stored.

"14-03-2019", "14/03/2019", "۱۴ مارچ ۲۰۱۹" and "14 March 2019" are one date, and a CSV
of all four is not a dataset anyone can use. Each field kind therefore has one canonical
written form, produced here.

Two rules govern all of it:

* **Read, do not invent.** A value that cannot be understood is kept exactly as printed
  rather than dropped or guessed at. The row then shows a reviewer the real text, and
  the validation stage flags it.
* **Say when a reading is ambiguous.** In Pakistan dates are written day first, so
  "03-04-2019" is the third of April. When the same digits would also be a valid date
  read the other way round, that is reported, because a wrong date in a birth record is
  worth a person's second look.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

import dateparser

from certex.enums import Sex
from certex.fields import FieldKind
from certex.pipeline.text.normalize import normalize_text

__all__ = [
    "DateReading",
    "format_cnic",
    "normalize_field_value",
    "read_date",
    "read_sex",
    "read_time",
    "to_western_digits",
]

_DATE_SETTINGS: Final[dict[str, object]] = {
    # Pakistan writes day first. Without this, "03-04-2019" silently becomes March.
    "DATE_ORDER": "DMY",
    "PREFER_DAY_OF_MONTH": "first",
    "REQUIRE_PARTS": ["day", "month", "year"],
    "RETURN_AS_TIMEZONE_AWARE": False,
    "PARSERS": ["absolute-time"],
}
_DATE_LANGUAGES: Final[list[str]] = ["en", "ur"]

_NUMERIC_DATE: Final = re.compile(r"^\s*(\d{1,4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,4})\s*$")
_TIME: Final = re.compile(
    # "04:25", "4.25 pm", and - because OCR loses a colon on a speckled page, and forms
    # print it this way anyway - a bare "0425".
    r"(?P<hour>\d{1,2})\s*[:.]\s*(?P<minute>\d{2})\s*(?P<meridiem>am|pm|a\.m\.|p\.m\.)?"
    r"|(?P<bare_hour>[01]\d|2[0-3])(?P<bare_minute>[0-5]\d)\b",
    re.IGNORECASE,
)
_CNIC_DIGITS: Final = re.compile(r"\d")
_CNIC_SHAPE: Final = re.compile(r"^\d{5}-\d{7}-\d$")
_NUMBER: Final = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_MALE_WORDS: Final = frozenset({"m", "male", "boy", "man", "مرد", "لڑکا", "نر"})
_FEMALE_WORDS: Final = frozenset({"f", "female", "girl", "woman", "عورت", "لڑکی", "خاتون", "مادہ"})

_EASTERN_DIGITS: Final[dict[int, str]] = {
    # Arabic-Indic and extended Arabic-Indic digits, as printed on Urdu forms.
    **{0x0660 + value: str(value) for value in range(10)},
    **{0x06F0 + value: str(value) for value in range(10)},
}


@dataclass(frozen=True, slots=True)
class DateReading:
    """A date as read, and how sure that reading is."""

    iso: str | None
    ambiguous: bool = False
    """The same digits are a valid date read month-first as well as day-first."""

    impossible: bool = False
    """It looks like a date and is not one: the 31st of February."""


def to_western_digits(text: str) -> str:
    """Fold Urdu and Arabic-Indic digits to 0-9, leaving everything else alone."""
    return text.translate(_EASTERN_DIGITS)


def read_date(raw: str, *, today: dt.date | None = None) -> DateReading:
    """Read a printed date into ISO 8601, saying whether the reading is ambiguous."""
    text = to_western_digits(normalize_text(raw)).strip()
    if not text:
        return DateReading(iso=None)

    numeric = _NUMERIC_DATE.match(text)
    if numeric is not None:
        first, second, third = (int(part) for part in numeric.groups())
        return _read_numeric_date(first, second, third, today=today)

    parsed = dateparser.parse(text, languages=_DATE_LANGUAGES, settings=_DATE_SETTINGS)  # type: ignore[arg-type]
    if parsed is None:
        return DateReading(iso=None)
    return DateReading(iso=parsed.date().isoformat())


def _valid(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _read_numeric_date(
    first: int, second: int, third: int, *, today: dt.date | None = None
) -> DateReading:
    """All-numeric dates, where the order is a convention rather than a fact."""
    del today
    if first > 31:  # 2019-03-14: year first is unambiguous
        date = _valid(first, second, third)
        return DateReading(iso=date.isoformat()) if date else DateReading(iso=None, impossible=True)

    day_first = _valid(third, second, first)
    month_first = _valid(third, first, second)
    if day_first is None and month_first is None:
        return DateReading(iso=None, impossible=True)
    if day_first is None:
        # Only one reading is a real date, so there is nothing ambiguous about it.
        assert month_first is not None
        return DateReading(iso=month_first.isoformat())
    return DateReading(
        iso=day_first.isoformat(),
        ambiguous=month_first is not None and first != second,
    )


def read_time(raw: str) -> str | None:
    """A printed time as 24-hour HH:MM."""
    text = to_western_digits(normalize_text(raw))
    match = _TIME.search(text)
    if match is None:
        return None
    if match.group("hour") is None:
        return f"{int(match.group('bare_hour')):02d}:{int(match.group('bare_minute')):02d}"
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    meridiem = (match.group("meridiem") or "").lower().replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def read_sex(raw: str) -> str | None:
    """Canonical sex, from the words English and Urdu forms print."""
    text = normalize_text(raw).strip().lower().strip(".")
    if not text:
        return None
    for word in (text, *text.split()):
        if word in _MALE_WORDS:
            return Sex.MALE.value
        if word in _FEMALE_WORDS:
            return Sex.FEMALE.value
    return None


def format_cnic(raw: str) -> str | None:
    """A Pakistani identity number as 12345-1234567-1, when the digits allow it."""
    text = to_western_digits(normalize_text(raw))
    if _CNIC_SHAPE.match(text.strip()):
        return text.strip()
    digits = "".join(_CNIC_DIGITS.findall(text))
    if len(digits) != 13:
        return None
    return f"{digits[:5]}-{digits[5:12]}-{digits[12]}"


def _read_number(raw: str) -> str | None:
    """The quantity out of "PKR 500,000" or "73 years"."""
    text = to_western_digits(normalize_text(raw))
    match = _NUMBER.search(text)
    if match is None:
        return None
    return match.group(0).replace(",", "")


def normalize_field_value(raw: str, kind: FieldKind) -> tuple[str, DateReading | None]:
    """The canonical written form of a value, with the date reading when it is one.

    Anything that cannot be read is returned as printed: a reviewer can correct a value
    they can see, and can do nothing with one this stage has thrown away.
    """
    text = normalize_text(raw).strip()
    if not text:
        return "", None

    if kind is FieldKind.DATE:
        reading = read_date(text)
        return (reading.iso or text), reading
    if kind is FieldKind.TIME:
        return read_time(text) or text, None
    if kind is FieldKind.SEX:
        return read_sex(text) or text, None
    if kind is FieldKind.ID_NUMBER:
        return format_cnic(text) or text, None
    if kind is FieldKind.NUMBER:
        return _read_number(text) or text, None
    return text, None
