"""Registry entries: the certificates themselves, and how a read row becomes one.

The pipeline produces extractions - what one document said. This package turns
those into register entries, which is what the office actually keeps: one row per
certificate, found by its number and by the names on it, linked to the documents it
was read from.
"""

from certex.certificates.draft import (
    CertificateDraft,
    DateEntry,
    NameEntry,
    build_draft,
)
from certex.certificates.keys import name_key, name_tokens, number_key, shares_a_name

__all__ = [
    "CertificateDraft",
    "DateEntry",
    "NameEntry",
    "build_draft",
    "name_key",
    "name_tokens",
    "number_key",
    "shares_a_name",
]
