"""Finding a certificate, the way the counter actually works.

Somebody arrives with a number, or with a name and roughly a year. The tests below are
the cases that decide whether a clerk can answer in thirty seconds: a number that is
exactly right, a number half remembered, a name four people share, and a name spelled
the other way.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.security import hash_password
from certex.db.models import User, Workspace
from certex.enums import UserRole
from certex.services.search_service import MatchKind
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
CERTIFICATES = "/api/v1/certificates"
SEARCH = f"{CERTIFICATES}/search"


async def birth_type_id(client: AsyncClient) -> str:
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    return next(item["id"] for item in response.json() if item["key"] == "BIRTH")


async def record(
    client: AsyncClient,
    *,
    type_id: str,
    number: str,
    child: str = "Ayesha Noor Malik",
    father: str = "Tariq Mahmood Malik",
    mother: str | None = None,
    birth: str = "2019-04-03",
) -> dict[str, Any]:
    values: dict[str, str] = {
        "certificate_number": number,
        "child_full_name": child,
        "father_full_name": father,
        "date_of_birth": birth,
    }
    if mother is not None:
        values["mother_full_name"] = mother
    response = await client.post(
        CERTIFICATES, json={"certificate_type_id": type_id, "values": values}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def find(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(SEARCH, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def numbers(results: dict[str, Any]) -> list[str]:
    return [item["certificate"]["certificate_number"] for item in results["items"]]


async def a_second_office(session: AsyncSession) -> User:
    workspace = Workspace(name=f"Other Office {uuid.uuid4().hex[:8]}", settings_json={})
    session.add(workspace)
    await session.flush()
    user = User(
        workspace_id=workspace.id,
        email=f"outsider-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(TEST_PASSWORD),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def register(api_client: AsyncClient, operator_user: User) -> str:
    """A small register with the collisions a real one has."""
    await authenticate(api_client, operator_user)
    type_id = await birth_type_id(api_client)

    await record(api_client, type_id=type_id, number="BC/LHR/2019/1001")
    await record(
        api_client,
        type_id=type_id,
        number="BC/LHR/2019/1002",
        child="Muhammad Ahmed",
        father="Tariq Mahmood Malik",
        birth="2018-11-20",
    )
    await record(
        api_client,
        type_id=type_id,
        number="BC/LHR/2019/1003",
        child="Muhammad Ahmed",
        father="Imran Hussain",
        birth="2017-06-14",
    )
    await record(
        api_client,
        type_id=type_id,
        number="BC/KHI/2020/5501",
        child="Sana Fatima",
        father="Rashid Ali",
        mother="Nasreen Akhtar",
        birth="2020-02-29",
    )
    return type_id


class TestByNumber:
    async def test_the_exact_number_is_the_answer(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="BC/LHR/2019/1001")
        assert results["match"] == MatchKind.CERTIFICATE_NUMBER
        assert numbers(results) == ["BC/LHR/2019/1001"]

    async def test_separators_and_case_do_not_matter(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """A clerk types what is easiest, not what is printed."""
        results = await find(api_client, q="bc-lhr-2019-1001")
        assert numbers(results) == ["BC/LHR/2019/1001"]

    async def test_a_partly_remembered_number_still_finds_it(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="BC/LHR/2019")
        assert results["match"] == MatchKind.NUMBER_PREFIX
        assert set(numbers(results)) == {
            "BC/LHR/2019/1001",
            "BC/LHR/2019/1002",
            "BC/LHR/2019/1003",
        }

    async def test_too_few_characters_to_be_a_prefix_finds_nothing(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """Two characters match half the register, so they match nothing instead.

        The screen shows this as "keep typing" rather than as "no such certificate".
        """
        results = await find(api_client, q="BC")
        assert results["items"] == []
        assert results["match"] == MatchKind.NONE

    async def test_a_number_nobody_holds_finds_nothing(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="ZZ/9999/9999")
        assert results["items"] == []
        assert results["match"] == MatchKind.NONE
        assert results["total"] == 0

    async def test_a_wildcard_is_not_a_wildcard(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """Otherwise a search for "%" would return the whole register."""
        results = await find(api_client, q="%")
        assert results["items"] == []

    async def test_an_underscore_is_not_a_wildcard(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="BC_LHR_2019_1001")
        assert numbers(results) == ["BC/LHR/2019/1001"], "the underscores are separators here"


class TestByName:
    async def test_a_name_finds_everyone_who_has_it(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="Muhammad Ahmed")
        assert results["match"] == MatchKind.NAME
        assert set(numbers(results)) == {"BC/LHR/2019/1002", "BC/LHR/2019/1003"}

    async def test_the_result_says_how_many_share_the_name(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """The number a clerk needs before they start reading."""
        results = await find(api_client, q="Muhammad Ahmed")
        assert {item["same_name_count"] for item in results["items"]} == {2}

    async def test_each_namesake_carries_what_tells_them_apart(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="Muhammad Ahmed")
        summaries = [item["certificate"] for item in results["items"]]
        assert all(summary["event_date"] for summary in summaries)
        assert len({summary["event_date"] for summary in summaries}) == 2

    async def test_an_honorific_does_not_hide_the_name(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="Mr Muhammad Ahmed")
        assert results["match"] == MatchKind.NAME
        assert len(results["items"]) == 2

    async def test_a_parent_is_searchable_too(self, api_client: AsyncClient, register: str) -> None:
        """A clerk with only the mother's name still has to find the certificate."""
        results = await find(api_client, q="Nasreen Akhtar")
        assert numbers(results) == ["BC/KHI/2020/5501"]

    async def test_the_other_spelling_still_finds_it(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """Mohammad and Muhammad are the same name written by two clerks."""
        results = await find(api_client, q="Mohammad Ahmed")
        assert results["match"] == MatchKind.SIMILAR_NAME
        assert set(numbers(results)) == {"BC/LHR/2019/1002", "BC/LHR/2019/1003"}

    async def test_an_unrelated_name_finds_nothing(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, q="Xavier Kowalski")
        assert results["items"] == []


class TestFilters:
    async def test_the_fathers_name_narrows_a_shared_name(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """Which of the two Muhammad Ahmeds: the one whose father is Imran Hussain."""
        results = await find(api_client, name="Muhammad Ahmed", father_name="Imran Hussain")
        assert numbers(results) == ["BC/LHR/2019/1003"]

    async def test_a_date_range_narrows_it(self, api_client: AsyncClient, register: str) -> None:
        results = await find(api_client, name="Muhammad Ahmed", event_date_from="2018-01-01")
        assert numbers(results) == ["BC/LHR/2019/1002"]

    async def test_filters_alone_browse_the_register(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, event_date_from="2020-01-01")
        assert results["match"] == MatchKind.FILTERED
        assert numbers(results) == ["BC/KHI/2020/5501"]

    async def test_newest_event_first_when_browsing(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client)
        dates = [item["certificate"]["event_date"] for item in results["items"]]
        assert dates == sorted(dates, reverse=True)

    async def test_the_type_can_be_pinned(self, api_client: AsyncClient, register: str) -> None:
        results = await find(api_client, certificate_type_id=register)
        assert len(results["items"]) == 4

    async def test_another_type_holds_none_of_them(
        self, api_client: AsyncClient, register: str
    ) -> None:
        listed = (await api_client.get(TYPES)).json()
        death = next(item["id"] for item in listed if item["key"] == "DEATH")
        results = await find(api_client, certificate_type_id=death)
        assert results["items"] == []


class TestPaging:
    async def test_a_page_says_how_many_there_are_in_all(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, limit=2)
        assert len(results["items"]) == 2
        assert results["total"] == 4
        assert results["has_more"] is True

    async def test_the_pages_between_them_hold_everything_once(
        self, api_client: AsyncClient, register: str
    ) -> None:
        first = await find(api_client, limit=2, offset=0)
        second = await find(api_client, limit=2, offset=2)
        assert second["has_more"] is False
        assert not set(numbers(first)) & set(numbers(second)), "no result on two pages"
        assert len(set(numbers(first)) | set(numbers(second))) == 4

    async def test_paging_past_the_end_is_empty_not_an_error(
        self, api_client: AsyncClient, register: str
    ) -> None:
        results = await find(api_client, limit=2, offset=100)
        assert results["items"] == []
        assert results["total"] == 4

    async def test_a_prefix_page_counts_only_its_own_tier(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """The total belongs to the tier that answered, not to the register."""
        results = await find(api_client, q="BC/LHR", limit=2)
        assert results["match"] == MatchKind.NUMBER_PREFIX
        assert results["total"] == 3

    async def test_paging_absurdly_deep_is_refused(
        self, api_client: AsyncClient, register: str
    ) -> None:
        """One request must not be able to scan the register."""
        response = await api_client.get(SEARCH, params={"q": "BC", "offset": 10_000_000})
        assert response.status_code == 422


class TestAccess:
    async def test_a_viewer_can_search(
        self, api_client: AsyncClient, register: str, viewer_user: User
    ) -> None:
        await authenticate(api_client, viewer_user)
        results = await find(api_client, q="BC/LHR/2019/1001")
        assert len(results["items"]) == 1

    async def test_signing_in_is_required(self, api_client: AsyncClient) -> None:
        assert (await api_client.get(SEARCH, params={"q": "BC"})).status_code == 401

    async def test_another_office_s_register_is_not_searched(
        self, api_client: AsyncClient, register: str, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        results = await find(api_client, q="BC/LHR/2019/1001")
        assert results["items"] == []

    async def test_the_same_name_count_stops_at_the_workspace_boundary(
        self, api_client: AsyncClient, register: str, db_session: AsyncSession, operator_user: User
    ) -> None:
        """Otherwise the count would leak how many records another office holds."""
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        their_type = await birth_type_id(api_client)
        await record(api_client, type_id=their_type, number="BC/OTHER/1", child="Muhammad Ahmed")

        await authenticate(api_client, operator_user)
        results = await find(api_client, q="Muhammad Ahmed")
        assert {item["same_name_count"] for item in results["items"]} == {2}


class TestSearchingAConfiguredField:
    """Searching a column the office invented.

    The number and name tiers know about certificate numbers and about people. They
    know nothing about "Village", "Blood group" or whatever else a particular register
    prints, and those are exactly the columns a clerk is given to search by.
    """

    async def test_a_field_is_matched_exactly_by_its_configured_name(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id, number="BC/2020/1", child="Ali Khan")
        await record(api_client, type_id=type_id, number="BC/2020/2", child="Sara Ahmed")

        found = await api_client.get(f"{SEARCH}?field=child_full_name&field_value=Sara Ahmed")
        assert found.status_code == 200, found.text
        body = found.json()
        assert body["match"] == MatchKind.FIELD_VALUE.value
        assert [hit["certificate"]["certificate_number"] for hit in body["items"]] == ["BC/2020/2"]

    async def test_a_field_search_does_not_fall_through_to_the_other_tiers(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Asked for a village called "42", answering with certificate 42 is worse
        than answering with nothing: the clerk would act on the wrong record."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id, number="42", child="Ali Khan")

        found = await api_client.get(f"{SEARCH}?field=permanent_address&field_value=42")
        assert found.json()["match"] == MatchKind.NONE.value
        assert found.json()["items"] == []

    async def test_a_prefix_is_offered_inside_a_category(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Only inside a scope. Unscoped it would scan every record the office holds."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id, number="BC/2020/9", child="Abdul Rehman")

        scoped = await api_client.get(
            f"{SEARCH}?field=child_full_name&field_value=Abdul&certificate_type_id={type_id}"
        )
        assert scoped.json()["match"] == MatchKind.FIELD_PREFIX.value
        assert len(scoped.json()["items"]) == 1

    async def test_a_prefix_is_not_offered_without_a_scope(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id, number="BC/2020/9", child="Abdul Rehman")

        unscoped = await api_client.get(f"{SEARCH}?field=child_full_name&field_value=Abdul")
        assert unscoped.json()["match"] == MatchKind.NONE.value

    async def test_an_unknown_field_name_finds_nothing_rather_than_failing(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """A column one batch defines and another does not is not an error."""
        await authenticate(api_client, operator_user)
        await record(api_client, type_id=await birth_type_id(api_client), number="BC/2020/1")

        found = await api_client.get(f"{SEARCH}?field=blood_group&field_value=O+")
        assert found.status_code == 200
        assert found.json()["items"] == []

    async def test_a_value_without_a_field_is_ignored_rather_than_guessed_at(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id, number="BC/2020/1", child="Ali Khan")

        found = await api_client.get(f"{SEARCH}?field_value=Ali Khan")
        # Falls back to the ordinary filtered browse, not to a field match.
        assert found.json()["match"] == MatchKind.FILTERED.value
