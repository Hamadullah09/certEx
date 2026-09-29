"""Loading a CSV of existing records into the register.

Runs in a worker, never in a request. A file of three hundred thousand rows takes
minutes, and the operator watches the counters on the import row rather than holding a
connection open.

Three things this stage will not do, each because the alternative loses a record:

**It will not overwrite.** A certificate number the register already holds is either
skipped with an error naming the entry that holds it, or recorded as a second entry
linked to the first - whichever the operator chose. Never a replacement.

**It will not stop on a bad row.** Row 4,182 having an unreadable date is a fact about
row 4,182. It is recorded, in terms a clerk can act on, and the other 299,999 rows are
loaded.

**It will not half-load a bad file.** Columns the schema does not define, or a missing
certificate-number column, are decided before the first row is written, because an
office cannot un-import a register.

Committed in batches: one transaction per file would hold locks for the length of the
run and lose everything to a single failure at the end, while one per row would spend
the whole time in transaction overhead.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy.orm import Session as SyncSession

from certex.certificates.draft import build_draft
from certex.config import Settings
from certex.core.errors import ConflictError, ValidationFailedError
from certex.db.models import CertificateImport, CertificateTypeRecord
from certex.db.session import session_scope
from certex.enums import CertificateSource, CertificateType, ImportDuplicatePolicy
from certex.fields import FieldSchema, builtin_schema
from certex.imports.reader import (
    HeaderPlan,
    RowRead,
    decode_stream,
    detect_delimiter,
    plan_headers,
    read_rows,
)
from certex.logging_setup import get_logger
from certex.services import certificate_service, import_service, schema_service
from certex.services.import_service import ImportCounts
from certex.storage.s3 import ObjectStorage, get_object_storage

__all__ = ["ImportStageResult", "run_import_stage"]

logger = get_logger(__name__)

_COMMIT_EVERY: Final = 200
"""Rows per transaction. Small enough that a failure costs little and progress is
visible while the file is still being read; large enough that the commit is not the
expensive part of the run."""


@dataclass(frozen=True, slots=True)
class ImportStageResult:
    import_id: uuid.UUID
    ran: bool
    counts: ImportCounts = field(default_factory=ImportCounts)
    failure: str | None = None


def run_import_stage(
    import_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    storage: ObjectStorage | None = None,
) -> ImportStageResult:
    """Read one uploaded CSV into the register."""
    del settings  # the import has no settings of its own; everything is on the row
    store = storage or get_object_storage()

    with session_scope() as session:
        record = import_service.start_import(session, import_id)
        if record is None:
            return ImportStageResult(import_id=import_id, ran=False)
        plan_input = _prepare(session, record)

    if plan_input.failure is not None:
        with session_scope() as session:
            record = _reload(session, import_id)
            import_service.finish_import(
                session, record, ImportCounts(), failure=plan_input.failure
            )
        return ImportStageResult(import_id=import_id, ran=True, failure=plan_input.failure[0])

    return _load(import_id, plan_input, store=store)


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Everything the read needs, resolved before the file is opened."""

    schema: FieldSchema | None = None
    certificate_type_id: uuid.UUID | None = None
    storage_key: str = ""
    delimiter: str = ","
    duplicate_policy: ImportDuplicatePolicy = ImportDuplicatePolicy.SKIP
    workspace_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    failure: tuple[str, str] | None = None


def _reload(session: SyncSession, import_id: uuid.UUID) -> CertificateImport:
    record = session.get(CertificateImport, import_id)
    if record is None:  # pragma: no cover - the caller just read it
        raise LookupError(f"Import {import_id} disappeared mid-run")
    return record


def _prepare(session: SyncSession, record: CertificateImport) -> _Prepared:
    """Resolve the schema this file will be read against."""
    type_record = session.get(CertificateTypeRecord, record.certificate_type_id)
    if type_record is None or type_record.workspace_id != record.workspace_id:
        return _Prepared(
            failure=(
                "certificate_type_missing",
                "The certificate type this import was created for no longer exists.",
            )
        )

    schema: FieldSchema | None = None
    if record.schema_version_id is not None:
        schema = schema_service.resolve_schema_sync(session, record.schema_version_id)
    if schema is None:
        version = schema_service.default_version_for_type_sync(
            session,
            workspace_id=record.workspace_id,
            certificate_type_id=record.certificate_type_id,
        )
        if version is not None:
            schema = schema_service.resolve_schema_sync(session, version.id)
    if schema is None:
        schema = builtin_schema(type_record.classifier_key or CertificateType.OTHER)

    return _Prepared(
        schema=schema,
        certificate_type_id=record.certificate_type_id,
        storage_key=record.storage_key,
        delimiter=record.delimiter,
        duplicate_policy=record.duplicate_policy,
        workspace_id=record.workspace_id,
        actor_id=record.created_by,
    )


def _header_failure(plan: HeaderPlan, schema: FieldSchema) -> tuple[str, str] | None:
    """Why this file's columns cannot be used, in words a clerk can act on."""
    if plan.missing_required:
        names = ", ".join(plan.missing_required)
        return (
            "missing_columns",
            f"The file has no column for {names}. Download the template to see the "
            "columns this certificate type expects.",
        )
    if plan.duplicated:
        names = ", ".join(plan.duplicated)
        return (
            "duplicate_columns",
            f"Two columns both supply {names}. Remove one of them and try again.",
        )
    if plan.unknown:
        shown = ", ".join(plan.unknown[:5])
        more = "" if len(plan.unknown) <= 5 else f" and {len(plan.unknown) - 5} more"
        return (
            "unknown_columns",
            f"The schema has no field for {shown}{more}. Either remove the column or "
            "add the field to the schema, then import again.",
        )
    if not plan.mapped_fields:
        return ("no_columns", "None of the file's columns matched this certificate type.")
    del schema
    return None


def _load(import_id: uuid.UUID, prepared: _Prepared, *, store: ObjectStorage) -> ImportStageResult:
    """Stream the file and file its rows, committing as it goes."""
    schema = prepared.schema
    if schema is None:  # pragma: no cover - _prepare fills it whenever failure is None
        raise ValueError("An import cannot be read without a schema.")

    counts = ImportCounts()
    errors_recorded = 0
    encoding: str | None = None
    body = store.download_stream(prepared.storage_key)
    try:
        lines, encoding = decode_stream(body)
        header_line = next(lines, None)
        if header_line is None:
            with session_scope() as session:
                import_service.finish_import(
                    session,
                    _reload(session, import_id),
                    counts,
                    encoding=encoding,
                    failure=("empty_file", "The file has no rows at all."),
                )
            return ImportStageResult(import_id=import_id, ran=True, failure="empty_file")

        delimiter = prepared.delimiter or detect_delimiter(header_line)
        headers = _split_header(header_line, delimiter)
        plan = plan_headers(schema, headers)
        failure = _header_failure(plan, schema)
        if failure is not None:
            with session_scope() as session:
                import_service.finish_import(
                    session,
                    _reload(session, import_id),
                    counts,
                    encoding=encoding,
                    failure=failure,
                )
            return ImportStageResult(import_id=import_id, ran=True, failure=failure[0])

        rows = read_rows(lines, plan, delimiter=delimiter)
        while True:
            chunk = _take(rows, _COMMIT_EVERY)
            if not chunk:
                break
            with session_scope() as session:
                record = _reload(session, import_id)
                for row in chunk:
                    counts, errors_recorded = _file_row(
                        session,
                        record,
                        row,
                        prepared=prepared,
                        schema=schema,
                        counts=counts,
                        errors_recorded=errors_recorded,
                    )
                import_service.update_counts(session, record, counts, encoding=encoding)
    finally:
        body.close()

    with session_scope() as session:
        import_service.finish_import(
            session, _reload(session, import_id), counts, encoding=encoding
        )
    return ImportStageResult(import_id=import_id, ran=True, counts=counts)


def _split_header(line: str, delimiter: str) -> list[str]:
    """The header row, parsed as CSV so a quoted heading survives."""
    return next(csv.reader(io.StringIO(line), delimiter=delimiter), [])


def _take(rows: Iterator[RowRead], count: int) -> list[RowRead]:
    """The next ``count`` rows, or fewer at the end of the file."""
    taken: list[RowRead] = []
    for row in rows:
        taken.append(row)
        if len(taken) >= count:
            break
    return taken


def _file_row(
    session: SyncSession,
    record: CertificateImport,
    row: RowRead,
    *,
    prepared: _Prepared,
    schema: FieldSchema,
    counts: ImportCounts,
    errors_recorded: int,
) -> tuple[ImportCounts, int]:
    """File one row, or record why it could not be filed."""
    workspace_id = prepared.workspace_id
    certificate_type_id = prepared.certificate_type_id
    if workspace_id is None or certificate_type_id is None:  # pragma: no cover - defensive
        raise ValueError("An import without a workspace or a type should not have started.")

    total = counts.total + 1
    if row.is_blank:
        return ImportCounts(
            total=total, created=counts.created, skipped=counts.skipped + 1, failed=counts.failed
        ), errors_recorded

    draft = build_draft(schema, row.values)
    number = draft.certificate_number or None

    def fail(
        code: str, message: str, *, field_name: str | None = None, value: str | None = None
    ) -> tuple[ImportCounts, int]:
        kept = import_service.record_row_error(
            session,
            record,
            row_number=row.row_number,
            code=code,
            message=message,
            field_name=field_name,
            certificate_number=number,
            value_excerpt=value,
            recorded_so_far=errors_recorded,
        )
        return (
            ImportCounts(
                total=total,
                created=counts.created,
                skipped=counts.skipped,
                failed=counts.failed + 1,
            ),
            errors_recorded + (1 if kept else 0),
        )

    if row.too_many_columns:
        return fail(
            "extra_columns",
            "This row has more values than the file has columns, so the values after "
            "the last column would be lost.",
        )

    if not draft.has_identifier:
        identifier = schema.identifier
        column = identifier.name if identifier else "the certificate number"
        return fail(
            "missing_identifier",
            f"{column} is empty. Every entry is found by its certificate number, so a "
            "row without one cannot be filed.",
            field_name=identifier.name if identifier else None,
        )

    try:
        certificate = certificate_service.record_certificate_sync(
            session,
            workspace_id=workspace_id,
            certificate_type_id=certificate_type_id,
            schema=schema,
            values=row.values,
            source=CertificateSource.IMPORT,
            actor_id=prepared.actor_id,
            needs_review=False,
        )
    except ValidationFailedError as exc:
        return fail("invalid_row", exc.detail or "This row could not be filed.")
    except ConflictError as exc:  # pragma: no cover - the sync path does not raise this
        return fail("duplicate_number", exc.detail or "That certificate number is taken.")

    if certificate.duplicate_of_id is not None and (
        prepared.duplicate_policy is ImportDuplicatePolicy.SKIP
    ):
        # Recorded, then withdrawn: detection needs the row in place to compare
        # against, and the policy says the register keeps only the first.
        session.delete(certificate)
        session.flush()
        kept = import_service.record_row_error(
            session,
            record,
            row_number=row.row_number,
            code="duplicate_number",
            message=(
                f"The register already holds certificate number {number}. This row was "
                "not imported. Change the policy to record duplicates if both entries "
                "should be kept for a reviewer to reconcile."
            ),
            certificate_number=number,
            recorded_so_far=errors_recorded,
        )
        return (
            ImportCounts(
                total=total,
                created=counts.created,
                skipped=counts.skipped + 1,
                failed=counts.failed,
            ),
            errors_recorded + (1 if kept else 0),
        )

    return (
        ImportCounts(
            total=total,
            created=counts.created + 1,
            skipped=counts.skipped,
            failed=counts.failed,
        ),
        errors_recorded,
    )
