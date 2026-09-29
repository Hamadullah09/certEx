"""Certificate types and schema versions.

The routes behind the Birth / Marriage / Death navigation and the schema builder.
Reading is open to any signed-in member of the workspace; defining what a
certificate contains is an administrator's job, because a schema decides how
every record created under it is read.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, WorkspaceScopeDep
from certex.core.errors import ConflictError, NotFoundError
from certex.db.models import CertificateSchema, CertificateTypeRecord, SchemaVersion
from certex.enums import AuditAction, SchemaVersionStatus, UserRole
from certex.logging_setup import get_logger
from certex.schemas.registry import (
    CertificateTypeCreate,
    CertificateTypeSummary,
    FieldDefinition,
    SchemaCreate,
    SchemaSummary,
    SchemaVersionDetail,
    SchemaVersionSummary,
    VersionCreate,
)
from certex.services import schema_service
from certex.services.schema_service import SchemaDraftField

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(tags=["registry"])


def _to_draft(definition: FieldDefinition) -> SchemaDraftField:
    return SchemaDraftField(
        name=definition.name,
        label=definition.label,
        kind=definition.kind,
        role=definition.role,
        required=definition.required,
        searchable=definition.searchable,
        unique=definition.unique,
        description=definition.description,
        labels_en=tuple(definition.labels_en),
        labels_ur=tuple(definition.labels_ur),
    )


# ---------------------------------------------------------------------------
# Certificate types
# ---------------------------------------------------------------------------
@router.get(
    "/certificate-types",
    response_model=list[CertificateTypeSummary],
    summary="The certificate types this workspace holds",
)
async def list_certificate_types(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    include_inactive: bool = Query(default=False),
) -> list[CertificateTypeSummary]:
    """Drives the main navigation.

    Seeds the three standard types on first call, so a new workspace opens onto a
    working registry rather than an empty screen.
    """
    types = await schema_service.list_types(session, scope=scope, include_inactive=include_inactive)
    if not types:
        await schema_service.ensure_builtin_types(session, workspace_id=scope.workspace_id)
        await session.commit()
        types = await schema_service.list_types(
            session, scope=scope, include_inactive=include_inactive
        )
    return [CertificateTypeSummary.model_validate(record) for record in types]


@router.post(
    "/certificate-types",
    response_model=CertificateTypeSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Add a certificate type",
    responses={403: {"description": "Only administrators may add a type."}},
)
async def create_certificate_type(
    payload: CertificateTypeCreate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> CertificateTypeSummary:
    scope.require(UserRole.ADMIN)

    clash = await session.scalar(
        select(func.count())
        .select_from(CertificateTypeRecord)
        .where(
            CertificateTypeRecord.workspace_id == scope.workspace_id,
            CertificateTypeRecord.key == payload.key,
        )
    )
    if clash:
        raise ConflictError(
            f"A certificate type with the key {payload.key!r} already exists.",
            remediation="Choose a different key, or edit the existing type.",
        )

    record = CertificateTypeRecord(
        workspace_id=scope.workspace_id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        classifier_key=payload.classifier_key,
        position=payload.position,
        created_by=scope.user_id,
    )
    session.add(record)
    await session.flush()

    await record_audit(
        session,
        AuditAction.SETTINGS_UPDATED,
        audit,
        entity_type="certificate_type",
        entity_id=record.id,
    )
    await session.commit()
    return CertificateTypeSummary.model_validate(record)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
@router.get(
    "/schemas",
    response_model=list[SchemaSummary],
    summary="Schemas, optionally for one certificate type",
)
async def list_schemas(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    certificate_type_id: uuid.UUID | None = Query(default=None),
) -> list[SchemaSummary]:
    statement = select(CertificateSchema).where(
        CertificateSchema.workspace_id == scope.workspace_id
    )
    if certificate_type_id is not None:
        statement = statement.where(CertificateSchema.certificate_type_id == certificate_type_id)
    statement = statement.order_by(CertificateSchema.is_default.desc(), CertificateSchema.name)
    rows = list((await session.scalars(statement)).all())

    summaries: list[SchemaSummary] = []
    for row in rows:
        latest = await session.scalar(
            select(SchemaVersion)
            .where(
                SchemaVersion.schema_id == row.id,
                SchemaVersion.status == SchemaVersionStatus.PUBLISHED,
            )
            .order_by(SchemaVersion.version.desc())
            .limit(1)
        )
        summary = SchemaSummary.model_validate(row)
        summaries.append(
            summary.model_copy(
                update={
                    "latest_version": latest.version if latest else None,
                    "latest_version_id": latest.id if latest else None,
                }
            )
        )
    return summaries


@router.post(
    "/schemas",
    response_model=SchemaSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schema",
    responses={409: {"description": "A schema with that name already exists."}},
)
async def create_schema(
    payload: SchemaCreate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> SchemaSummary:
    schema = await schema_service.create_schema(
        session,
        scope=scope,
        certificate_type_id=payload.certificate_type_id,
        name=payload.name,
        description=payload.description,
    )
    await record_audit(
        session,
        AuditAction.SETTINGS_UPDATED,
        audit,
        entity_type="certificate_schema",
        entity_id=schema.id,
    )
    await session.commit()
    return SchemaSummary.model_validate(schema)


@router.get(
    "/schemas/{schema_id}/versions",
    response_model=list[SchemaVersionSummary],
    summary="Every version of a schema, newest first",
)
async def list_versions(
    schema_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> list[SchemaVersionSummary]:
    rows = list(
        (
            await session.scalars(
                select(SchemaVersion)
                .where(
                    SchemaVersion.schema_id == schema_id,
                    SchemaVersion.workspace_id == scope.workspace_id,
                )
                .order_by(SchemaVersion.version.desc())
            )
        ).all()
    )
    if not rows:
        raise NotFoundError("That schema has no versions, or does not exist here.")
    return [SchemaVersionSummary.model_validate(row) for row in rows]


@router.post(
    "/schemas/{schema_id}/versions",
    response_model=SchemaVersionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Add the next version of a schema",
    responses={
        403: {"description": "Only administrators may change a schema."},
        422: {"description": "The field definitions are not usable."},
    },
)
async def create_version(
    schema_id: uuid.UUID,
    payload: VersionCreate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> SchemaVersionDetail:
    """Always additive.

    The previous version is untouched, so every record already read under it keeps
    meaning exactly what it meant.
    """
    version = await schema_service.create_version(
        session,
        scope=scope,
        schema_id=schema_id,
        fields=[_to_draft(item) for item in payload.fields],
        notes=payload.notes,
        publish=payload.publish,
    )
    await record_audit(
        session,
        AuditAction.SETTINGS_UPDATED,
        audit,
        entity_type="schema_version",
        entity_id=version.id,
        metadata={"count": len(payload.fields), "schema_version": str(version.version)},
    )
    await session.commit()

    # Built from the summary rather than validated whole: the ORM row carries a
    # ``fields`` relationship of its own, and reading it here would both lazy-load
    # inside a finished request and describe the rows rather than the definitions.
    summary = SchemaVersionSummary.model_validate(version)
    return SchemaVersionDetail(**summary.model_dump(), fields=payload.fields)


@router.get(
    "/schema-versions/{version_id}",
    response_model=SchemaVersionDetail,
    summary="One schema version and its fields",
)
async def get_version(
    version_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> SchemaVersionDetail:
    version = await session.scalar(
        select(SchemaVersion).where(
            SchemaVersion.id == version_id,
            SchemaVersion.workspace_id == scope.workspace_id,
        )
    )
    if version is None:
        raise NotFoundError("That schema version does not exist in this workspace.")

    resolved = await schema_service.resolve_schema(session, version_id)
    summary = SchemaVersionSummary.model_validate(version)
    return SchemaVersionDetail(
        **summary.model_dump(),
        fields=[
            FieldDefinition(
                name=spec.name,
                label=spec.label,
                kind=spec.kind,
                role=spec.role,
                required=spec.required,
                searchable=spec.searchable,
                unique=spec.unique,
                description=spec.description,
                labels_en=list(spec.labels_en),
                labels_ur=list(spec.labels_ur),
            )
            for spec in resolved.fields
        ],
    )


@router.post(
    "/schema-versions/{version_id}/publish",
    response_model=SchemaVersionSummary,
    summary="Publish a draft version, freezing its fields",
)
async def publish_version(
    version_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> SchemaVersionSummary:
    version = await schema_service.publish_version(session, scope=scope, version_id=version_id)
    await record_audit(
        session,
        AuditAction.SETTINGS_UPDATED,
        audit,
        entity_type="schema_version",
        entity_id=version.id,
    )
    await session.commit()
    return SchemaVersionSummary.model_validate(version)
