"""Request and response models for the people in a workspace.

A password only ever travels inbound. No response model here carries one, carries a
hash, or carries anything an attacker could use to tell a real account from an absent
one - which is also why the forgotten-password request answers the same way whatever
address it is given.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from certex.enums import UserRole
from certex.services.user_service import MIN_PASSWORD_LENGTH

__all__ = [
    "PasswordChange",
    "PasswordResetRequest",
    "PasswordSet",
    "UserCreate",
    "UserSummary",
    "UserUpdate",
]


class UserSummary(BaseModel):
    """One person in this office."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: UserRole
    is_active: bool
    last_login_at: dt.datetime | None = None
    password_reset_requested_at: dt.datetime | None = Field(
        default=None,
        description=(
            "Set when this person said they had forgotten their password. An "
            "administrator sees it here and sets a new one; there is no mail server "
            "to send a link with."
        ),
    )
    created_at: dt.datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    role: UserRole = UserRole.OPERATOR
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class UserUpdate(BaseModel):
    """Only the fields present are changed; the rest are left alone."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=200)
    role: UserRole | None = None
    is_active: bool | None = None


class PasswordSet(BaseModel):
    """An administrator setting somebody else's password."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class PasswordChange(BaseModel):
    """Changing your own, which needs the current one."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().lower()
