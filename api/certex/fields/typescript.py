"""Generating the frontend's copy of the field schema.

The review grid and the download bar need to know what fields exist, what to call them
and what kind of value each holds. Restating that in TypeScript by hand guarantees the
two drift: a field added in Python quietly never appears in the UI, and a field renamed
quietly shows a blank column.

So the TypeScript is generated from :mod:`certex.fields.specs`, and a test fails when
the checked-in file no longer matches. Only what the UI actually uses is emitted - the
label synonyms are a matching concern and stay on this side.

    python -m certex.cli generate-types
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from certex.enums import CertificateType
from certex.fields.specs import FIELD_SCHEMA_VERSION, FieldKind, common_fields, fields_for

__all__ = ["GENERATED_TYPES_PATH", "render_typescript", "write_typescript"]

GENERATED_TYPES_PATH: Final = Path("web/src/lib/schemas/fields.generated.ts")
"""Where the generated file lives, relative to the repository root."""

_HEADER: Final = (
    "// Generated from api/certex/fields/specs.py by "
    "`python -m certex.cli generate-types`.\n"
    "// Do not edit: a test fails when this file and the Python schema disagree.\n"
)


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _field_literal(name: str, label: str, kind: FieldKind, required: bool) -> str:
    return (
        f"  {{ name: {_quote(name)}, label: {_quote(label)}, "
        f"kind: {_quote(kind.value)}, required: {str(required).lower()} }},"
    )


def render_typescript() -> str:
    """The generated module, exactly as it should appear on disk."""
    kinds = " | ".join(_quote(kind.value) for kind in FieldKind)
    types = " | ".join(_quote(kind.value) for kind in CertificateType)

    lines: list[str] = [
        _HEADER,
        f"export const FIELD_SCHEMA_VERSION = {_quote(FIELD_SCHEMA_VERSION)};",
        "",
        f"export type FieldKind = {kinds};",
        "",
        f"export type CertificateType = {types};",
        "",
        "export interface FieldSpec {",
        "  /** Machine name: the JSON key and the CSV column header. */",
        "  readonly name: string;",
        "  /** What a person calls it, in the grid and in the download bar. */",
        "  readonly label: string;",
        "  readonly kind: FieldKind;",
        "  /** A row missing this field is incomplete. */",
        "  readonly required: boolean;",
        "}",
        "",
        "/** Fields every certificate has, in export order. */",
        "export const COMMON_FIELDS: readonly FieldSpec[] = [",
    ]
    lines.extend(
        _field_literal(spec.name, spec.label, spec.kind, spec.required) for spec in common_fields()
    )
    lines.extend(
        [
            "];",
            "",
            "/** Every field of every certificate type, common fields first, in export order. */",
            "export const FIELDS_BY_TYPE: Record<CertificateType, readonly FieldSpec[]> = {",
        ]
    )
    for certificate_type in CertificateType:
        lines.append(f"  {certificate_type.value}: [")
        lines.extend(
            "  " + _field_literal(spec.name, spec.label, spec.kind, spec.required)
            for spec in fields_for(certificate_type)
        )
        lines.append("  ],")
    lines.extend(
        [
            "};",
            "",
            "export function fieldsFor(certificateType: CertificateType): readonly FieldSpec[] {",
            "  return FIELDS_BY_TYPE[certificateType] ?? COMMON_FIELDS;",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def write_typescript(repository_root: Path) -> Path:
    """Write the generated module, and return where it landed."""
    target = repository_root / GENERATED_TYPES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_typescript(), encoding="utf-8", newline="\n")
    return target
