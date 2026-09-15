"""Password hashing and token issuance tests."""

from __future__ import annotations

import datetime as dt
import uuid

import jwt
import pytest

from certex.config import get_settings
from certex.core.security import (
    AccessTokenClaims,
    TokenExpiredError,
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from certex.enums import UserRole

pytestmark = pytest.mark.unit


class TestPasswordHashing:
    def test_round_trip(self) -> None:
        hashed = hash_password("s3cret-passphrase")
        assert verify_password("s3cret-passphrase", hashed)

    def test_rejects_wrong_password(self) -> None:
        assert not verify_password("wrong", hash_password("right"))

    def test_hash_is_salted(self) -> None:
        assert hash_password("same") != hash_password("same")

    def test_long_passwords_are_not_truncated(self) -> None:
        """bcrypt ignores bytes past 72; two long passwords sharing a prefix must
        not become interchangeable."""
        base = "x" * 72
        hashed = hash_password(base + "AAAA")
        assert verify_password(base + "AAAA", hashed)
        assert not verify_password(base + "BBBB", hashed)

    def test_unicode_passwords(self) -> None:
        secret = "پاسورد-۱۲۳"
        assert verify_password(secret, hash_password(secret))

    def test_malformed_stored_hash_returns_false(self) -> None:
        assert not verify_password("anything", "not-a-bcrypt-hash")
        assert not verify_password("anything", "")

    def test_empty_password_round_trips(self) -> None:
        assert verify_password("", hash_password(""))

    def test_needs_rehash_detects_lower_cost(self) -> None:
        weak = hash_password("pw", rounds=4)
        assert needs_rehash(weak, rounds=12)
        assert not needs_rehash(hash_password("pw", rounds=12), rounds=12)

    def test_needs_rehash_on_garbage(self) -> None:
        assert needs_rehash("garbage")


class TestAccessTokens:
    def test_round_trip(self) -> None:
        user_id, workspace_id = uuid.uuid4(), uuid.uuid4()
        token, expires_at = create_access_token(
            user_id=user_id, workspace_id=workspace_id, role=UserRole.OPERATOR
        )
        claims = decode_access_token(token)

        assert claims.sub == user_id
        assert claims.wsp == workspace_id
        assert claims.role is UserRole.OPERATOR
        assert claims.typ == "access"
        assert expires_at > dt.datetime.now(dt.UTC)
        assert claims.expires_at == expires_at.replace(microsecond=0)

    def test_expired_token_rejected(self) -> None:
        settings = get_settings()
        past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        token, _ = create_access_token(
            user_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            role=UserRole.VIEWER,
            settings=settings,
            now=past,
        )
        with pytest.raises(TokenExpiredError):
            decode_access_token(token)

    def test_tampered_signature_rejected(self) -> None:
        token, _ = create_access_token(
            user_id=uuid.uuid4(), workspace_id=uuid.uuid4(), role=UserRole.ADMIN
        )
        head, payload, _ = token.split(".")
        with pytest.raises(TokenInvalidError):
            decode_access_token(f"{head}.{payload}.deadbeef")

    def test_token_signed_with_another_key_rejected(self) -> None:
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "wsp": str(uuid.uuid4()),
                "role": "ADMIN",
                "jti": str(uuid.uuid4()),
                "iat": int(dt.datetime.now(dt.UTC).timestamp()),
                "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).timestamp()),
                "typ": "access",
            },
            "a-completely-different-signing-key",
            algorithm="HS256",
        )
        with pytest.raises(TokenInvalidError):
            decode_access_token(forged)

    def test_alg_none_rejected(self) -> None:
        """The classic JWT downgrade: an unsigned token must never authenticate."""
        unsigned = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "wsp": str(uuid.uuid4()),
                "role": "ADMIN",
                "jti": str(uuid.uuid4()),
                "iat": int(dt.datetime.now(dt.UTC).timestamp()),
                "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).timestamp()),
                "typ": "access",
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(TokenInvalidError):
            decode_access_token(unsigned)

    def test_non_access_token_type_rejected(self) -> None:
        settings = get_settings()
        now = dt.datetime.now(dt.UTC)
        refreshish = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "wsp": str(uuid.uuid4()),
                "role": "ADMIN",
                "jti": str(uuid.uuid4()),
                "iat": int(now.timestamp()),
                "exp": int((now + dt.timedelta(hours=1)).timestamp()),
                "typ": "refresh",
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(TokenInvalidError):
            decode_access_token(refreshish)

    def test_missing_required_claim_rejected(self) -> None:
        settings = get_settings()
        token = jwt.encode(
            {"iat": 1, "exp": 9_999_999_999, "typ": "access"},
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(TokenInvalidError):
            decode_access_token(token)

    def test_garbage_rejected(self) -> None:
        with pytest.raises(TokenInvalidError):
            decode_access_token("not-a-jwt-at-all")

    def test_claims_model_rejects_unknown_role(self) -> None:
        with pytest.raises(ValueError):
            AccessTokenClaims.model_validate(
                {
                    "sub": str(uuid.uuid4()),
                    "wsp": str(uuid.uuid4()),
                    "role": "SUPERUSER",
                    "jti": str(uuid.uuid4()),
                    "iat": 1,
                    "exp": 2,
                }
            )


class TestRefreshTokens:
    def test_generated_token_is_high_entropy(self) -> None:
        raw, digest = generate_refresh_token()
        assert len(raw) >= 48
        assert len(digest) == 64
        assert hash_refresh_token(raw) == digest

    def test_tokens_are_unique(self) -> None:
        tokens = {generate_refresh_token()[0] for _ in range(200)}
        assert len(tokens) == 200

    def test_hash_is_stable_and_distinct(self) -> None:
        assert hash_refresh_token("abc") == hash_refresh_token("abc")
        assert hash_refresh_token("abc") != hash_refresh_token("abd")


class TestRoleOrdering:
    def test_role_hierarchy(self) -> None:
        assert UserRole.ADMIN.satisfies(UserRole.VIEWER)
        assert UserRole.ADMIN.satisfies(UserRole.OPERATOR)
        assert UserRole.OPERATOR.satisfies(UserRole.VIEWER)
        assert not UserRole.VIEWER.satisfies(UserRole.OPERATOR)
        assert not UserRole.OPERATOR.satisfies(UserRole.ADMIN)
        assert UserRole.VIEWER.satisfies(UserRole.VIEWER)


class TestTimingEqualisation:
    """An unknown address must cost the same as a wrong password.

    Otherwise response latency becomes an account-enumeration oracle.
    """

    def test_dummy_hash_is_a_usable_bcrypt_hash(self) -> None:
        from certex.services.auth_service import _dummy_hash

        _dummy_hash.cache_clear()
        hashed = _dummy_hash()
        # A malformed hash would make verify_password fail fast instead of doing
        # the KDF work, which is precisely the leak this guards against.
        assert hashed.startswith("$2b$")
        assert verify_password("anything-at-all", hashed) is False

    def test_dummy_hash_uses_the_configured_work_factor(self) -> None:
        from certex.services.auth_service import _dummy_hash

        _dummy_hash.cache_clear()
        hashed = _dummy_hash()
        assert not needs_rehash(hashed, rounds=get_settings().password_bcrypt_rounds)

    def test_dummy_hash_is_computed_once(self) -> None:
        from certex.services.auth_service import _dummy_hash

        _dummy_hash.cache_clear()
        assert _dummy_hash() == _dummy_hash()
