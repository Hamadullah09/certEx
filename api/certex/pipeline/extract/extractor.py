"""Reading one certificate's fields: the whole policy, with no database in sight.

The stage around this does the bookkeeping - loading pages, finding the office's
template, writing the row. What a certificate *says* is decided here, so it can be
measured directly against hand-labelled documents, which is what the golden accuracy
tests do.

Order of business: the template reads what it knows, the rules engine reads everything,
the merge decides between them, and each surviving value is rewritten in the canonical
form its field kind calls for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from certex.enums import CertificateType, ExtractionMethod
from certex.fields import FieldSchema, schema_or_builtin
from certex.pipeline.extract.candidates import Candidate
from certex.pipeline.extract.merge import merge_candidates
from certex.pipeline.extract.rules import UnitPage, extract_extra_fields, extract_with_rules
from certex.pipeline.extract.templates import apply_template
from certex.pipeline.extract.values import normalize_field_value
from certex.schemas.template import TemplateRules

__all__ = ["ExtractedFields", "extract_fields"]


@dataclass(frozen=True, slots=True)
class ExtractedFields:
    """Everything read off one certificate."""

    fields: dict[str, Candidate] = field(default_factory=dict)
    extras: dict[str, Candidate] = field(default_factory=dict)

    @property
    def template_used(self) -> bool:
        return any(
            candidate.method is ExtractionMethod.TEMPLATE for candidate in self.fields.values()
        )

    def value(self, name: str) -> str | None:
        candidate = self.fields.get(name)
        return candidate.value if candidate else None


def _normalise(candidates: dict[str, Candidate], schema: FieldSchema) -> dict[str, Candidate]:
    """Rewrite each value in the canonical form its field kind calls for."""
    specs = schema.by_name
    normalised: dict[str, Candidate] = {}
    for name, candidate in candidates.items():
        spec = specs.get(name)
        if spec is None:  # pragma: no cover - candidates come from these same specs
            normalised[name] = candidate
            continue
        value, _reading = normalize_field_value(candidate.value, spec.kind)
        normalised[name] = Candidate(
            field=candidate.field,
            value=value,
            confidence=candidate.confidence,
            method=candidate.method,
            source=candidate.source,
        )
    return normalised


def extract_fields(
    pages: Sequence[UnitPage],
    *,
    certificate_type: CertificateType,
    template_rules: TemplateRules | None = None,
    schema: FieldSchema | None = None,
) -> ExtractedFields:
    """Read every field of a certificate, and anything else it labels.

    ``schema`` is the version the batch was created under. Without one the
    built-in definition for the classified type is used instead.
    """
    active = schema_or_builtin(certificate_type, schema)
    template_layer = apply_template(pages, template_rules) if template_rules else {}
    rules_layer = extract_with_rules(pages, certificate_type=certificate_type, schema=active)
    merged = _normalise(merge_candidates(template_layer, rules_layer), active)
    return ExtractedFields(
        fields=merged,
        extras=extract_extra_fields(pages, certificate_type=certificate_type, schema=active),
    )
