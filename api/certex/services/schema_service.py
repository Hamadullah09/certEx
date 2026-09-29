"""Certificate types and schema versions.

This is the provider behind :class:`certex.fields.FieldSchema`. Everything
downstream - extraction, validation, export, search - takes a ``FieldSchema`` and
does not care whether it was shipped with the product or built by an operator
five minutes ago.

Immutability
------------
A published version is never edited. Certificates record the version they were
read under, so changing one in place would silently rewrite the meaning of
records already in the registry. Editing a published schema creates the next
version instead, leaving the old one readable forever.

That immutability is also what makes caching safe: a published version's fields
cannot change, so they are resolved once per process and reused.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import selectinload

from certex.core.deps import WorkspaceScope
from certex.core.errors import (
    ConflictError,
    FieldError,
    NotFoundError,
    ValidationFailedError,
)
from certex.db.models import (
    Batch,
    CertificateSchema,
    CertificateTypeRecord,
    SchemaField,
    SchemaVersion,
)
from certex.enums import CertificateType, FieldRole, SchemaVersionStatus, UserRole
from certex.fields import FieldKind, FieldSchema, FieldSpec, builtin_schema, fields_for
from certex.logging_setup import get_logger

__all__ = [
    "SchemaDraftField",
    "create_schema",
    "create_version",
    "default_version_for_type_sync",
    "ensure_builtin_types",
    "ensure_builtin_types_sync",
    "publish_version",
    "resolve_schema",
    "resolve_schema_sync",
    "schema_for_batch",
    "schema_for_batch_sync",
    "type_record_for_sync",
    "validate_draft_fields",
]

logger = get_logger(__name__)

FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}[a-z0-9]$")
"""Field keys become CSV headers, JSON keys and query parameters, so they are
restricted to what is safe in all three: lowercase, digits and underscores."""

MAX_FIELDS_PER_SCHEMA = 200

# Published versions are immutable, so a resolved schema is valid for the life of
# the process. Drafts are never cached.
_RESOLVED: dict[uuid.UUID, FieldSchema] = {}


@dataclass(frozen=True, slots=True)
class SchemaDraftField:
    """One field as the schema builder submits it."""

    name: str
    label: str
    kind: FieldKind
    role: FieldRole = FieldRole.NONE
    required: bool = False
    searchable: bool = False
    unique: bool = False
    description: str | None = None
    labels_en: tuple[str, ...] = ()
    labels_ur: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_draft_fields(fields: list[SchemaDraftField]) -> None:
    """Reject a schema that cannot work, with a message naming the field.

    These are the rules that would otherwise fail much later and much less
    clearly - a CSV with two identical headers, or a registry with no way to tell
    one certificate from another.
    """
    problems: list[FieldError] = []

    if not fields:
        raise ValidationFailedError(
            "A schema needs at least one field.",
            remediation="Add the certificate number, at minimum.",
        )
    if len(fields) > MAX_FIELDS_PER_SCHEMA:
        raise ValidationFailedError(
            f"A schema may hold at most {MAX_FIELDS_PER_SCHEMA} fields.",
            remediation="Split rarely used values into a second schema.",
        )

    seen: dict[str, int] = {}
    for index, field in enumerate(fields):
        position = f"fields.{index}"

        if not FIELD_KEY_PATTERN.match(field.name):
            problems.append(
                FieldError(
                    field=f"{position}.name",
                    message=(
                        "Use lowercase letters, digits and underscores, starting with "
                        "a letter - this becomes the CSV column header."
                    ),
                    code="invalid_field_key",
                )
            )
        if field.name in seen:
            problems.append(
                FieldError(
                    field=f"{position}.name",
                    message=(
                        f"Duplicate field key; already used at position {seen[field.name] + 1}."
                    ),
                    code="duplicate_field_key",
                )
            )
        seen.setdefault(field.name, index)

        if not field.label.strip():
            problems.append(
                FieldError(field=f"{position}.label", message="Give the field a label.")
            )
        if field.searchable and field.role is FieldRole.NONE:
            # Search columns are role-mapped; a searchable field with no role has
            # nowhere to be indexed and would silently not be searchable.
            problems.append(
                FieldError(
                    field=f"{position}.role",
                    message="A searchable field needs a role, so the registry knows what it means.",
                    code="searchable_without_role",
                )
            )

    identifiers = [field for field in fields if field.role is FieldRole.IDENTIFIER]
    if len(identifiers) == 0:
        problems.append(
            FieldError(
                field="fields",
                message="Exactly one field must be the certificate number.",
                code="missing_identifier",
            )
        )
    elif len(identifiers) > 1:
        names = ", ".join(field.name for field in identifiers)
        problems.append(
            FieldError(
                field="fields",
                message=f"Only one field can be the certificate number; found {names}.",
                code="multiple_identifiers",
            )
        )

    if problems:
        raise ValidationFailedError(
            "The schema has problems that must be fixed before it can be saved.",
            errors=problems,
            remediation="Correct the highlighted fields and save again.",
        )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _to_spec(row: SchemaField) -> FieldSpec:
    return FieldSpec(
        name=row.name,
        label=row.label,
        kind=FieldKind(row.kind),
        required=row.required,
        labels_en=tuple(str(item) for item in row.labels_en),
        labels_ur=tuple(str(item) for item in row.labels_ur),
        description=row.description,
        role=row.role,
        searchable=row.searchable,
        unique=row.is_unique,
    )


async def resolve_schema(session: AsyncSession, version_id: uuid.UUID) -> FieldSchema:
    """Load a schema version as a :class:`FieldSchema`."""
    cached = _RESOLVED.get(version_id)
    if cached is not None:
        return cached

    version = await session.scalar(
        select(SchemaVersion)
        .where(SchemaVersion.id == version_id)
        .options(
            selectinload(SchemaVersion.fields),
            selectinload(SchemaVersion.schema).selectinload(CertificateSchema.certificate_type),
        )
    )
    if version is None:
        raise NotFoundError(
            "That schema version does not exist.",
            remediation="Pick a schema from the list and try again.",
        )

    certificate_type = version.schema.certificate_type.classifier_key or CertificateType.OTHER
    resolved = FieldSchema(
        fields=tuple(_to_spec(row) for row in version.fields),
        certificate_type=certificate_type,
        version=version.version,
        version_id=version.id,
        name=version.schema.name,
    )

    if version.status is SchemaVersionStatus.PUBLISHED:
        _RESOLVED[version_id] = resolved
    return resolved


async def schema_for_batch(session: AsyncSession, batch: Batch) -> FieldSchema:
    """The schema a batch was created under.

    A batch made before schemas existed has no pinned version; it falls back to
    the built-in definition for whatever the classifier decided, which is exactly
    how it behaved before.
    """
    if batch.schema_version_id is not None:
        return await resolve_schema(session, batch.schema_version_id)

    expected = batch.settings_json.get("expected_types") if batch.settings_json else None
    if isinstance(expected, list) and len(expected) == 1:
        try:
            return builtin_schema(CertificateType(str(expected[0])))
        except ValueError:  # pragma: no cover - defensive
            pass
    return builtin_schema(CertificateType.OTHER)


def forget_cached_schema(version_id: uuid.UUID) -> None:
    """Drop a cached resolution. Only needed when a draft is edited."""
    _RESOLVED.pop(version_id, None)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
BUILTIN_TYPES: tuple[tuple[CertificateType, str, str, int], ...] = (
    (CertificateType.BIRTH, "Birth", "Birth certificates and registrations.", 1),
    (CertificateType.MARRIAGE, "Marriage", "Marriage certificates and nikah nama.", 2),
    (CertificateType.DEATH, "Death", "Death certificates and registrations.", 3),
)


async def ensure_builtin_types(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[CertificateTypeRecord]:
    """Create the three standard types and their v1 schemas, once per workspace.

    Idempotent: re-running adds nothing. The fields come from the built-in
    definitions, so a workspace starts with a working schema it can copy and edit
    rather than a blank page.
    """
    existing = {
        record.key: record
        for record in (
            await session.scalars(
                select(CertificateTypeRecord).where(
                    CertificateTypeRecord.workspace_id == workspace_id
                )
            )
        ).all()
    }

    created: list[CertificateTypeRecord] = []
    for classifier_key, name, description, position in BUILTIN_TYPES:
        if classifier_key.value in existing:
            continue

        record = CertificateTypeRecord(
            workspace_id=workspace_id,
            key=classifier_key.value,
            name=name,
            description=description,
            classifier_key=classifier_key,
            position=position,
            is_active=True,
        )
        session.add(record)
        await session.flush()

        schema = CertificateSchema(
            workspace_id=workspace_id,
            certificate_type_id=record.id,
            name=f"{name} certificate",
            description=f"The fields a {name.lower()} certificate normally carries.",
            is_default=True,
        )
        session.add(schema)
        await session.flush()

        version = SchemaVersion(
            schema_id=schema.id,
            workspace_id=workspace_id,
            version=1,
            status=SchemaVersionStatus.PUBLISHED,
            published_at=dt.datetime.now(dt.UTC),
            notes="Shipped with the product.",
        )
        session.add(version)
        await session.flush()

        for position_index, spec in enumerate(fields_for(classifier_key)):
            session.add(_field_row(version.id, position_index, spec))

        created.append(record)

    if created:
        await session.flush()
        logger.info(
            "schema.builtins_seeded",
            workspace_id=str(workspace_id),
            count=len(created),
        )
    return created


def _field_row(version_id: uuid.UUID, position: int, spec: FieldSpec) -> SchemaField:
    return SchemaField(
        schema_version_id=version_id,
        position=position,
        name=spec.name,
        label=spec.label,
        kind=spec.kind.value,
        role=spec.role,
        required=spec.required,
        searchable=spec.searchable,
        is_unique=spec.unique,
        description=spec.description,
        labels_en=list(spec.labels_en),
        labels_ur=list(spec.labels_ur),
    )


async def create_schema(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_type_id: uuid.UUID,
    name: str,
    description: str | None = None,
) -> CertificateSchema:
    scope.require(UserRole.ADMIN)

    certificate_type = await session.scalar(
        select(CertificateTypeRecord).where(
            CertificateTypeRecord.id == certificate_type_id,
            CertificateTypeRecord.workspace_id == scope.workspace_id,
        )
    )
    if certificate_type is None:
        raise NotFoundError("That certificate type does not exist in this workspace.")

    clash = await session.scalar(
        select(func.count())
        .select_from(CertificateSchema)
        .where(
            CertificateSchema.certificate_type_id == certificate_type_id,
            CertificateSchema.name == name,
        )
    )
    if clash:
        raise ConflictError(
            f"A schema called {name!r} already exists for this certificate type.",
            remediation="Give the schema a different name, or add a version to the existing one.",
        )

    schema = CertificateSchema(
        workspace_id=scope.workspace_id,
        certificate_type_id=certificate_type_id,
        name=name,
        description=description,
        created_by=scope.user_id,
    )
    session.add(schema)
    await session.flush()
    logger.info("schema.created", workspace_id=str(scope.workspace_id), entity_id=str(schema.id))
    return schema


async def create_version(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    schema_id: uuid.UUID,
    fields: list[SchemaDraftField],
    notes: str | None = None,
    publish: bool = True,
) -> SchemaVersion:
    """Add the next version of a schema.

    Always additive: the previous version is left exactly as it was, so records
    read under it keep meaning what they meant.
    """
    scope.require(UserRole.ADMIN)
    validate_draft_fields(fields)

    schema = await session.scalar(
        select(CertificateSchema).where(
            CertificateSchema.id == schema_id,
            CertificateSchema.workspace_id == scope.workspace_id,
        )
    )
    if schema is None:
        raise NotFoundError("That schema does not exist in this workspace.")

    highest = await session.scalar(
        select(func.coalesce(func.max(SchemaVersion.version), 0)).where(
            SchemaVersion.schema_id == schema_id
        )
    )
    version = SchemaVersion(
        schema_id=schema_id,
        workspace_id=scope.workspace_id,
        version=int(highest or 0) + 1,
        status=SchemaVersionStatus.PUBLISHED if publish else SchemaVersionStatus.DRAFT,
        published_at=dt.datetime.now(dt.UTC) if publish else None,
        notes=notes,
        created_by=scope.user_id,
    )
    session.add(version)
    await session.flush()

    for position, field in enumerate(fields):
        session.add(
            SchemaField(
                schema_version_id=version.id,
                position=position,
                name=field.name,
                label=field.label,
                kind=field.kind.value,
                role=field.role,
                required=field.required,
                searchable=field.searchable,
                is_unique=field.unique,
                description=field.description,
                labels_en=list(field.labels_en),
                labels_ur=list(field.labels_ur),
            )
        )
    await session.flush()

    logger.info(
        "schema.version_created",
        entity_id=str(version.id),
        schema_version=str(version.version),
        count=len(fields),
    )
    return version


async def publish_version(
    session: AsyncSession, *, scope: WorkspaceScope, version_id: uuid.UUID
) -> SchemaVersion:
    scope.require(UserRole.ADMIN)
    version = await session.scalar(
        select(SchemaVersion).where(
            SchemaVersion.id == version_id,
            SchemaVersion.workspace_id == scope.workspace_id,
        )
    )
    if version is None:
        raise NotFoundError("That schema version does not exist in this workspace.")
    if version.status is SchemaVersionStatus.PUBLISHED:
        return version
    if version.status is SchemaVersionStatus.ARCHIVED:
        raise ConflictError(
            "An archived version cannot be published again.",
            remediation="Create a new version from it instead.",
        )

    version.status = SchemaVersionStatus.PUBLISHED
    version.published_at = dt.datetime.now(dt.UTC)
    await session.flush()
    forget_cached_schema(version_id)
    return version


async def list_types(
    session: AsyncSession, *, scope: WorkspaceScope, include_inactive: bool = False
) -> list[CertificateTypeRecord]:
    statement = select(CertificateTypeRecord).where(
        CertificateTypeRecord.workspace_id == scope.workspace_id
    )
    if not include_inactive:
        statement = statement.where(CertificateTypeRecord.is_active.is_(True))
    statement = statement.order_by(CertificateTypeRecord.position, CertificateTypeRecord.name)
    return list((await session.scalars(statement)).all())


async def default_version_for_type(
    session: AsyncSession, *, scope: WorkspaceScope, certificate_type_id: uuid.UUID
) -> SchemaVersion | None:
    """The newest published version of a type's default schema."""
    version: SchemaVersion | None = await session.scalar(
        select(SchemaVersion)
        .join(CertificateSchema, CertificateSchema.id == SchemaVersion.schema_id)
        .where(
            CertificateSchema.certificate_type_id == certificate_type_id,
            CertificateSchema.workspace_id == scope.workspace_id,
            SchemaVersion.status == SchemaVersionStatus.PUBLISHED,
        )
        .order_by(CertificateSchema.is_default.desc(), SchemaVersion.version.desc())
        .limit(1)
    )
    return version


# ---------------------------------------------------------------------------
# Synchronous resolution, for the Celery stages
# ---------------------------------------------------------------------------
def resolve_schema_sync(session: SyncSession, version_id: uuid.UUID) -> FieldSchema | None:
    """Load a schema version inside a worker's synchronous session.

    Shares the cache with the async path, since a published version is immutable
    either way. Returns None rather than raising: a stage that cannot find a
    schema falls back to the built-in definition and keeps processing, because a
    missing schema row must not strand a document mid-pipeline.
    """
    cached = _RESOLVED.get(version_id)
    if cached is not None:
        return cached

    version = session.scalar(
        select(SchemaVersion)
        .where(SchemaVersion.id == version_id)
        .options(
            selectinload(SchemaVersion.fields),
            selectinload(SchemaVersion.schema).selectinload(CertificateSchema.certificate_type),
        )
    )
    if version is None:
        logger.warning("schema.version_missing", entity_id=str(version_id))
        return None

    resolved = FieldSchema(
        fields=tuple(_to_spec(row) for row in version.fields),
        certificate_type=version.schema.certificate_type.classifier_key or CertificateType.OTHER,
        version=version.version,
        version_id=version.id,
        name=version.schema.name,
    )
    if version.status is SchemaVersionStatus.PUBLISHED:
        _RESOLVED[version_id] = resolved
    return resolved


def type_record_for_sync(
    session: SyncSession, *, workspace_id: uuid.UUID, classifier_key: CertificateType
) -> CertificateTypeRecord | None:
    """The registry type a classified certificate belongs to, if the workspace has one.

    Returns None rather than inventing a type. A document the classifier could only
    call OTHER has no place in the register yet - its extraction still exists and
    still exports - and a workspace that deliberately removed a type should not have
    it recreated behind the operator's back.
    """
    record: CertificateTypeRecord | None = session.scalar(
        select(CertificateTypeRecord).where(
            CertificateTypeRecord.workspace_id == workspace_id,
            CertificateTypeRecord.classifier_key == classifier_key,
            CertificateTypeRecord.is_active.is_(True),
        )
    )
    return record


def default_version_for_type_sync(
    session: SyncSession, *, workspace_id: uuid.UUID, certificate_type_id: uuid.UUID
) -> SchemaVersion | None:
    """The newest published version of a type's default schema, from a worker."""
    version: SchemaVersion | None = session.scalar(
        select(SchemaVersion)
        .join(CertificateSchema, CertificateSchema.id == SchemaVersion.schema_id)
        .where(
            CertificateSchema.certificate_type_id == certificate_type_id,
            CertificateSchema.workspace_id == workspace_id,
            SchemaVersion.status == SchemaVersionStatus.PUBLISHED,
        )
        .order_by(CertificateSchema.is_default.desc(), SchemaVersion.version.desc())
        .limit(1)
    )
    return version


def schema_for_batch_sync(session: SyncSession, batch_id: uuid.UUID) -> FieldSchema | None:
    """The pinned schema for a batch, or None when it pinned none."""
    version_id = session.scalar(select(Batch.schema_version_id).where(Batch.id == batch_id))
    if version_id is None:
        return None
    return resolve_schema_sync(session, version_id)


def ensure_builtin_types_sync(session: SyncSession, *, workspace_id: uuid.UUID) -> int:
    """Synchronous twin of :func:`ensure_builtin_types`, for the CLI and workers.

    Idempotent, so the seed command can run on every container start.
    """
    existing = {
        record.key
        for record in session.scalars(
            select(CertificateTypeRecord).where(CertificateTypeRecord.workspace_id == workspace_id)
        ).all()
    }

    created = 0
    for classifier_key, name, description, position in BUILTIN_TYPES:
        if classifier_key.value in existing:
            continue

        record = CertificateTypeRecord(
            workspace_id=workspace_id,
            key=classifier_key.value,
            name=name,
            description=description,
            classifier_key=classifier_key,
            position=position,
            is_active=True,
        )
        session.add(record)
        session.flush()

        schema = CertificateSchema(
            workspace_id=workspace_id,
            certificate_type_id=record.id,
            name=f"{name} certificate",
            description=f"The fields a {name.lower()} certificate normally carries.",
            is_default=True,
        )
        session.add(schema)
        session.flush()

        version = SchemaVersion(
            schema_id=schema.id,
            workspace_id=workspace_id,
            version=1,
            status=SchemaVersionStatus.PUBLISHED,
            published_at=dt.datetime.now(dt.UTC),
            notes="Shipped with the product.",
        )
        session.add(version)
        session.flush()

        for position_index, spec in enumerate(fields_for(classifier_key)):
            session.add(_field_row(version.id, position_index, spec))
        created += 1

    if created:
        session.flush()
    return created
