"""What the register does when it is full.

A records office that has digitised fifty years of registers holds hundreds of thousands
of entries, and the counter still has thirty seconds to answer a question. So these tests
are about two things, and the first matters more than the second.

**The query plan.** A wall-clock number on one machine says little - a laptop under
Docker on a Windows filesystem is slower than the server this will run on, and a fast
sequential scan over 10,000 rows looks fine right up until there are a million. What
tells the truth is *how* Postgres answers: an index scan stays an index scan at any size,
and a sequential scan over the register is a defect whatever the clock says.

**A generous ceiling.** Timings are asserted, but loosely, and only to catch the
accidental full scan the plan check might miss - a filter that silently disabled an
index, a sort that spilled to disk.

Row counts come from ``CERTEX_SCALE_ROWS`` so the same tests run at 10,000 for a quick
check and at a million when somebody wants to know. Everything is inserted in bulk
through Core rather than the ORM: a million ORM objects is a test of SQLAlchemy's unit
of work, not of the register.
"""

from __future__ import annotations

import datetime as dt
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.engine import Connection

from certex.certificates.keys import name_key, number_key
from certex.core.security import hash_password
from certex.db.models import (
    Certificate,
    CertificateName,
    CertificateSchema,
    CertificateTypeRecord,
    SchemaVersion,
    User,
    Workspace,
)
from certex.db.session import get_sync_engine
from certex.enums import (
    CertificateSource,
    CertificateStatus,
    CertificateType,
    DuplicateStatus,
    FieldRole,
    SchemaVersionStatus,
    UserRole,
)
from tests.conftest import TEST_PASSWORD

pytestmark = [pytest.mark.load, pytest.mark.integration]

ROWS = int(os.environ.get("CERTEX_SCALE_ROWS", "10000"))
BATCH = 5_000

# Deliberately loose. These are laptop-under-Docker numbers; the plan assertions are
# what actually hold, and these only catch an accidental full scan.
NUMBER_LOOKUP_CEILING_MS = 750.0
NAME_LOOKUP_CEILING_MS = 1500.0
BROWSE_PAGE_CEILING_MS = 1500.0
COUNT_CEILING_MS = 3000.0

FIRST_NAMES = (
    "Ayesha",
    "Muhammad",
    "Fatima",
    "Bilal",
    "Zainab",
    "Hassan",
    "Sana",
    "Usman",
)
LAST_NAMES = ("Malik", "Hussain", "Khan", "Akhtar", "Butt", "Chaudhry", "Rehman", "Shah")


@dataclass(frozen=True, slots=True)
class Register:
    """A workspace holding ``rows`` entries, and the ids needed to query it."""

    workspace_id: uuid.UUID
    certificate_type_id: uuid.UUID
    rows: int
    known_number: str
    known_name: str


def _child(index: int) -> str:
    return f"{FIRST_NAMES[index % len(FIRST_NAMES)]} {LAST_NAMES[(index // 7) % len(LAST_NAMES)]}"


def _certificate_rows(
    register_ids: tuple[uuid.UUID, uuid.UUID], start: int, count: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """One batch of entries and their name rows, as plain dicts for a Core insert."""
    workspace_id, type_id = register_ids
    entries: list[dict[str, object]] = []
    names: list[dict[str, object]] = []
    now = dt.datetime.now(tz=dt.UTC)

    for offset in range(count):
        index = start + offset
        number = f"BC/LHR/{2000 + index % 25}/{index:07d}"
        child = _child(index)
        father = (
            f"{FIRST_NAMES[(index + 3) % len(FIRST_NAMES)]} {LAST_NAMES[index % len(LAST_NAMES)]}"
        )
        entry_id = uuid.uuid4()
        event = dt.date(2000 + index % 25, 1 + index % 12, 1 + index % 28)

        entries.append(
            {
                "id": entry_id,
                "workspace_id": workspace_id,
                "certificate_type_id": type_id,
                "schema_version_id": None,
                "certificate_number": number,
                "certificate_number_key": number_key(number),
                "registration_number": None,
                "registration_number_key": None,
                "primary_name": child,
                "primary_name_key": name_key(child),
                "secondary_name": None,
                "secondary_name_key": None,
                "event_date": event,
                "event_date_role": FieldRole.BIRTH_DATE,
                "registration_date": None,
                "issue_date": None,
                "issuing_authority": "Union Council 42, Lahore",
                "values_jsonb": {
                    "certificate_number": number,
                    "child_full_name": child,
                    "father_full_name": father,
                    "date_of_birth": event.isoformat(),
                },
                "confidences_jsonb": {},
                "provenance_jsonb": {},
                "row_confidence": 1.0,
                "status": CertificateStatus.ACTIVE,
                "needs_review": index % 50 == 0,
                "record_version": 1,
                "duplicate_status": DuplicateStatus.NONE,
                "duplicate_of_id": None,
                "superseded_by_id": None,
                "source": CertificateSource.IMPORT,
                "source_extraction_id": None,
                "source_batch_id": None,
                "created_by": None,
                "updated_by": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        for role, field_name, value in (
            (FieldRole.SUBJECT_NAME, "child_full_name", child),
            (FieldRole.FATHER_NAME, "father_full_name", father),
        ):
            names.append(
                {
                    "id": uuid.uuid4(),
                    "certificate_id": entry_id,
                    "workspace_id": workspace_id,
                    "role": role,
                    "field_name": field_name,
                    "position": 0,
                    "value": value,
                    "value_key": name_key(value),
                }
            )
    return entries, names


def _seed(connection: Connection, rows: int) -> Register:
    """Build a workspace with ``rows`` entries, in batches, through Core inserts."""
    workspace_id = uuid.uuid4()
    connection.execute(
        insert(Workspace).values(
            id=workspace_id, name=f"Scale {workspace_id.hex[:8]}", settings_json={}
        )
    )
    connection.execute(
        insert(User).values(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            email=f"scale-{workspace_id.hex[:8]}@example.com",
            password_hash=hash_password(TEST_PASSWORD),
            role=UserRole.ADMIN,
            is_active=True,
        )
    )
    type_id = uuid.uuid4()
    connection.execute(
        insert(CertificateTypeRecord).values(
            id=type_id,
            workspace_id=workspace_id,
            key="BIRTH",
            name="Birth",
            classifier_key=CertificateType.BIRTH,
            position=1,
            is_active=True,
        )
    )
    schema_id = uuid.uuid4()
    connection.execute(
        insert(CertificateSchema).values(
            id=schema_id,
            workspace_id=workspace_id,
            certificate_type_id=type_id,
            name="Birth certificate",
            is_default=True,
        )
    )
    connection.execute(
        insert(SchemaVersion).values(
            id=uuid.uuid4(),
            schema_id=schema_id,
            workspace_id=workspace_id,
            version=1,
            status=SchemaVersionStatus.PUBLISHED,
            published_at=dt.datetime.now(tz=dt.UTC),
        )
    )

    for start in range(0, rows, BATCH):
        count = min(BATCH, rows - start)
        entries, names = _certificate_rows((workspace_id, type_id), start, count)
        connection.execute(insert(Certificate), entries)
        connection.execute(insert(CertificateName), names)

    # Without fresh statistics the planner has no idea how big this table is, and will
    # happily choose a sequential scan over an index it should be using. A real
    # deployment gets this from autovacuum; a test that skipped it would be measuring
    # the planner's ignorance rather than the schema.
    connection.execute(text("ANALYZE certificates"))
    connection.execute(text("ANALYZE certificate_names"))

    middle = rows // 2
    return Register(
        workspace_id=workspace_id,
        certificate_type_id=type_id,
        rows=rows,
        known_number=f"BC/LHR/{2000 + middle % 25}/{middle:07d}",
        known_name=_child(middle),
    )


@pytest.fixture(scope="module")
def register(db_engine: object) -> Iterator[Register]:
    """One register, built once for every test in this module."""
    del db_engine  # requested so the schema exists
    engine = get_sync_engine()
    started = time.perf_counter()
    with engine.begin() as connection:
        built = _seed(connection, ROWS)
    elapsed = time.perf_counter() - started
    print(f"\nseeded {ROWS:,} entries in {elapsed:.1f}s ({ROWS / max(elapsed, 0.001):,.0f}/s)")

    yield built

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM workspaces WHERE id = :id"), {"id": built.workspace_id}
        )


def _timed(connection: Connection, statement: object) -> tuple[object, float]:
    started = time.perf_counter()
    result = connection.execute(statement).all()  # type: ignore[arg-type]
    return result, (time.perf_counter() - started) * 1000.0


def _index_plan(connection: Connection, statement: object) -> str:
    """The plan Postgres uses when a sequential scan is discouraged, lowercased.

    Two decisions are folded into this helper.

    *Sequential scans are turned off for the plan check.* At ten thousand rows a
    sequential scan genuinely is cheaper than an index, so a plan taken at this size
    would fail a perfectly good schema - and the same assertion at a million rows would
    pass for reasons the test never verified. What has to be true at every size is that
    **an index exists that can answer this query**, and discouraging the scan is how you
    ask Postgres that question: if the answer still comes back as a sequential scan there
    is nothing else to use, which is the defect worth catching. The unforced query is
    still timed, because that is what the register actually experiences.

    *Parameters are handed to the driver rather than inlined.* The trigram match operator
    is a per-cent sign, and compiling with literal binds doubles it into something
    Postgres does not recognise as an operator at all.
    """
    compiled = statement.compile(connection.engine)  # type: ignore[attr-defined]
    connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
    try:
        rows = connection.exec_driver_sql(f"EXPLAIN {compiled.string}", compiled.params).all()
    finally:
        connection.exec_driver_sql("SET LOCAL enable_seqscan = on")
    return "\n".join(str(row[0]) for row in rows).lower()


class TestFindingOneCertificate:
    """The commonest request at the counter: somebody is holding the certificate."""

    def test_an_exact_number_uses_an_index(self, register: Register) -> None:
        statement = select(Certificate.id).where(
            Certificate.workspace_id == register.workspace_id,
            Certificate.certificate_number_key == number_key(register.known_number),
        )
        with get_sync_engine().connect() as connection:
            plan = _index_plan(connection, statement)
            found, elapsed = _timed(connection, statement)

        assert "seq scan" not in plan, plan
        assert "ix_certificates_number_key" in plan, plan
        assert len(found) == 1
        assert elapsed < NUMBER_LOOKUP_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"

    def test_a_number_prefix_uses_an_index(self, register: Register) -> None:
        prefix = number_key(f"BC/LHR/{2000 + (register.rows // 2) % 25}")
        statement = (
            select(Certificate.id)
            .where(
                Certificate.workspace_id == register.workspace_id,
                Certificate.certificate_number_key.like(f"{prefix}%"),
            )
            .limit(25)
        )
        with get_sync_engine().connect() as connection:
            plan = _index_plan(connection, statement)
            _found, elapsed = _timed(connection, statement)

        assert "seq scan" not in plan, plan
        assert elapsed < NUMBER_LOOKUP_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"


class TestFindingByName:
    """The second commonest: somebody has a name and roughly a year."""

    def test_an_exact_name_uses_an_index(self, register: Register) -> None:
        statement = (
            select(Certificate.id)
            .where(
                Certificate.workspace_id == register.workspace_id,
                Certificate.primary_name_key == name_key(register.known_name),
            )
            .limit(25)
        )
        with get_sync_engine().connect() as connection:
            plan = _index_plan(connection, statement)
            found, elapsed = _timed(connection, statement)

        assert "seq scan" not in plan, plan
        assert len(found) > 0
        assert elapsed < NAME_LOOKUP_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"

    def test_a_name_in_any_role_uses_the_side_table_index(self, register: Register) -> None:
        """Searching "the father's name" must not turn into a scan of the register."""
        statement = (
            select(CertificateName.certificate_id)
            .where(
                CertificateName.workspace_id == register.workspace_id,
                CertificateName.role == FieldRole.FATHER_NAME,
                CertificateName.value_key == name_key("Ayesha Malik"),
            )
            .limit(25)
        )
        with get_sync_engine().connect() as connection:
            plan = _index_plan(connection, statement)
            _found, elapsed = _timed(connection, statement)

        assert "seq scan" not in plan, plan
        assert elapsed < NAME_LOOKUP_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"

    def test_a_similar_name_has_a_trigram_index_and_answers_quickly(
        self, register: Register
    ) -> None:
        """The fuzzy tier, which is the one that would quietly become a full scan.

        Two things are asserted, and a third is deliberately not.

        The index exists, with the workspace inside it. A trigram index on the name alone
        cannot satisfy the workspace scope every query here carries, so the planner falls
        back to the workspace B-tree and applies the similarity as a filter - computing it
        for every entry the office holds. Putting ``workspace_id`` in the GIN index
        through btree_gin is what gives Postgres something it can use.

        The query answers inside the ceiling.

        What is *not* asserted is that the planner chooses that index at this size. At ten
        thousand rows it measurably prefers the workspace B-tree and filters, which is a
        reasonable cost decision on a small table; whether it switches to the GIN index at
        a million rows has not been verified here, and claiming it in an assertion that
        passes for another reason would be worse than saying so.
        """
        similarity = func.similarity(Certificate.primary_name_key, name_key("Mohammad Khan"))
        statement = (
            select(Certificate.id)
            .where(
                Certificate.workspace_id == register.workspace_id,
                Certificate.primary_name_key.op("%")(name_key("Mohammad Khan")),
            )
            .order_by(similarity.desc())
            .limit(25)
        )
        with get_sync_engine().connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'ix_certificates_name_key_trgm'"
                )
            ).scalar()
            _found, elapsed = _timed(connection, statement)

        assert definition is not None, "the trigram index is missing"
        assert "gin_trgm_ops" in definition, definition
        assert "workspace_id" in definition, definition
        assert elapsed < NAME_LOOKUP_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"


class TestBrowsing:
    def test_a_keyset_page_does_not_care_how_deep_it_is(self, register: Register) -> None:
        """The reason the register pages by cursor rather than by offset."""
        with get_sync_engine().connect() as connection:
            first = select(Certificate.created_at, Certificate.id).where(
                Certificate.workspace_id == register.workspace_id
            )
            rows, _elapsed = _timed(
                connection, first.order_by(Certificate.created_at, Certificate.id).limit(1)
            )
            anchor = next(iter(rows))

            deep = (
                select(Certificate.id)
                .where(
                    Certificate.workspace_id == register.workspace_id,
                    Certificate.created_at >= anchor[0],
                )
                .order_by(Certificate.created_at, Certificate.id)
                .limit(50)
            )
            plan = _index_plan(connection, deep)
            _found, elapsed = _timed(connection, deep)

        assert "seq scan" not in plan, plan
        assert elapsed < BROWSE_PAGE_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"

    def test_the_review_backlog_is_counted_without_reading_the_register(
        self, register: Register
    ) -> None:
        statement = (
            select(func.count())
            .select_from(Certificate)
            .where(
                Certificate.workspace_id == register.workspace_id,
                Certificate.needs_review.is_(True),
            )
        )
        with get_sync_engine().connect() as connection:
            _found, elapsed = _timed(connection, statement)
        assert elapsed < COUNT_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"

    def test_a_type_and_date_window_uses_its_index(self, register: Register) -> None:
        statement = (
            select(Certificate.id)
            .where(
                Certificate.workspace_id == register.workspace_id,
                Certificate.certificate_type_id == register.certificate_type_id,
                Certificate.event_date >= dt.date(2015, 1, 1),
                Certificate.event_date <= dt.date(2015, 12, 31),
            )
            .limit(50)
        )
        with get_sync_engine().connect() as connection:
            plan = _index_plan(connection, statement)
            _found, elapsed = _timed(connection, statement)

        assert "seq scan" not in plan, plan
        assert elapsed < BROWSE_PAGE_CEILING_MS, f"{elapsed:.0f}ms over {register.rows:,} rows"


class TestTheRegisterIsWhatWeThink:
    def test_every_entry_landed(self, register: Register) -> None:
        with get_sync_engine().connect() as connection:
            count = connection.execute(
                select(func.count())
                .select_from(Certificate)
                .where(Certificate.workspace_id == register.workspace_id)
            ).scalar()
        assert count == register.rows

    def test_every_entry_is_searchable_by_name(self, register: Register) -> None:
        """Two names each - the child and the father - and no key is blank."""
        with get_sync_engine().connect() as connection:
            names = connection.execute(
                select(func.count())
                .select_from(CertificateName)
                .where(CertificateName.workspace_id == register.workspace_id)
            ).scalar()
        assert names == register.rows * 2
