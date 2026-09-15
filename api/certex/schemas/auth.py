"""Authentication request and response schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from certex.enums import UserRole

__all__ = [
    "LoginRequest",
    "SessionResponse",
    "UserProfile",
    "WorkspaceSummary",
]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class WorkspaceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: UserRole
    workspace_id: uuid.UUID
    is_active: bool
    last_login_at: dt.datetime | None = None


class SessionResponse(BaseModel):
    """Returned by login and refresh.

    The tokens themselves travel in httpOnly cookies and are deliberately absent
    from the body, so a stray console log or error report cannot capture them.
    ``access_expires_at`` lets the client schedule a refresh before expiry.
    """

    model_config = ConfigDict(extra="forbid")

    user: UserProfile
    workspace: WorkspaceSummary
    access_expires_at: dt.datetime
    csrf_token: str = Field(description="Echo this in the X-CertEx-CSRF header on unsafe requests.")
