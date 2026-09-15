"""Health probe tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration]


class TestLiveness:
    async def test_live_needs_no_dependencies(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/health/live")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "up"
        assert body["version"]
        assert body["components"] == []

    async def test_live_requires_no_authentication(self, api_client: AsyncClient) -> None:
        assert (await api_client.get("/health/live")).status_code == 200

    async def test_also_mounted_under_the_versioned_prefix(self, api_client: AsyncClient) -> None:
        assert (await api_client.get("/api/v1/health/live")).status_code == 200


class TestReadiness:
    async def test_reports_each_component(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/health/ready")
        assert response.status_code in (200, 503)

        body = response.json()
        names = {component["name"] for component in body["components"]}
        assert names == {"database", "redis"}

        for component in body["components"]:
            assert component["state"] in {"up", "down", "degraded"}
            assert component["latency_ms"] is not None

    async def test_database_is_reachable_in_the_test_environment(
        self, api_client: AsyncClient
    ) -> None:
        body = (await api_client.get("/health/ready")).json()
        database = next(c for c in body["components"] if c["name"] == "database")
        assert database["state"] == "up", database.get("detail")

    async def test_status_is_503_when_a_required_component_is_down(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _broken() -> None:
            raise RuntimeError("connection refused")

        monkeypatch.setattr("certex.api.v1.health._probe_database", _broken)

        response = await api_client.get("/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "down"
        database = next(c for c in body["components"] if c["name"] == "database")
        assert database["state"] == "down"
        assert "RuntimeError" in database["detail"]
