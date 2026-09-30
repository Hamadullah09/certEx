"""Building the ask, and checking the answer against what was actually on the page.

The verification is the point of this module. A language model asked to read a
certificate will sometimes produce a value that is plausible, well-formed, and not
printed anywhere on the document - a father's name completed from a common pattern, a
date that fits the others. In a register that is worse than a blank field, because a
blank field is obviously missing and an invented one is not.

So every value the model returns is looked for in the text that was sent. A value that
cannot be found is still recorded, because a reviewer may recognise it as a correct
reading of something the text layer mangled - but it is flagged, and the flag is what
stops it being treated as read. Nothing invented is silently accepted.

The comparison is deliberately loose about form and strict about substance: case,
spacing and the difference between a slash and a dash are normalised away, because the
model reasonably rewrites "35201 1234567 1" as "35201-1234567-1". What it cannot do is
introduce words that were not there.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Final

from certex.enums import FieldRole
from certex.fields import FieldKind, FieldSchema, FieldSpec
from certex.pipeline.text.normalize import normalize_text

__all__ = [
    "SYSTEM_PROMPT",
    "VerifiedValue",
    "build_prompt",
    "build_tool_schema",
    "prompt_digest",
    "verify_values",
]

SYSTEM_PROMPT: Final = (
    "You read Pakistani civil certificates - birth, marriage, death and similar - and "
    "report the fields printed on them.\n"
    "\n"
    "Rules:\n"
    "- Report only what is printed. If a field is not on the certificate, omit it.\n"
    "- Copy values as they appear. Do not translate, expand abbreviations, correct "
    "spellings, or convert dates to another calendar or format.\n"
    "- Urdu values stay in Urdu. Do not transliterate them.\n"
    "- Never infer a value from the other fields. A father's name that is not printed "
    "is not a field you can work out.\n"
    "- If a value is partly illegible, report the part you can read."
)

_TOKEN: Final = re.compile(r"\w+", re.UNICODE)
_SEPARATORS: Final = re.compile(r"[\s/\\.,:;_-]+")

MIN_TOKEN_OVERLAP: Final = 1.0
"""Share of a value's words that must appear in the source for it to count as read.

All of them. A value with one word the document does not contain is a value with one
word somebody invented, and in a register that is the whole problem.
"""


@dataclass(frozen=True, slots=True)
class VerifiedValue:
    """One returned value, and whether the document actually says it."""

    field: str
    value: str
    verified: bool
    """False means the value could not be found in what was sent. Kept, but flagged."""


def _hint(spec: FieldSpec) -> str:
    """What to tell the model about one field, in its own terms."""
    parts: list[str] = [spec.label]
    if spec.labels_en or spec.labels_ur:
        printed = ", ".join([*spec.labels_en[:4], *spec.labels_ur[:2]])
        parts.append(f"printed as: {printed}")
    if spec.kind is FieldKind.DATE:
        parts.append("a date, copied exactly as printed")
    elif spec.kind is FieldKind.ID_NUMBER:
        parts.append("an identity number as printed")
    elif spec.kind is FieldKind.NAME:
        parts.append("a person's name as printed")
    if spec.role is FieldRole.IDENTIFIER:
        parts.append("the certificate's own number")
    return " - ".join(parts)


def build_tool_schema(schema: FieldSchema, wanted: list[str]) -> dict[str, Any]:
    """JSON Schema for the tool call: one string property per field being asked for.

    Nothing is required. A required field invites a model to fill it in, which is
    exactly the failure this layer has to avoid.
    """
    properties: dict[str, Any] = {}
    for name in wanted:
        spec = schema.get(name)
        if spec is None:
            continue
        properties[name] = {"type": "string", "description": _hint(spec)}
    return {"type": "object", "properties": properties, "additionalProperties": False}


def build_prompt(source_text: str, wanted: list[str], *, max_chars: int) -> str:
    """The user turn: the certificate as read, and what is still missing from it."""
    text = source_text.strip()
    if len(text) > max_chars:
        # Truncated from the end: a certificate's fields are near the top, and the
        # tail of a bad text layer is usually the worst of it.
        text = f"{text[:max_chars]}\n[...truncated]"
    listed = "\n".join(f"- {name}" for name in wanted)
    return (
        "This is the text read from one certificate. The reading is imperfect: it may "
        "have joined words, dropped spaces, or mangled Urdu.\n"
        "\n"
        "--- certificate text ---\n"
        f"{text}\n"
        "--- end ---\n"
        "\n"
        "Earlier layers could not find these fields. Report any of them that the text "
        "above shows, and omit the rest:\n"
        f"{listed}"
    )


def prompt_digest(model: str, request_text: str, tool_schema: dict[str, Any]) -> str:
    """Cache key for one ask.

    Covers the model and the tool schema as well as the text: the same certificate asked
    for different fields, or by a different model, is a different question and must not
    be served an older answer.
    """
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(request_text.encode("utf-8"))
    digest.update(b"\0")
    for name in sorted(tool_schema.get("properties", {})):
        digest.update(name.encode("utf-8"))
        digest.update(b",")
    return digest.hexdigest()


def _searchable(text: str) -> str:
    """The form both sides are compared in: folded, and free of separators."""
    return _SEPARATORS.sub(" ", normalize_text(text).casefold())


def verify_values(
    values: dict[str, str], *, source_text: str
) -> tuple[dict[str, VerifiedValue], list[str]]:
    """Check each value against the source. Returns (verified map, unverified names).

    A value counts as read when every one of its words appears in the source. That
    allows the rewriting a model legitimately does - punctuation, spacing, the shape of
    an identity number - and refuses the thing it must not do, which is add a word the
    document does not contain.
    """
    haystack = _searchable(source_text)
    haystack_tokens = set(_TOKEN.findall(haystack))

    checked: dict[str, VerifiedValue] = {}
    unverified: list[str] = []
    for name, value in values.items():
        needle = _searchable(value)
        tokens = _TOKEN.findall(needle)
        if not tokens:
            continue

        # A contiguous match is the common case and the cheapest check.
        found = needle in haystack or all(token in haystack_tokens for token in tokens)
        checked[name] = VerifiedValue(field=name, value=value, verified=found)
        if not found:
            unverified.append(name)
    return checked, unverified
