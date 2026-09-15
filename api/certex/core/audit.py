"""Audit trail writer.

Section 11 requires an entry for every upload, view, correction, export and
delete, recording who acted and from where. Section 11 equally forbids PII in
logs, and the audit trail is a log: metadata carries counts, ids and enum labels
only, never a field value.

:func:`record_audit` enforces that by filtering its metadata through the same
redaction policy as the logging pipeline before the row is written.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from certex.db.base import JSONDict
from certex.db.models import AuditLog
from certex.enums import AuditAction
from certex.logging_setup import SAFE_KEYS, get_logger

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request

__all__ = ["AuditContext", "record_audit", "record_audit_sync", "sanitise_metadata"]

logger = get_logger(__name__)

_MAX_METADATA_KEYS = 40
_MAX_STRING_LENGTH = 200


class AuditContext:
    """Who is acting and from where. Built once per request."""

    __slots__ = ("ip_address", "user_agent", "user_id", "workspace_id")

    def __init__(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.ip_address = ip_address
        self.user_agent = user_agent[:512] if user_agent else None

    @classmethod
    def from_request(
        cls,
        request: Request,
        *,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> AuditContext:
        return cls(
            workspace_id=workspace_id,
            user_id=user_id,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )


def client_ip(request: Request) -> str | None:
    """Best-effort client address.

    ``X-Forwarded-For`` is honoured only for its left-most entry, and only because
    this service is expected to sit behind a trusted reverse proxy in every
    deployment topology documented in the README.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate[:45]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:45]
    return request.client.host if request.client else None


def sanitise_metadata(metadata: JSONDict | None) -> JSONDict:
    """Strip anything that could carry document content out of audit metadata.

    Only allowlisted keys survive, and surviving strings are length-capped. A
    caller that tries to audit ``{"new_value": "Fatima Bibi"}`` gets an empty
    object and a warning rather than a PII row in the database.
    """
    if not metadata:
        return {}

    cleaned: JSONDict = {}
    dropped: list[str] = []
    for key, value in list(metadata.items())[:_MAX_METADATA_KEYS]:
        if key not in SAFE_KEYS:
            dropped.append(key)
            continue
        cleaned[key] = _cap(value)

    if dropped:
        logger.warning(
            "audit.metadata_keys_dropped",
            field_names=sorted(dropped),
            count=len(dropped),
        )
    return cleaned


def _cap(value: object) -> object:
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if isinstance(value, list):
        return [_cap(item) for item in value[:25]]
    if isinstance(value, dict):
        return {key: _cap(item) for key, item in list(value.items())[:25]}
    return value


def _build_row(
    action: AuditAction,
    context: AuditContext,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    metadata: JSONDict | None,
) -> AuditLog:
    return AuditLog(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_jsonb=sanitise_metadata(metadata),
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        created_at=dt.datetime.now(dt.UTC),
    )


async def record_audit(
    session: AsyncSession,
    action: AuditAction,
    context: AuditContext,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: JSONDict | None = None,
) -> None:
    """Append an audit row inside the caller's transaction.

    Deliberately not auto-committed: an audited action and its audit record commit
    together, so the trail cannot disagree with what actually happened.
    """
    session.add(_build_row(action, context, entity_type, entity_id, metadata))
    logger.info(
        "audit.recorded",
        action=action.value,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        workspace_id=str(context.workspace_id) if context.workspace_id else None,
        user_id=str(context.user_id) if context.user_id else None,
    )


def record_audit_sync(
    session: Session,
    action: AuditAction,
    context: AuditContext,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: JSONDict | None = None,
) -> None:
    """Synchronous counterpart used by Celery workers."""
    session.add(_build_row(action, context, entity_type, entity_id, metadata))
    logger.info(
        "audit.recorded",
        action=action.value,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        workspace_id=str(context.workspace_id) if context.workspace_id else None,
    )
