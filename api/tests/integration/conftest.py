"""Fixtures for tests that run pipeline stages.

A stage runs in a Celery worker, so it opens its own synchronous sessions and commits
as it goes. The suite's usual ``db_session`` fixture wraps each test in a transaction
that is rolled back, and a worker in another session would see none of those rows -
so stage tests commit their fixtures for real and clean up afterwards by truncating
every table.

Each fixture also uploads the document's bytes to object storage under the same key
the ingest stage would have used, because a stage starts by downloading them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import text

from certex.core.security import hash_password
from certex.db.base import Base
from certex.db.models import Batch, Document, User, Workspace
from certex.db.session import get_sync_engine, session_scope
from certex.enums import BatchStatus, DocumentStatus, UserRole
from certex.pipeline.dispatch import TaskArgument
from certex.storage.s3 import ObjectStorage, StorageKeys
from tests.conftest import TEST_PASSWORD


@dataclass(slots=True)
class Recorder:
    """Stands in for Celery's dispatch, capturing what the stage would enqueue."""

    calls: list[tuple[str, dict[str, TaskArgument]]] = field(default_factory=list)

    def __call__(self, task_name: str, kwargs: dict[str, TaskArgument]) -> None:
        self.calls.append((task_name, kwargs))

    @property
    def task_names(self) -> list[str]:
        return [name for name, _ in self.calls]


@dataclass(slots=True)
class CommittedBatch:
    """A workspace, an operator and a processing batch, committed to the database."""

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    batch_id: uuid.UUID
    storage: ObjectStorage
    storage_keys: list[str] = field(default_factory=list)

    def add_document(
        self,
        path: Path,
        *,
        mime_type: str,
        status: DocumentStatus = DocumentStatus.QUEUED,
        upload: bool = True,
    ) -> uuid.UUID:
        """Ingest a local file the way the upload stage would, and return its id."""
        document_id = uuid.uuid4()
        now = dt.datetime.now(tz=dt.UTC)
        key = StorageKeys.document(self.workspace_id, document_id, year=now.year, month=now.month)
        payload = path.read_bytes()
        if upload:
            self.storage.upload_bytes(key, payload, content_type=mime_type)
            self.storage_keys.append(key)
        with session_scope() as session:
            session.add(
                Document(
                    id=document_id,
                    batch_id=self.batch_id,
                    workspace_id=self.workspace_id,
                    original_filename=path.name,
                    mime_type=mime_type,
                    byte_size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    storage_key=key,
                    status=status,
                )
            )
        return document_id


def _truncate_everything() -> None:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with get_sync_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def committed_batch(
    db_engine: object, object_storage: ObjectStorage
) -> Iterator[CommittedBatch]:
    """A committed batch a pipeline stage can actually see.

    ``db_engine`` is requested only for the schema it creates.
    """
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            Workspace(
                id=workspace_id, name=f"Stage Workspace {workspace_id.hex[:8]}", settings_json={}
            )
        )
        session.flush()
        session.add(
            User(
                id=user_id,
                workspace_id=workspace_id,
                email=f"operator-{user_id.hex[:8]}@example.com",
                password_hash=hash_password(TEST_PASSWORD),
                role=UserRole.OPERATOR,
                is_active=True,
            )
        )
        # Batch.created_by is a bare foreign key with no relationship, so the unit of
        # work cannot know the user must be inserted first.
        session.flush()
        session.add(
            Batch(
                id=batch_id,
                workspace_id=workspace_id,
                name="Stage Batch",
                status=BatchStatus.PROCESSING,
                settings_json={},
                created_by=user_id,
                started_at=dt.datetime.now(tz=dt.UTC),
            )
        )

    fixture = CommittedBatch(
        workspace_id=workspace_id, user_id=user_id, batch_id=batch_id, storage=object_storage
    )
    try:
        yield fixture
    finally:
        for key in fixture.storage_keys:
            object_storage.delete(key)
        _truncate_everything()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()
