"""A certificate schema: the ordered set of fields one batch was read under.

This is the seam that makes the pipeline schema-agnostic. Every stage that used
to ask ``fields_for(certificate_type)`` now takes a :class:`FieldSchema`, and it
no longer matters whether that schema came from the built-in definitions or from
a version an operator built in the UI.

Two things a schema knows that a bare list of fields does not:

* **which version it is** - a certificate records the exact version it was read
  under, so a later edit cannot silently change what an old record means;
* **roles** - which field is the certificate number, which is the subject's name,
  which dates are which. Generic code applies semantic rules through roles, so
  none of it names a field.

A role may appear more than once. A marriage certificate has two parties and two
dates of birth, and a rule that assumed one of each would quietly skip half of
them - so every role lookup returns a tuple, and callers that genuinely want one
value ask for :meth:`first`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Final

from certex.enums import CertificateType, FieldRole
from certex.fields.specs import FieldSpec, fields_for

__all__ = [
    "BUILTIN_VERSION",
    "EVENT_DATE_ROLES",
    "PERSON_NAME_ROLES",
    "FieldSchema",
    "builtin_schema",
    "schema_or_builtin",
]

BUILTIN_VERSION = 1
"""Version number given to the schemas shipped with the product."""

EVENT_DATE_ROLES: Final[tuple[FieldRole, ...]] = (
    FieldRole.DEATH_DATE,
    FieldRole.MARRIAGE_DATE,
    FieldRole.EVENT_DATE,
    FieldRole.BIRTH_DATE,
)
"""Which printed date a certificate is *about*, most specific first.

A death certificate also prints a date of birth, but it is the death that the
registration follows and the death that the entry should be found by. Read in this
order, one rule covers every type - including one an operator invented, as long as
its dates carry roles.
"""

PERSON_NAME_ROLES: Final[tuple[FieldRole, ...]] = (
    FieldRole.SUBJECT_NAME,
    FieldRole.PARTY_NAME,
)
"""Whose certificate this is: the subject, or the parties to the event.

Parents, spouses and witnesses are named on a certificate too, and are searchable,
but they are not who the entry is filed under.
"""


# Not slotted: the cached lookups below need a __dict__, and there is one schema
# per batch rather than one per row, so the memory is irrelevant.
@dataclass(frozen=True)
class FieldSchema:
    """An ordered, immutable set of fields, and what they mean."""

    fields: tuple[FieldSpec, ...]
    certificate_type: CertificateType = CertificateType.OTHER
    version: int = BUILTIN_VERSION
    version_id: uuid.UUID | None = None
    """The ``schema_versions`` row this came from, or None for a built-in."""

    name: str = "Built-in"

    # ------------------------------------------------------------------ basics
    def __iter__(self) -> Iterator[FieldSpec]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.by_name

    @cached_property
    def by_name(self) -> dict[str, FieldSpec]:
        return {spec.name: spec for spec in self.fields}

    @cached_property
    def names(self) -> tuple[str, ...]:
        """Field keys in export order."""
        return tuple(spec.name for spec in self.fields)

    def get(self, name: str) -> FieldSpec | None:
        return self.by_name.get(name)

    # ------------------------------------------------------------------- roles
    @cached_property
    def _by_role(self) -> dict[FieldRole, tuple[FieldSpec, ...]]:
        grouped: dict[FieldRole, list[FieldSpec]] = {}
        for spec in self.fields:
            if spec.role is not FieldRole.NONE:
                grouped.setdefault(spec.role, []).append(spec)
        return {role: tuple(specs) for role, specs in grouped.items()}

    def by_role(self, *roles: FieldRole) -> tuple[FieldSpec, ...]:
        """Every field carrying any of these roles, in schema order."""
        if len(roles) == 1:
            return self._by_role.get(roles[0], ())
        wanted = set(roles)
        return tuple(spec for spec in self.fields if spec.role in wanted)

    def first(self, *roles: FieldRole) -> FieldSpec | None:
        """The first field carrying any of these roles, or None."""
        found = self.by_role(*roles)
        return found[0] if found else None

    def names_for(self, *roles: FieldRole) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.by_role(*roles))

    @cached_property
    def identifier(self) -> FieldSpec | None:
        """The field holding the certificate number, if the schema declares one."""
        return self.first(FieldRole.IDENTIFIER)

    @cached_property
    def required_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if spec.required)

    @cached_property
    def searchable(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.fields if spec.searchable)

    @cached_property
    def unique_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields if spec.unique)

    # -------------------------------------------------------------- derivation
    def subset(self, names: Sequence[str]) -> FieldSchema:
        """A schema holding only the named fields, order preserved.

        Used by exports that a person narrowed to a few columns; the result still
        carries its roles, so a narrowed export is still a valid input elsewhere.
        """
        wanted = set(names)
        return FieldSchema(
            fields=tuple(spec for spec in self.fields if spec.name in wanted),
            certificate_type=self.certificate_type,
            version=self.version,
            version_id=self.version_id,
            name=self.name,
        )

    def describe(self) -> str:
        identifier = self.identifier.name if self.identifier else "none"
        return (
            f"{self.name} v{self.version} "
            f"({self.certificate_type.value}, {len(self.fields)} fields, id={identifier})"
        )


def builtin_schema(certificate_type: CertificateType) -> FieldSchema:
    """The schema shipped with the product for a certificate type.

    Used where no operator-defined schema applies: a batch created before schemas
    existed, and the classifier's own view of what a certificate of this kind
    normally contains.
    """
    return FieldSchema(
        fields=fields_for(certificate_type),
        certificate_type=certificate_type,
        version=BUILTIN_VERSION,
        version_id=None,
        name=f"Built-in {certificate_type.value.lower()}",
    )


def schema_or_builtin(certificate_type: CertificateType, schema: FieldSchema | None) -> FieldSchema:
    """The caller's schema, or the built-in one for this type.

    Every pipeline stage takes an optional schema. When a batch pins a version,
    that version is threaded through and the stage reads exactly the fields the
    operator defined. When nothing is pinned - a batch older than schemas, or a
    unit being re-read on its own - the built-in definition stands in, which is
    how the pipeline behaved before schemas existed.
    """
    return schema if schema is not None else builtin_schema(certificate_type)
