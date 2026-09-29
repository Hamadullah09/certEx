"""The field schema: what a certificate of each kind contains.

This package is the single source of truth for field names, labels, kinds and the
label synonyms the rules engine looks for. The CSV columns, the review grid, the
validation rules and the generated TypeScript types all derive from it, so a field is
added in exactly one place.
"""

from certex.fields.schema import (
    BUILTIN_VERSION,
    EVENT_DATE_ROLES,
    PERSON_NAME_ROLES,
    FieldSchema,
    builtin_schema,
    schema_or_builtin,
)
from certex.fields.specs import (
    FIELD_SCHEMA_VERSION,
    FieldKind,
    FieldSpec,
    common_fields,
    field_names_for,
    fields_for,
    spec_for,
)

__all__ = [
    "BUILTIN_VERSION",
    "EVENT_DATE_ROLES",
    "FIELD_SCHEMA_VERSION",
    "PERSON_NAME_ROLES",
    "FieldKind",
    "FieldSchema",
    "FieldSpec",
    "builtin_schema",
    "common_fields",
    "field_names_for",
    "fields_for",
    "schema_or_builtin",
    "spec_for",
]
