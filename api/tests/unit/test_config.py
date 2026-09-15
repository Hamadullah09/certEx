"""Configuration validation tests.

The coherence checks exist to turn silent misconfiguration into a boot failure:
a soft task limit above the hard limit, an unencrypted cookie claiming
``SameSite=None``, or demo credentials seeded into production.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from certex.config import AppEnv, LLMProvider, S3ServerSideEncryption, Settings

pytestmark = pytest.mark.unit

STRONG_SECRET = "a" * 64


def build(**overrides: object) -> Settings:
    """Construct settings without reading the ambient environment or .env file."""
    base: dict[str, object] = {
        "app_env": AppEnv.LOCAL,
        "secret_key": STRONG_SECRET,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestParsing:
    def test_cors_origins_from_csv(self) -> None:
        settings = build(cors_origins="http://a.test, http://b.test ,")
        assert settings.cors_origins == ["http://a.test", "http://b.test"]

    def test_cors_origins_from_list(self) -> None:
        assert build(cors_origins=["http://a.test"]).cors_origins == ["http://a.test"]

    def test_empty_optional_strings_become_none(self) -> None:
        settings = build(cookie_domain="  ", s3_endpoint_url="", tesseract_cmd="")
        assert settings.cookie_domain is None
        assert settings.s3_endpoint_url is None
        assert settings.tesseract_cmd is None

    def test_ocr_language_list(self) -> None:
        assert build(ocr_languages="eng+urd+ara").ocr_language_list == ["eng", "urd", "ara"]

    def test_blank_llm_key_becomes_none(self) -> None:
        assert build(llm_api_key="  ").llm_api_key is None


class TestCoherence:
    def test_review_floor_above_auto_approve_rejected(self) -> None:
        with pytest.raises(ValidationError, match="CONFIDENCE_REVIEW_FLOOR"):
            build(confidence_review_floor=0.95, confidence_auto_approve=0.90)

    def test_equal_thresholds_allowed(self) -> None:
        assert build(confidence_review_floor=0.9, confidence_auto_approve=0.9)

    def test_soft_limit_must_precede_hard_limit(self) -> None:
        with pytest.raises(ValidationError, match="SOFT_TIME_LIMIT"):
            build(celery_task_soft_time_limit=1800, celery_task_time_limit=1800)

    def test_high_dpi_below_base_dpi_rejected(self) -> None:
        with pytest.raises(ValidationError, match="OCR_HIGH_DPI"):
            build(ocr_dpi=600, ocr_high_dpi=300)

    def test_samesite_none_requires_secure(self) -> None:
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            build(cookie_samesite="none", cookie_secure=False)
        assert build(cookie_samesite="none", cookie_secure=True)

    def test_kms_requires_key_id(self) -> None:
        with pytest.raises(ValidationError, match="S3_SSE_KMS_KEY_ID"):
            build(s3_sse=S3ServerSideEncryption.AWS_KMS)
        assert build(s3_sse=S3ServerSideEncryption.AWS_KMS, s3_sse_kms_key_id="key-1")

    def test_local_llm_requires_base_url(self) -> None:
        with pytest.raises(ValidationError, match="LLM_BASE_URL"):
            build(llm_provider=LLMProvider.LOCAL)
        assert build(llm_provider=LLMProvider.LOCAL, llm_base_url="http://localhost:11434/v1")

    def test_probabilities_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            build(confidence_auto_approve=1.5)
        with pytest.raises(ValidationError):
            build(llm_trigger_confidence=-0.1)


class TestProductionGuards:
    def _prod(self, **overrides: object) -> Settings:
        base: dict[str, object] = {
            "app_env": AppEnv.PRODUCTION,
            "secret_key": STRONG_SECRET,
            "cookie_secure": True,
            "seed_enabled": False,
            "db_echo": False,
        }
        base.update(overrides)
        return build(**base)

    def test_accepts_a_hardened_configuration(self) -> None:
        assert self._prod().is_production

    def test_rejects_default_secret(self) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            self._prod(secret_key="dev-only-insecure-change-me-000000000000000000000000")

    def test_rejects_short_secret(self) -> None:
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            self._prod(secret_key="tooshort")

    def test_requires_secure_cookies(self) -> None:
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            self._prod(cookie_secure=False, cookie_samesite="lax")

    def test_rejects_sql_echo(self) -> None:
        """Echoed statements carry bind parameters, which are field values."""
        with pytest.raises(ValidationError, match="DB_ECHO"):
            self._prod(db_echo=True)

    def test_rejects_seeded_demo_accounts(self) -> None:
        with pytest.raises(ValidationError, match="SEED_ENABLED"):
            self._prod(seed_enabled=True)

    def test_docs_hidden_in_production(self) -> None:
        assert not self._prod().docs_enabled
        assert build().docs_enabled


class TestEnvironmentSource:
    """Settings must parse from the real environment, not just from kwargs.

    Constructor arguments bypass pydantic-settings' env source, so a bug in how a
    complex field is decoded from a string is invisible to kwarg-based tests. This
    class exercises the path the containers actually take.
    """

    def test_cors_origins_parses_from_a_plain_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,https://records.example.com")
        monkeypatch.setenv("SECRET_KEY", STRONG_SECRET)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.cors_origins == [
            "http://localhost:3000",
            "https://records.example.com",
        ]

    def test_single_origin_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
        monkeypatch.setenv("SECRET_KEY", STRONG_SECRET)
        assert Settings(_env_file=None).cors_origins == ["http://localhost:3000"]  # type: ignore[call-arg]

    def test_json_array_still_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Operators who already set a JSON array should not be broken by the fix."""
        monkeypatch.setenv("CORS_ORIGINS", '["http://a.test","http://b.test"]')
        monkeypatch.setenv("SECRET_KEY", STRONG_SECRET)
        assert Settings(_env_file=None).cors_origins == ["http://a.test", "http://b.test"]  # type: ignore[call-arg]

    def test_numeric_and_bool_fields_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET_KEY", STRONG_SECRET)
        monkeypatch.setenv("OCR_DPI", "400")
        monkeypatch.setenv("LLM_ENABLED", "false")
        monkeypatch.setenv("CONFIDENCE_AUTO_APPROVE", "0.85")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.ocr_dpi == 400
        assert settings.llm_enabled is False
        assert settings.confidence_auto_approve == 0.85
