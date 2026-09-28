"""Declarative base, shared column types and mixins."""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from typing import ClassVar, TypeAlias

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB

# N811: SQLAlchemy exports the type as all-caps UUID; the alias is CamelCase
# because it names a type, not a constant.
from sqlalchemy.dialects.postgresql import UUID as PgUuid  # noqa: N811
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "JSONDict",
    "JSONList",
    "JSONValue",
    "TimestampMixin",
    "enum_column",
    "utcnow",
    "uuid_pk",
]

# JSON-serialisable values, typed without resorting to ``Any``.
#
# Two flavours, deliberately:
#
# ``JSONValue`` is the precise recursive shape, used by helpers that walk JSON
# (redaction, audit sanitising). SQLAlchemy cannot resolve a self-referential
# alias inside a ``Mapped[...]`` annotation, so it is never used on a column.
#
# ``JSONDict`` / ``JSONList`` are the flat, resolvable aliases used by ORM
# columns and registered in ``Base.type_annotation_map``. Their members are typed
# ``object`` rather than ``Any``: a reader must narrow before use, which is what
# keeps untyped dicts from crossing a module boundary.
JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = "JSONScalar | list[JSONValue] | dict[str, JSONValue]"
JSONDict: TypeAlias = dict[str, object]
JSONList: TypeAlias = list[object]


# Explicit, deterministic constraint names so Alembic can autogenerate reversible
# migrations and so ``ALTER ... DROP CONSTRAINT`` never has to guess.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Root of the ORM hierarchy."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[object, object]] = {
        dt.datetime: DateTime(timezone=True),
        uuid.UUID: PgUuid(as_uuid=True),
        JSONDict: JSONB,
        JSONList: JSONB,
    }

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


_CLOCK_LOCK = threading.Lock()
_last_timestamp = dt.datetime.min.replace(tzinfo=dt.UTC)
_TICK = dt.timedelta(microseconds=1)


def utcnow() -> dt.datetime:
    """Timezone-aware current time, strictly increasing within this process.

    Used as the Python-side default for ``created_at``. "Newest first" lists and
    keyset cursors order on it, and on Windows the system clock advances in
    ~15 ms steps: thousands of consecutive calls return the same value, so rows
    inserted in quick succession tied and came back in random order. Nudging a
    repeated reading forward by one microsecond keeps insertion order without
    moving any timestamp by a perceptible amount.
    """
    global _last_timestamp
    now = dt.datetime.now(dt.UTC)
    with _CLOCK_LOCK:
        if now <= _last_timestamp:
            now = _last_timestamp + _TICK
        _last_timestamp = now
    return now


def uuid_pk() -> Mapped[uuid.UUID]:
    """Primary key column: application-generated UUID4."""
    return mapped_column(
        PgUuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        sort_order=-100,
    )


def enum_column(enum_cls: type, *, length: int = 48) -> SAEnum:
    """Store a Python enum as a checked VARCHAR.

    ``native_enum=False`` keeps the values in a ``VARCHAR`` guarded by a CHECK
    constraint rather than a Postgres ``TYPE``. Adding a new member is then an
    ordinary constraint swap instead of an ``ALTER TYPE``, which cannot run inside
    a transaction on older servers.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the database."""

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        index=True,
        sort_order=100,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        sort_order=101,
    )
