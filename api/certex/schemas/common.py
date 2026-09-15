"""Shared response envelopes and cursor pagination.

Offset pagination is wrong for this data: a batch is still being written while an
operator pages through it, so ``OFFSET 40`` silently skips or repeats rows as
earlier rows are inserted. Every list endpoint therefore uses a keyset cursor over
a stable, unique sort key.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import enum
import json
import uuid
from typing import Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from certex.core.errors import BadRequestError

__all__ = [
    "Cursor",
    "Page",
    "PageMeta",
    "SortDirection",
]

T = TypeVar("T")


class SortDirection(str, enum.Enum):
    ASC = "asc"
    DESC = "desc"


class Cursor(BaseModel):
    """Opaque keyset position.

    Encodes ``(created_at, id)`` - a timestamp alone is not unique enough, since
    a bulk insert can share a microsecond, which would drop rows at a page
    boundary. Base64 of compact JSON, so the client treats it as opaque and the
    server can extend the payload without breaking old cursors.
    """

    model_config = ConfigDict(frozen=True)

    created_at: dt.datetime
    id: uuid.UUID

    def encode(self) -> str:
        payload = json.dumps(
            {"t": self.created_at.isoformat(), "i": str(self.id)},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, raw: str) -> Self:
        try:
            padded = raw + "=" * (-len(raw) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return cls(
                created_at=dt.datetime.fromisoformat(str(data["t"])),
                id=uuid.UUID(str(data["i"])),
            )
        except (KeyError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
            raise BadRequestError(
                "The pagination cursor is not valid.",
                title="Invalid cursor",
                remediation="Drop the cursor parameter to start from the first page.",
            ) from exc


class PageMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    next_cursor: str | None = Field(
        default=None, description="Pass as ?cursor= to fetch the following page."
    )
    has_more: bool
    limit: int
    total: int | None = Field(
        default=None,
        description=(
            "Total matching rows. Present only when the caller asked for it, "
            "because counting a multi-million-row workspace is not free."
        ),
    )


class Page(BaseModel, Generic[T]):
    """One page of results plus the cursor for the next."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    meta: PageMeta

    @classmethod
    def build(
        cls,
        items: list[T],
        *,
        limit: int,
        next_cursor: Cursor | None,
        total: int | None = None,
    ) -> Page[T]:
        return cls(
            items=items,
            meta=PageMeta(
                next_cursor=next_cursor.encode() if next_cursor else None,
                has_more=next_cursor is not None,
                limit=limit,
                total=total,
            ),
        )
