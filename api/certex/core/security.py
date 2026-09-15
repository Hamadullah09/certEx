"""Password hashing and token issuance.

Access tokens are short-lived signed JWTs carrying the workspace and role, so an
authenticated request needs no database round-trip to authorise.

Refresh tokens are **opaque random strings**, not JWTs. Only their SHA-256 digest
is stored, so a database disclosure does not yield usable tokens, and each one can
be individually consumed - which is what makes rotation and replay detection
possible at all.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from typing import Literal

import bcrypt
import jwt
from pydantic import BaseModel, ConfigDict, Field

from certex.config import Settings, get_settings
from certex.core.errors import AppError, ErrorCode
from certex.enums import UserRole

__all__ = [
    "AccessTokenClaims",
    "TokenExpiredError",
    "TokenInvalidError",
    "constant_time_equals",
    "create_access_token",
    "decode_access_token",
    "generate_refresh_token",
    "hash_password",
    "hash_refresh_token",
    "needs_rehash",
    "verify_password",
]

_BCRYPT_MAX_BYTES = 72


class TokenExpiredError(AppError):
    status = 401
    code = ErrorCode.TOKEN_EXPIRED
    title = "Session expired"
    remediation = "Your session timed out. Sign in again."


class TokenInvalidError(AppError):
    status = 401
    code = ErrorCode.TOKEN_INVALID
    title = "Invalid token"
    remediation = "Sign in again to obtain a fresh session."


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def _prepare_password(plain: str) -> bytes:
    """Fold the password to a fixed length before bcrypt.

    bcrypt silently ignores everything past 72 bytes, which would make two long
    passwords sharing a prefix interchangeable. Digesting first removes the limit
    without truncating; base64 keeps the digest free of NUL bytes, which bcrypt
    also treats as a terminator.
    """
    raw = plain.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    return base64.b64encode(hashlib.sha256(raw).digest())


def hash_password(plain: str, *, rounds: int | None = None) -> str:
    settings = get_settings()
    cost = rounds if rounds is not None else settings.password_bcrypt_rounds
    salt = bcrypt.gensalt(rounds=cost)
    return bcrypt.hashpw(_prepare_password(plain), salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password check. Never raises on a malformed stored hash."""
    try:
        return bcrypt.checkpw(_prepare_password(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed: str, *, rounds: int | None = None) -> bool:
    """True when a stored hash uses a weaker cost than currently configured."""
    settings = get_settings()
    target = rounds if rounds is not None else settings.password_bcrypt_rounds
    try:
        cost = int(hashed.split("$")[2])
    except (IndexError, ValueError):
        return True
    return cost < target


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------
class AccessTokenClaims(BaseModel):
    """Decoded access-token payload."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    sub: uuid.UUID = Field(description="User id.")
    wsp: uuid.UUID = Field(description="Workspace id. Scopes every query.")
    role: UserRole
    jti: uuid.UUID
    iat: int
    exp: int
    typ: Literal["access"] = "access"

    @property
    def issued_at(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.iat, tz=dt.UTC)

    @property
    def expires_at(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.exp, tz=dt.UTC)


def create_access_token(
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: UserRole,
    settings: Settings | None = None,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    """Return ``(encoded_jwt, expires_at)``."""
    active = settings or get_settings()
    issued = now or dt.datetime.now(dt.UTC)
    expires = issued + dt.timedelta(seconds=active.access_token_ttl_seconds)
    payload: dict[str, str | int] = {
        "sub": str(user_id),
        "wsp": str(workspace_id),
        "role": role.value,
        "jti": str(uuid.uuid4()),
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "typ": "access",
    }
    token = jwt.encode(
        payload,
        active.secret_key.get_secret_value(),
        algorithm=active.jwt_algorithm,
    )
    return token, expires


def decode_access_token(token: str, *, settings: Settings | None = None) -> AccessTokenClaims:
    """Verify and parse an access token.

    Raises :class:`TokenExpiredError` or :class:`TokenInvalidError`; never returns
    a partially trusted payload.
    """
    active = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            active.secret_key.get_secret_value(),
            algorithms=[active.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("The access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError("The access token could not be verified.") from exc

    if payload.get("typ") != "access":
        # A refresh token presented as a bearer credential must not authenticate.
        raise TokenInvalidError("Token is not an access token.")

    try:
        return AccessTokenClaims.model_validate(payload)
    except ValueError as exc:
        raise TokenInvalidError("The access token payload is malformed.") from exc


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------
def generate_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, sha256_hex)``.

    The raw value goes to the client in an httpOnly cookie and is never persisted.
    """
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    """SHA-256 of a refresh token.

    A plain digest is correct here, unlike for passwords: the token is 48 bytes of
    CSPRNG output, so there is no guessable keyspace for a work factor to defend.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
