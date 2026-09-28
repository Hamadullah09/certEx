"""Live progress while a batch is being processed.

An operator who has handed over two hundred certificates watches this screen, so the
stream has to be right about two things: the numbers, and when to stop. A stream that
never ends holds a connection open forever; one that ends early leaves the progress bar
frozen at 94% while the work finishes behind it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from certex.api.v1 import progress as progress_api
from certex.db.models import Batch, User
from certex.enums import BatchStatus
from tests.conftest import authenticate

pytestmark = pytest.mark.integration


async def read_events(
    client: AsyncClient, url: str, *, stop_after: int = 1
) -> list[tuple[str, dict[str, object]]]:
    """Collect server-sent events until the stream ends or enough have arrived."""
    events: list[tuple[str, dict[str, object]]] = []
    async with client.stream("GET", url) as response:
        assert response.status_code == 200, await response.aread()
        name = "message"
        async for line in _lines(response.aiter_lines()):
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                events.append((name, json.loads(payload) if payload else {}))
                if len(events) >= stop_after:
                    break
    return events


async def _lines(source: AsyncIterator[str]) -> AsyncIterator[str]:
    async for line in source:
        if line.strip():
            yield line


class TestTheFirstEvent:
    async def test_a_batch_reports_where_it_stands_immediately(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        # The screen must show something the moment it opens, not after the first change.
        batch.status = BatchStatus.PROCESSING
        batch.file_count = 4
        batch.processed_count = 1
        batch.failed_count = 1
        await db_session.flush()
        await authenticate(api_client, operator_user)

        events = await read_events(api_client, f"/api/v1/batches/{batch.id}/progress")

        name, data = events[0]
        assert name == "progress"
        assert data["file_count"] == 4
        assert data["processed_count"] == 1
        assert data["failed_count"] == 1
        assert data["finished"] is False

    async def test_the_percentage_counts_every_finished_file(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        # A duplicate and a failure are both finished as far as the bar is concerned.
        batch.status = BatchStatus.PROCESSING
        batch.file_count = 4
        batch.processed_count = 1
        batch.failed_count = 1
        batch.duplicate_count = 1
        await db_session.flush()
        await authenticate(api_client, operator_user)

        _name, data = (await read_events(api_client, f"/api/v1/batches/{batch.id}/progress"))[0]

        assert data["percent"] == 75.0

    async def test_a_batch_with_no_files_does_not_divide_by_zero(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        batch.status = BatchStatus.PROCESSING
        await db_session.flush()
        await authenticate(api_client, operator_user)

        _name, data = (await read_events(api_client, f"/api/v1/batches/{batch.id}/progress"))[0]

        assert data["percent"] == 0.0


class TestEnding:
    @pytest.mark.parametrize(
        "status",
        [BatchStatus.COMPLETED, BatchStatus.COMPLETED_WITH_ERRORS, BatchStatus.FAILED],
    )
    async def test_a_finished_batch_sends_one_event_and_stops(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        status: BatchStatus,
    ) -> None:
        # No polling loop, no open connection: the work is done.
        batch.status = status
        batch.file_count = 2
        batch.processed_count = 2
        await db_session.flush()
        await authenticate(api_client, operator_user)

        events = await read_events(api_client, f"/api/v1/batches/{batch.id}/progress", stop_after=5)

        assert len(events) == 1
        assert events[0][1]["finished"] is True
        assert events[0][1]["status"] == status.value

    async def test_a_finished_batch_reports_a_hundred_percent(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        batch.status = BatchStatus.COMPLETED
        batch.file_count = 3
        batch.processed_count = 3
        await db_session.flush()
        await authenticate(api_client, operator_user)

        _name, data = (await read_events(api_client, f"/api/v1/batches/{batch.id}/progress"))[0]

        assert data["percent"] == 100.0


class TestWhoMayWatch:
    async def test_a_viewer_may_watch(
        self, api_client: AsyncClient, db_session: AsyncSession, viewer_user: User, batch: Batch
    ) -> None:
        batch.status = BatchStatus.COMPLETED
        await db_session.flush()
        await authenticate(api_client, viewer_user)

        events = await read_events(api_client, f"/api/v1/batches/{batch.id}/progress")

        assert events[0][0] == "progress"

    async def test_another_workspaces_batch_is_not_watchable(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        # Checked before the stream opens, so a wrong id fails at once rather than
        # hanging on an empty stream.
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/batches/{uuid.uuid4()}/progress")

        assert response.status_code == 404

    async def test_signing_in_is_required(self, api_client: AsyncClient, batch: Batch) -> None:
        response = await api_client.get(f"/api/v1/batches/{batch.id}/progress")
        assert response.status_code == 401


class TestTheStreamItself:
    def test_it_polls_often_enough_to_look_live(self) -> None:
        assert progress_api._POLL_SECONDS <= 2.0

    def test_it_speaks_before_a_proxy_would_give_up_on_it(self) -> None:
        # A silent connection looks dead to whatever sits in front of this service.
        assert 10.0 <= progress_api._HEARTBEAT_SECONDS <= 30.0

    def test_it_does_not_run_forever(self) -> None:
        # A batch that has not finished in an hour is a person's problem, not a stream's.
        assert progress_api._MAX_STREAM_SECONDS <= 3600.0

    def test_a_finished_batch_is_one_of_the_states_it_stops_on(self) -> None:
        assert set(progress_api._TERMINAL) == {
            BatchStatus.COMPLETED,
            BatchStatus.COMPLETED_WITH_ERRORS,
            BatchStatus.FAILED,
            BatchStatus.CANCELLED,
        }
