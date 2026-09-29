"""Writing and reading register entries.

Everything that creates a certificate goes through here: the pipeline when a
document finishes, a CSV import, an operator typing a record in. Three rules hold
for all of them.

**Nothing is overwritten.** A certificate number that already exists produces a
second entry linked to the first and marked as a suspected duplicate. Deciding they
are the same record is a person's judgement, and the register keeps both until
somebody makes it.

**Nobody is merged by name.** Two people genuinely share a name, and two spellings
of one name look like two people. Names bring candidates together for review; they
never join records.

**The entry carries its own provenance.** An entry can outlive the batch it was
read from, so the method, the page and the verbatim snippet behind each value are
copied onto it rather than left behind in the extraction.

The module is written twice over, async for the API and synchronous for the Celery
stages, because a worker has no event loop. The query construction and every
decision are shared; only the execution differs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import selectinload

from certex.certificates.draft import CertificateDraft, build_draft
from certex.core.errors import ConflictError, NotFoundError, ValidationFailedError
from certex.db.models import (
    Certificate,
    CertificateDate,
    CertificateDocument,
    CertificateName,
    CertificateRevision,
    Document,
)
from certex.enums import (
    CertificateSource,
    CertificateStatus,
    DocumentLinkKind,
    DuplicateStatus,
    FieldRole,
    RevisionAction,
)
from certex.fields import FieldSchema
from certex.logging_setup import get_logger
from certex.schemas.common import Cursor
from certex.schemas.workspace import ReviewSettings

__all__ = [
    "MAX_DUPLICATE_CANDIDATES",
    "CertificateWrite",
    "DuplicateMatch",
    "build_write",
    "find_duplicates",
    "find_duplicates_sync",
    "get_certificate",
    "link_document",
    "link_document_sync",
    "record_certificate",
    "record_certificate_sync",
    "role_values",
]

logger = get_logger(__name__)

MAX_DUPLICATE_CANDIDATES: Final = 20
"""Enough for a reviewer to judge by. A number that matches hundreds of entries is
a data problem to investigate, not a list to page through."""

SAME_NUMBER: Final = "same_number"
SAME_NUMBER_OTHER_TYPE: Final = "same_number_other_type"
SAME_NAME_AND_EVENT_DATE: Final = "same_name_and_event_date"


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """An existing entry that the one being written might repeat."""

    certificate_id: uuid.UUID
    certificate_number: str
    reason: str
    primary_name: str | None = None
    event_date: dt.date | None = None
    same_type: bool = True

    @property
    def is_strong(self) -> bool:
        """Whether this is the match a new entry should be filed against.

        The certificate number is the identifier the office issues, so a match on
        it within the same type is the one worth pointing at. A name-and-date match
        is worth showing a reviewer and not worth linking on its own.
        """
        return self.reason == SAME_NUMBER


@dataclass(frozen=True, slots=True)
class CertificateWrite:
    """A validated entry, with whatever it appears to duplicate.

    Separating this from the write itself is what lets the API answer "this already
    exists" before creating anything, and lets the pipeline record the entry anyway
    with the question attached.
    """

    draft: CertificateDraft
    duplicates: tuple[DuplicateMatch, ...] = ()

    @property
    def strong_duplicate(self) -> DuplicateMatch | None:
        return next((match for match in self.duplicates if match.is_strong), None)


# ---------------------------------------------------------------------------
# Preparing a write
# ---------------------------------------------------------------------------
def build_write(
    schema: FieldSchema,
    values: Mapping[str, str | None],
    *,
    today: dt.date | None = None,
) -> CertificateDraft:
    """Read a row into a draft, refusing one the register cannot file.

    An entry with no certificate number has nothing to be found by and nothing to
    be de-duplicated against, which makes it unreachable the moment the batch that
    produced it is out of sight. That is worth refusing loudly rather than storing
    quietly.
    """
    draft = build_draft(schema, values, today=today)
    if not draft.has_identifier:
        identifier = schema.identifier
        raise ValidationFailedError(
            "The certificate number is missing, so this record cannot be filed.",
            remediation=(
                f"Fill in {identifier.label.lower()} and save again."
                if identifier
                else "Add an identifier field to the schema, then save again."
            ),
        )
    return draft


def text_values(stored: Mapping[str, object]) -> dict[str, str | None]:
    """A stored JSONB field map narrowed to text.

    Field values are text by the time they reach the register - a date is its ISO
    form, a number its digits - so anything that came back as another JSON type is
    treated as absent rather than coerced into a string nobody wrote.
    """
    return {name: value if isinstance(value, str) else None for name, value in stored.items()}


def _number_matches(
    *,
    workspace_id: uuid.UUID,
    draft: CertificateDraft,
    exclude_id: uuid.UUID | None,
) -> Select[tuple[Certificate]]:
    """Entries sharing this certificate number, or this registration number.

    A registration number match counts: the same entry is quoted by its register
    entry number in one office and by the certificate number in another.
    """
    conditions = [Certificate.certificate_number_key == draft.certificate_number_key]
    if draft.registration_number_key:
        conditions.append(Certificate.registration_number_key == draft.registration_number_key)
        conditions.append(
            Certificate.certificate_number_key == draft.registration_number_key,
        )

    statement = select(Certificate).where(
        Certificate.workspace_id == workspace_id,
        Certificate.status != CertificateStatus.VOID,
        or_(*conditions),
    )
    if exclude_id is not None:
        statement = statement.where(Certificate.id != exclude_id)
    return statement.order_by(Certificate.created_at).limit(MAX_DUPLICATE_CANDIDATES)


def _person_matches(
    *,
    workspace_id: uuid.UUID,
    draft: CertificateDraft,
    certificate_type_id: uuid.UUID,
    exclude_id: uuid.UUID | None,
) -> Select[tuple[Certificate]] | None:
    """Entries for the same name and the same event date.

    The case this catches is one certificate entered twice with the number
    mistyped: everything else agrees, so the numbers disagreeing is the error. It is
    restricted to one certificate type and requires an exact event date, because
    without both it would return every namesake in the register.
    """
    if not draft.primary_name_key or draft.event_date is None:
        return None
    statement = select(Certificate).where(
        Certificate.workspace_id == workspace_id,
        Certificate.certificate_type_id == certificate_type_id,
        Certificate.status != CertificateStatus.VOID,
        Certificate.primary_name_key == draft.primary_name_key,
        Certificate.event_date == draft.event_date,
        Certificate.certificate_number_key != draft.certificate_number_key,
    )
    if exclude_id is not None:
        statement = statement.where(Certificate.id != exclude_id)
    return statement.order_by(Certificate.created_at).limit(MAX_DUPLICATE_CANDIDATES)


def _as_matches(
    rows: Sequence[Certificate], *, certificate_type_id: uuid.UUID, reason: str
) -> list[DuplicateMatch]:
    return [
        DuplicateMatch(
            certificate_id=row.id,
            certificate_number=row.certificate_number,
            reason=(
                reason if row.certificate_type_id == certificate_type_id else SAME_NUMBER_OTHER_TYPE
            ),
            primary_name=row.primary_name,
            event_date=row.event_date,
            same_type=row.certificate_type_id == certificate_type_id,
        )
        for row in rows
    ]


def _combine(
    by_number: Sequence[Certificate],
    by_person: Sequence[Certificate],
    *,
    certificate_type_id: uuid.UUID,
) -> tuple[DuplicateMatch, ...]:
    matches = _as_matches(by_number, certificate_type_id=certificate_type_id, reason=SAME_NUMBER)
    seen = {match.certificate_id for match in matches}
    matches.extend(
        match
        for match in _as_matches(
            by_person, certificate_type_id=certificate_type_id, reason=SAME_NAME_AND_EVENT_DATE
        )
        if match.certificate_id not in seen
    )
    # Strongest first: a reviewer should see the number collision before the
    # circumstantial one.
    matches.sort(key=lambda match: (not match.is_strong, not match.same_type))
    return tuple(matches[:MAX_DUPLICATE_CANDIDATES])


async def find_duplicates(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    draft: CertificateDraft,
    exclude_id: uuid.UUID | None = None,
) -> tuple[DuplicateMatch, ...]:
    """Existing entries that this draft might repeat, strongest first."""
    by_number = list(
        (
            await session.scalars(
                _number_matches(workspace_id=workspace_id, draft=draft, exclude_id=exclude_id)
            )
        ).all()
    )
    person_statement = _person_matches(
        workspace_id=workspace_id,
        draft=draft,
        certificate_type_id=certificate_type_id,
        exclude_id=exclude_id,
    )
    by_person = (
        list((await session.scalars(person_statement)).all())
        if person_statement is not None
        else []
    )
    return _combine(by_number, by_person, certificate_type_id=certificate_type_id)


def find_duplicates_sync(
    session: SyncSession,
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    draft: CertificateDraft,
    exclude_id: uuid.UUID | None = None,
) -> tuple[DuplicateMatch, ...]:
    by_number = list(
        session.scalars(
            _number_matches(workspace_id=workspace_id, draft=draft, exclude_id=exclude_id)
        ).all()
    )
    person_statement = _person_matches(
        workspace_id=workspace_id,
        draft=draft,
        certificate_type_id=certificate_type_id,
        exclude_id=exclude_id,
    )
    by_person = (
        list(session.scalars(person_statement).all()) if person_statement is not None else []
    )
    return _combine(by_number, by_person, certificate_type_id=certificate_type_id)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _new_certificate(
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    schema: FieldSchema,
    draft: CertificateDraft,
    duplicates: tuple[DuplicateMatch, ...],
    source: CertificateSource,
    confidences: Mapping[str, float] | None,
    provenance: Mapping[str, object] | None,
    row_confidence: float,
    needs_review: bool,
    source_extraction_id: uuid.UUID | None,
    source_batch_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    review: ReviewSettings,
) -> Certificate:
    """The entry row itself, with its duplicate question already attached."""
    strong = next((match for match in duplicates if match.is_strong), None)
    # Whether a duplicate is worth a reviewer's time is the office's call. Turning it
    # off does not merge anything: the duplicate is still recorded and still shown on
    # both entries, it simply does not queue work.
    queue_for_duplicate = bool(duplicates) and review.review_suspected_duplicates
    return Certificate(
        workspace_id=workspace_id,
        certificate_type_id=certificate_type_id,
        schema_version_id=schema.version_id,
        certificate_number=draft.certificate_number,
        certificate_number_key=draft.certificate_number_key,
        registration_number=draft.registration_number,
        registration_number_key=draft.registration_number_key,
        primary_name=draft.primary_name,
        primary_name_key=draft.primary_name_key,
        secondary_name=draft.secondary_name,
        secondary_name_key=draft.secondary_name_key,
        event_date=draft.event_date,
        event_date_role=draft.event_date_role,
        registration_date=draft.registration_date,
        issue_date=draft.issue_date,
        issuing_authority=draft.issuing_authority,
        values_jsonb=dict(draft.values),
        confidences_jsonb=dict(confidences or {}),
        provenance_jsonb=dict(provenance or {}),
        row_confidence=row_confidence,
        status=CertificateStatus.ACTIVE,
        # A duplicate is a question, and a question belongs in the review queue
        # whatever the confidence of the reading was.
        needs_review=needs_review or queue_for_duplicate,
        duplicate_status=DuplicateStatus.SUSPECTED if duplicates else DuplicateStatus.NONE,
        duplicate_of_id=strong.certificate_id if strong else None,
        source=source,
        source_extraction_id=source_extraction_id,
        source_batch_id=source_batch_id,
        created_by=actor_id,
        updated_by=actor_id,
    )


def _side_rows(
    certificate: Certificate, *, workspace_id: uuid.UUID, draft: CertificateDraft
) -> list[CertificateName | CertificateDate]:
    """The repeatable roles, as their own rows.

    Both parties to a marriage, both of their dates of birth, every parent named -
    each one indexed, so a search for any of them finds this entry.
    """
    rows: list[CertificateName | CertificateDate] = [
        CertificateName(
            certificate_id=certificate.id,
            workspace_id=workspace_id,
            role=entry.role,
            field_name=entry.field_name,
            position=entry.position,
            value=entry.value,
            value_key=entry.key,
        )
        for entry in draft.names
    ]
    rows.extend(
        CertificateDate(
            certificate_id=certificate.id,
            workspace_id=workspace_id,
            role=entry.role,
            field_name=entry.field_name,
            position=entry.position,
            value=entry.value,
        )
        for entry in draft.dates
    )
    return rows


def _logged(certificate: Certificate, duplicates: tuple[DuplicateMatch, ...]) -> None:
    """One line per entry written. No field values: they are all personal data."""
    logger.info(
        "certificate.recorded",
        entity_id=str(certificate.id),
        workspace_id=str(certificate.workspace_id),
        extraction_source=certificate.source.value,
        duplicate_count=len(duplicates),
        review_status="NEEDS_REVIEW" if certificate.needs_review else "SETTLED",
    )


def record_revision(
    session: AsyncSession | SyncSession,
    certificate: Certificate,
    *,
    action: RevisionAction,
    changed_fields: Sequence[str] = (),
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> CertificateRevision:
    """Add a history row for what just happened to this entry.

    Does not flush: the caller is mid-transaction and the revision belongs to the same
    unit of work as the change it records, or a failure could leave one without the
    other.
    """
    revision = CertificateRevision(
        certificate_id=certificate.id,
        workspace_id=certificate.workspace_id,
        record_version=certificate.record_version,
        action=action,
        values_jsonb=dict(certificate.values_jsonb),
        changed_fields=list(changed_fields),
        note=note,
        actor_id=actor_id,
    )
    session.add(revision)
    return revision


async def record_certificate(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    schema: FieldSchema,
    values: Mapping[str, str | None],
    source: CertificateSource,
    confidences: Mapping[str, float] | None = None,
    provenance: Mapping[str, object] | None = None,
    row_confidence: float = 1.0,
    needs_review: bool = False,
    source_extraction_id: uuid.UUID | None = None,
    source_batch_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    allow_duplicate: bool = False,
    review: ReviewSettings | None = None,
    today: dt.date | None = None,
) -> Certificate:
    """Write one entry into the register.

    Raises :class:`ConflictError` when the certificate number is already held and
    the caller has not said to record it anyway. The conflict names the existing
    entry, so the answer to it is a decision about two records rather than a
    retry.
    """
    draft = build_write(schema, values, today=today)
    duplicates = await find_duplicates(
        session,
        workspace_id=workspace_id,
        certificate_type_id=certificate_type_id,
        draft=draft,
    )
    strong = next((match for match in duplicates if match.is_strong), None)
    if strong is not None and not allow_duplicate:
        raise ConflictError(
            f"Certificate number {draft.certificate_number} is already in the register.",
            remediation=(
                "Open the existing entry to compare them. If this really is a "
                "second certificate with the same number, record it as a duplicate "
                "and a reviewer will reconcile the two."
            ),
        )

    certificate = _new_certificate(
        workspace_id=workspace_id,
        certificate_type_id=certificate_type_id,
        schema=schema,
        draft=draft,
        duplicates=duplicates,
        source=source,
        confidences=confidences,
        provenance=provenance,
        row_confidence=row_confidence,
        needs_review=needs_review,
        source_extraction_id=source_extraction_id,
        source_batch_id=source_batch_id,
        actor_id=actor_id,
        review=review or ReviewSettings(),
    )
    session.add(certificate)
    await session.flush()
    for row in _side_rows(certificate, workspace_id=workspace_id, draft=draft):
        session.add(row)
    record_revision(session, certificate, action=RevisionAction.CREATED, actor_id=actor_id)
    await session.flush()
    _logged(certificate, duplicates)
    return certificate


def record_certificate_sync(
    session: SyncSession,
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    schema: FieldSchema,
    values: Mapping[str, str | None],
    source: CertificateSource,
    confidences: Mapping[str, float] | None = None,
    provenance: Mapping[str, object] | None = None,
    row_confidence: float = 1.0,
    needs_review: bool = False,
    source_extraction_id: uuid.UUID | None = None,
    source_batch_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    review: ReviewSettings | None = None,
    today: dt.date | None = None,
) -> Certificate:
    """Write one entry from a worker.

    Unlike the API path this never refuses a duplicate. A batch of five hundred
    scans cannot stop on one repeated number - the entry is written, linked to what
    it repeats and sent to review, which is where the decision belongs.
    """
    draft = build_write(schema, values, today=today)
    duplicates = find_duplicates_sync(
        session,
        workspace_id=workspace_id,
        certificate_type_id=certificate_type_id,
        draft=draft,
    )
    certificate = _new_certificate(
        workspace_id=workspace_id,
        certificate_type_id=certificate_type_id,
        schema=schema,
        draft=draft,
        duplicates=duplicates,
        source=source,
        confidences=confidences,
        provenance=provenance,
        row_confidence=row_confidence,
        needs_review=needs_review,
        source_extraction_id=source_extraction_id,
        source_batch_id=source_batch_id,
        actor_id=actor_id,
        review=review or ReviewSettings(),
    )
    session.add(certificate)
    session.flush()
    for row in _side_rows(certificate, workspace_id=workspace_id, draft=draft):
        session.add(row)
    record_revision(session, certificate, action=RevisionAction.CREATED, actor_id=actor_id)
    session.flush()
    _logged(certificate, duplicates)
    return certificate


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def _new_link(
    *,
    certificate: Certificate,
    document_id: uuid.UUID,
    unit_id: uuid.UUID | None,
    kind: DocumentLinkKind,
    page_start: int | None,
    page_end: int | None,
    note: str | None,
    actor_id: uuid.UUID | None,
) -> CertificateDocument:
    return CertificateDocument(
        certificate_id=certificate.id,
        document_id=document_id,
        unit_id=unit_id,
        workspace_id=certificate.workspace_id,
        kind=kind,
        page_start=page_start,
        page_end=page_end,
        note=note,
        linked_by=actor_id,
    )


def _existing_link(
    *, certificate_id: uuid.UUID, document_id: uuid.UUID, unit_id: uuid.UUID | None
) -> Select[tuple[CertificateDocument]]:
    statement = select(CertificateDocument).where(
        CertificateDocument.certificate_id == certificate_id,
        CertificateDocument.document_id == document_id,
    )
    if unit_id is None:
        return statement.where(CertificateDocument.unit_id.is_(None))
    return statement.where(CertificateDocument.unit_id == unit_id)


async def link_document(
    session: AsyncSession,
    *,
    certificate: Certificate,
    document_id: uuid.UUID,
    unit_id: uuid.UUID | None = None,
    kind: DocumentLinkKind = DocumentLinkKind.PRIMARY,
    page_start: int | None = None,
    page_end: int | None = None,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> CertificateDocument:
    """Attach a document to an entry, by identity rather than by name.

    Idempotent: linking the same document and page range twice returns the existing
    link, so a retried task cannot produce two.
    """
    existing = await session.scalar(
        _existing_link(certificate_id=certificate.id, document_id=document_id, unit_id=unit_id)
    )
    if existing is not None:
        return existing

    link = _new_link(
        certificate=certificate,
        document_id=document_id,
        unit_id=unit_id,
        kind=kind,
        page_start=page_start,
        page_end=page_end,
        note=note,
        actor_id=actor_id,
    )
    session.add(link)
    await session.flush()
    return link


def link_document_sync(
    session: SyncSession,
    *,
    certificate: Certificate,
    document_id: uuid.UUID,
    unit_id: uuid.UUID | None = None,
    kind: DocumentLinkKind = DocumentLinkKind.PRIMARY,
    page_start: int | None = None,
    page_end: int | None = None,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> CertificateDocument:
    existing = session.scalar(
        _existing_link(certificate_id=certificate.id, document_id=document_id, unit_id=unit_id)
    )
    if existing is not None:
        return existing

    link = _new_link(
        certificate=certificate,
        document_id=document_id,
        unit_id=unit_id,
        kind=kind,
        page_start=page_start,
        page_end=page_end,
        note=note,
        actor_id=actor_id,
    )
    session.add(link)
    session.flush()
    return link


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
async def get_certificate(
    session: AsyncSession, *, workspace_id: uuid.UUID, certificate_id: uuid.UUID
) -> Certificate:
    """One entry, or 404.

    Scoped by workspace in the same statement as the id, so an entry belonging to
    another office is indistinguishable from one that does not exist.
    """
    certificate = await session.scalar(
        select(Certificate).where(
            Certificate.id == certificate_id,
            Certificate.workspace_id == workspace_id,
        )
    )
    if certificate is None:
        raise NotFoundError("That certificate is not in this register.")
    return certificate


def role_values(
    certificate: Certificate, schema: FieldSchema, *roles: FieldRole
) -> tuple[str, ...]:
    """The stored values for a role, read back through the schema.

    Used by screens and exports that want "the father's name" without knowing what
    the field is called in this particular schema.
    """
    return tuple(
        value
        for name in schema.names_for(*roles)
        if (value := certificate.values_jsonb.get(name)) is not None and isinstance(value, str)
    )


async def list_certificates(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID | None = None,
    needs_review: bool | None = None,
    duplicates_only: bool = False,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> tuple[list[Certificate], Cursor | None]:
    """A page of entries, newest last, over a keyset rather than an offset.

    The register is written while it is being read - a batch finishing adds rows
    under whoever is paging through them - and an offset would silently skip or
    repeat entries at every page boundary.
    """
    query = select(Certificate).where(Certificate.workspace_id == workspace_id)
    if certificate_type_id is not None:
        query = query.where(Certificate.certificate_type_id == certificate_type_id)
    if needs_review is not None:
        query = query.where(Certificate.needs_review.is_(needs_review))
    if duplicates_only:
        query = query.where(Certificate.duplicate_status != DuplicateStatus.NONE)
    if cursor is not None:
        query = query.where(
            or_(
                Certificate.created_at > cursor.created_at,
                (Certificate.created_at == cursor.created_at) & (Certificate.id > cursor.id),
            )
        )

    rows = list(
        (
            await session.scalars(
                query.order_by(Certificate.created_at, Certificate.id).limit(limit + 1)
            )
        ).all()
    )
    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)
    return rows, next_cursor


async def load_detail(
    session: AsyncSession, *, workspace_id: uuid.UUID, certificate_id: uuid.UUID
) -> Certificate:
    """One entry with its names, dates and document links already loaded.

    Eager-loaded in one round trip: the detail screen always shows all three, and
    lazy loading them would be four queries and, in an async session, an error.
    """
    certificate = await session.scalar(
        select(Certificate)
        .where(Certificate.id == certificate_id, Certificate.workspace_id == workspace_id)
        .options(
            selectinload(Certificate.names),
            selectinload(Certificate.dates),
            selectinload(Certificate.documents),
        )
    )
    if certificate is None:
        raise NotFoundError("That certificate is not in this register.")
    return certificate


async def replace_document(
    session: AsyncSession,
    *,
    certificate: Certificate,
    link_id: uuid.UUID,
    document_id: uuid.UUID,
    unit_id: uuid.UUID | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> tuple[CertificateDocument, CertificateDocument]:
    """Attach a better scan and keep the one it replaces.

    Returns (replaced, added). The old link is marked superseded rather than deleted: a
    value in the register was read from that image, and "why does it say that" has to
    stay answerable against the image it was actually read from - not against a later,
    better photograph of the same page.
    """
    replaced = await session.scalar(
        select(CertificateDocument).where(
            CertificateDocument.id == link_id,
            CertificateDocument.certificate_id == certificate.id,
            CertificateDocument.workspace_id == certificate.workspace_id,
        )
    )
    if replaced is None:
        raise NotFoundError("That document is not attached to this certificate.")

    document = await session.get(Document, document_id)
    if document is None or document.workspace_id != certificate.workspace_id:
        raise NotFoundError("That document is not in this workspace.")
    if document.id == replaced.document_id:
        raise ConflictError(
            "That is the scan already attached.",
            remediation="Upload the new scan first, then replace this one with it.",
        )

    existing = await session.scalar(
        _existing_link(certificate_id=certificate.id, document_id=document.id, unit_id=unit_id)
    )
    if existing is not None:
        raise ConflictError(
            "That scan is already attached to this entry.",
            remediation="Remove the duplicate attachment, or replace the other link.",
        )

    replaced.kind = DocumentLinkKind.SUPERSEDED
    added = CertificateDocument(
        certificate_id=certificate.id,
        document_id=document.id,
        unit_id=unit_id,
        workspace_id=certificate.workspace_id,
        kind=DocumentLinkKind.PRIMARY,
        page_start=page_start if page_start is not None else replaced.page_start,
        page_end=page_end if page_end is not None else replaced.page_end,
        note=note,
        linked_by=actor_id,
    )
    session.add(added)
    await session.flush()
    logger.info(
        "certificate.document_replaced",
        entity_id=str(certificate.id),
        document_id=str(document.id),
        related_id=str(replaced.id),
    )
    return replaced, added
