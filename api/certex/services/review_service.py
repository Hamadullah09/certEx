"""Acting on a register entry: approving it, correcting it, settling a duplicate.

Everything here is a person's decision, and every one of them is recorded. That is
not bureaucracy - a register is a historical record, and somebody may be holding a
certificate issued from what an entry used to say, so "it says this now" is never a
complete answer. Each change writes a revision holding the values as they stood
afterwards, who made it and why.

Three rules the service will not bend:

**A correction states its reason.** A changed value with no explanation is
indistinguishable from a mistake six months later.

**Approving is not correcting.** A reviewer accepting a reading as it stands leaves
the values alone and says so; the two show up differently in the history, because
they mean different things.

**Duplicates are resolved, not merged.** Confirming two entries are one certificate
marks the later one superseded and points it at the earlier. Nothing is deleted and
no values are combined: the office decides which entry is authoritative, and both
remain findable for anyone holding a copy of either.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.certificates.draft import CertificateDraft
from certex.core.deps import WorkspaceScope
from certex.core.errors import BadRequestError, ConflictError, ValidationFailedError
from certex.db.models import (
    Certificate,
    CertificateDate,
    CertificateName,
    CertificateRevision,
)
from certex.enums import (
    CertificateStatus,
    DuplicateStatus,
    RevisionAction,
    UserRole,
)
from certex.fields import FieldSchema
from certex.logging_setup import get_logger
from certex.services import certificate_service
from certex.services.certificate_service import record_revision

__all__ = [
    "MAX_NOTE_LENGTH",
    "ReviewCounts",
    "approve_certificate",
    "correct_certificate",
    "list_revisions",
    "resolve_duplicate",
    "review_counts",
    "void_certificate",
]

logger = get_logger(__name__)

MAX_NOTE_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class ReviewCounts:
    """What is waiting, for the badge on the navigation."""

    needs_review: int = 0
    suspected_duplicates: int = 0

    @property
    def total(self) -> int:
        """Not the sum: a duplicate also needs review, and counting it twice would
        promise a reviewer more work than exists."""
        return self.needs_review


async def approve_certificate(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_id: uuid.UUID,
    note: str | None = None,
) -> Certificate:
    """Accept an entry as it stands.

    Idempotent: approving an entry that needs no review changes nothing and adds no
    history, so a double-click does not litter the record.
    """
    scope.require(UserRole.OPERATOR)
    certificate = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    if not certificate.needs_review:
        return certificate

    if certificate.duplicate_status is DuplicateStatus.SUSPECTED:
        raise ConflictError(
            "This entry may repeat another one, and that has to be settled first.",
            remediation=(
                "Open the possible duplicate, decide whether they are the same "
                "certificate, and record that decision. Then approve this entry."
            ),
        )

    certificate.needs_review = False
    certificate.updated_by = scope.user_id
    certificate.record_version += 1
    record_revision(
        session,
        certificate,
        action=RevisionAction.APPROVED,
        note=note,
        actor_id=scope.user_id,
    )
    await session.flush()
    logger.info(
        "certificate.approved", entity_id=str(certificate.id), workspace_id=str(scope.workspace_id)
    )
    return certificate


async def correct_certificate(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_id: uuid.UUID,
    schema: FieldSchema,
    values: Mapping[str, str],
    note: str,
    approve: bool = False,
) -> Certificate:
    """Change what an entry says, keeping what it said.

    ``values`` is a patch, not a replacement: only the named fields change, because a
    screen that submits the whole record would silently blank a field a second reviewer
    filled in from another tab. An empty string clears a field, which is different from
    not mentioning it.
    """
    scope.require(UserRole.OPERATOR)
    cleaned_note = note.strip()
    if not cleaned_note:
        raise ValidationFailedError(
            "A correction needs a reason.",
            remediation=(
                "Say what was wrong and where the right value came from - the scan, "
                "the paper register, the person at the counter."
            ),
        )
    if len(cleaned_note) > MAX_NOTE_LENGTH:
        raise BadRequestError(f"The reason may be at most {MAX_NOTE_LENGTH} characters.")

    certificate = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    if certificate.status is CertificateStatus.SUPERSEDED:
        raise ConflictError(
            "This entry has been superseded, so correcting it would change a record "
            "nobody relies on any more.",
            remediation="Open the entry that replaced it and correct that instead.",
        )

    unknown = sorted(name for name in values if name not in schema)
    if unknown:
        raise ValidationFailedError(
            f"The schema has no field called {unknown[0]!r}.",
            remediation="Use the field names from the schema this entry was read under.",
        )

    merged = certificate_service.text_values(certificate.values_jsonb)
    changed: list[str] = []
    for name, raw in values.items():
        replacement = raw.strip() or None
        if merged.get(name) != replacement:
            changed.append(name)
            merged[name] = replacement

    if not changed:
        return certificate

    draft = certificate_service.build_write(schema, merged)
    await _rewrite(session, certificate, schema=schema, values=merged)

    certificate.record_version += 1
    certificate.updated_by = scope.user_id
    if approve:
        certificate.needs_review = False
    record_revision(
        session,
        certificate,
        action=RevisionAction.CORRECTED,
        changed_fields=changed,
        note=cleaned_note,
        actor_id=scope.user_id,
    )
    await session.flush()

    # The number may have been what was wrong, so what this entry appears to repeat
    # can change with the correction.
    await _refresh_duplicate_state(session, certificate, draft=draft)
    await session.flush()

    logger.info(
        "certificate.corrected",
        entity_id=str(certificate.id),
        workspace_id=str(scope.workspace_id),
        # Field names, never values: the values are somebody's personal data.
        count=len(changed),
        field_names=sorted(changed),
        renumbered=draft.certificate_number_key != certificate.certificate_number_key,
    )
    return certificate


async def _rewrite(
    session: AsyncSession,
    certificate: Certificate,
    *,
    schema: FieldSchema,
    values: Mapping[str, str | None],
) -> None:
    """Re-derive every indexed column and side row from the corrected values.

    A correction that left the keys alone would leave an entry findable only by what
    it used to say, which is the one thing worse than not finding it.
    """
    draft = certificate_service.build_write(schema, values)

    certificate.certificate_number = draft.certificate_number
    certificate.certificate_number_key = draft.certificate_number_key
    certificate.registration_number = draft.registration_number
    certificate.registration_number_key = draft.registration_number_key
    certificate.primary_name = draft.primary_name
    certificate.primary_name_key = draft.primary_name_key
    certificate.secondary_name = draft.secondary_name
    certificate.secondary_name_key = draft.secondary_name_key
    certificate.event_date = draft.event_date
    certificate.event_date_role = draft.event_date_role
    certificate.registration_date = draft.registration_date
    certificate.issue_date = draft.issue_date
    certificate.issuing_authority = draft.issuing_authority
    certificate.values_jsonb = dict(draft.values)

    # Replaced rather than reconciled: a handful of rows, and a diff would be more
    # code and more ways to leave a stale name behind.
    await session.execute(
        delete(CertificateName).where(CertificateName.certificate_id == certificate.id)
    )
    await session.execute(
        delete(CertificateDate).where(CertificateDate.certificate_id == certificate.id)
    )
    for entry in draft.names:
        session.add(
            CertificateName(
                certificate_id=certificate.id,
                workspace_id=certificate.workspace_id,
                role=entry.role,
                field_name=entry.field_name,
                position=entry.position,
                value=entry.value,
                value_key=entry.key,
            )
        )
    for date_entry in draft.dates:
        session.add(
            CertificateDate(
                certificate_id=certificate.id,
                workspace_id=certificate.workspace_id,
                role=date_entry.role,
                field_name=date_entry.field_name,
                position=date_entry.position,
                value=date_entry.value,
            )
        )


async def _refresh_duplicate_state(
    session: AsyncSession,
    certificate: Certificate,
    *,
    draft: CertificateDraft,
) -> None:
    """Recheck what this entry appears to repeat, without undoing any decision.

    A correction to a mistyped number is the commonest reason a duplicate appears or
    disappears. A decision a person has already recorded - confirmed or distinct -
    stands, because re-raising a settled question is how a review queue stops being
    read.
    """
    if certificate.duplicate_status in (DuplicateStatus.CONFIRMED, DuplicateStatus.DISTINCT):
        return

    matches = await certificate_service.find_duplicates(
        session,
        workspace_id=certificate.workspace_id,
        certificate_type_id=certificate.certificate_type_id,
        draft=draft,
        exclude_id=certificate.id,
    )
    strong = next((match for match in matches if match.is_strong), None)
    certificate.duplicate_status = DuplicateStatus.SUSPECTED if matches else DuplicateStatus.NONE
    certificate.duplicate_of_id = strong.certificate_id if strong else None
    if matches:
        certificate.needs_review = True


async def resolve_duplicate(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_id: uuid.UUID,
    other_id: uuid.UUID,
    same_certificate: bool,
    note: str | None = None,
) -> tuple[Certificate, Certificate]:
    """Settle whether two entries are one certificate or two.

    ``same_certificate`` confirms they are the same: the entry filed later is marked
    superseded and points at the earlier one, which stays authoritative. Nothing is
    deleted and no values are combined - the office may need to explain either entry to
    whoever is holding a copy of it.

    Otherwise they are recorded as distinct, so the same pair is never raised again.
    Returns both entries, earlier first.
    """
    scope.require(UserRole.OPERATOR)
    if certificate_id == other_id:
        raise BadRequestError("An entry cannot be a duplicate of itself.")

    first = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    second = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=other_id
    )
    earlier, later = (first, second) if first.created_at <= second.created_at else (second, first)

    if same_certificate:
        later.status = CertificateStatus.SUPERSEDED
        later.superseded_by_id = earlier.id
    # The suspicion is cleared either way: it has been answered, and leaving it set
    # would keep pointing a clerk at a question somebody already settled.
    later.duplicate_of_id = None
    earlier.duplicate_of_id = None

    decision = DuplicateStatus.CONFIRMED if same_certificate else DuplicateStatus.DISTINCT
    for entry in (earlier, later):
        entry.duplicate_status = decision
        entry.needs_review = False
        entry.updated_by = scope.user_id
        entry.record_version += 1
        record_revision(
            session,
            entry,
            action=(
                RevisionAction.SUPERSEDED
                if same_certificate and entry is later
                else RevisionAction.DUPLICATE_RESOLVED
            ),
            note=note,
            actor_id=scope.user_id,
        )

    await session.flush()
    logger.info(
        "certificate.duplicate_resolved",
        entity_id=str(earlier.id),
        related_id=str(later.id),
        workspace_id=str(scope.workspace_id),
        reason_code="same_certificate" if same_certificate else "distinct_certificates",
    )
    return earlier, later


async def void_certificate(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_id: uuid.UUID,
    note: str,
) -> Certificate:
    """Cancel an entry without removing it.

    A voided entry is still readable and still found by its number - somebody holding
    the certificate has to be told it was cancelled, which a missing record cannot do.
    Administrators only: this is the office withdrawing something it issued.
    """
    scope.require(UserRole.ADMIN)
    cleaned = note.strip()
    if not cleaned:
        raise ValidationFailedError(
            "Voiding an entry needs a reason.",
            remediation="Record why the office cancelled this certificate.",
        )

    certificate = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    if certificate.status is CertificateStatus.VOID:
        return certificate

    certificate.status = CertificateStatus.VOID
    certificate.needs_review = False
    certificate.updated_by = scope.user_id
    certificate.record_version += 1
    record_revision(
        session,
        certificate,
        action=RevisionAction.VOIDED,
        note=cleaned,
        actor_id=scope.user_id,
    )
    await session.flush()
    logger.info("certificate.voided", entity_id=str(certificate.id))
    return certificate


async def list_revisions(
    session: AsyncSession, *, workspace_id: uuid.UUID, certificate_id: uuid.UUID
) -> list[CertificateRevision]:
    """An entry's history, oldest first.

    Whole rather than paged: an entry accumulates a handful of revisions over its life,
    and a history split across pages is a history nobody reads.
    """
    await certificate_service.get_certificate(
        session, workspace_id=workspace_id, certificate_id=certificate_id
    )
    return list(
        (
            await session.scalars(
                select(CertificateRevision)
                .where(CertificateRevision.certificate_id == certificate_id)
                .order_by(CertificateRevision.record_version, CertificateRevision.created_at)
            )
        ).all()
    )


async def review_counts(session: AsyncSession, *, workspace_id: uuid.UUID) -> ReviewCounts:
    """How much is waiting, for the badge on the navigation.

    Two indexed counts rather than a scan: the duplicate index is partial, so the
    second one reads only the rows that have a question against them.
    """
    needs_review = await session.scalar(
        select(func.count())
        .select_from(Certificate)
        .where(
            Certificate.workspace_id == workspace_id,
            Certificate.needs_review.is_(True),
            Certificate.status != CertificateStatus.VOID,
        )
    )
    duplicates = await session.scalar(
        select(func.count())
        .select_from(Certificate)
        .where(
            Certificate.workspace_id == workspace_id,
            Certificate.duplicate_status == DuplicateStatus.SUSPECTED,
            Certificate.status != CertificateStatus.VOID,
        )
    )
    return ReviewCounts(
        needs_review=int(needs_review or 0), suspected_duplicates=int(duplicates or 0)
    )
