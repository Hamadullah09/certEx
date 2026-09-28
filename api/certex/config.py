"""Central configuration.

Every tunable in the system is declared here as a typed field so that the rest of
the codebase never reads ``os.environ`` directly. Values come from the process
environment, falling back to a ``.env`` file for local development.

The settings object is cached; call :func:`get_settings` rather than constructing
``Settings()`` so that a single instance is shared by the API process and by each
Celery worker process.
"""

from __future__ import annotations

import enum
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

__all__ = [
    "AppEnv",
    "LogFormat",
    "S3ServerSideEncryption",
    "Settings",
    "get_settings",
]

# Repository root: api/certex/config.py -> api/certex -> api -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]


class AppEnv(str, enum.Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(str, enum.Enum):
    JSON = "json"
    CONSOLE = "console"


class S3ServerSideEncryption(str, enum.Enum):
    NONE = "none"
    AES256 = "AES256"
    AWS_KMS = "aws:kms"


Probability = Annotated[float, Field(ge=0.0, le=1.0)]
"""A confidence or threshold value constrained to the closed interval [0, 1]."""

# Not a credential: the prefix the boot guard looks for to reject an unset key.
_INSECURE_SECRET_PREFIX = "dev-only-insecure"  # noqa: S105


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application ----------------------------------------------------------
    app_env: AppEnv = AppEnv.LOCAL
    app_name: str = "CertExtract"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    # NoDecode suppresses pydantic-settings' JSON decoding of complex types. Without
    # it, CORS_ORIGINS=http://localhost:3000 is fed to json.loads and raises before
    # any validator runs; the comma-splitting validator below does the parsing.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # -- Security / auth ------------------------------------------------------
    secret_key: SecretStr = SecretStr(f"{_INSECURE_SECRET_PREFIX}-change-me")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_seconds: int = Field(default=900, ge=60)
    refresh_token_ttl_seconds: int = Field(default=1_209_600, ge=300)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    password_bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # -- Database -------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://certex:certex@localhost:5432/certex"
    database_url_sync: str = "postgresql+psycopg://certex:certex@localhost:5432/certex"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)
    db_echo: bool = False

    # -- Redis / Celery -------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_worker_concurrency: int = Field(default=4, ge=1)
    celery_ocr_concurrency: int = Field(default=2, ge=1)
    celery_task_time_limit: int = Field(default=1800, ge=30)
    celery_task_soft_time_limit: int = Field(default=1500, ge=15)
    celery_task_max_retries: int = Field(default=3, ge=0)
    celery_retry_backoff_seconds: int = Field(default=5, ge=1)
    celery_max_tasks_per_child: int = Field(default=50, ge=1)

    # -- Object storage -------------------------------------------------------
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: SecretStr = SecretStr("minioadmin")
    s3_bucket: str = "certex-documents"
    s3_region: str = "us-east-1"
    s3_use_path_style: bool = True
    s3_sse: S3ServerSideEncryption = S3ServerSideEncryption.AES256
    s3_sse_kms_key_id: str | None = None
    s3_presign_ttl_seconds: int = Field(default=900, ge=30)

    # -- Upload limits --------------------------------------------------------
    max_file_bytes: int = Field(default=524_288_000, ge=1)
    max_batch_files: int = Field(default=2_000, ge=1)
    max_batch_bytes: int = Field(default=5_368_709_120, ge=1)
    upload_chunk_bytes: int = Field(default=8_388_608, ge=4096)
    upload_session_ttl_hours: int = Field(default=48, ge=1)
    max_zip_depth: int = Field(default=2, ge=0, le=8)
    max_zip_ratio: int = Field(default=120, ge=2)
    max_zip_uncompressed_bytes: int = Field(default=2_147_483_648, ge=1)

    # -- Text extraction / OCR ------------------------------------------------
    ocr_languages: str = "eng+urd"
    ocr_dpi: int = Field(default=300, ge=72, le=1200)
    ocr_high_dpi: int = Field(default=600, ge=72, le=1200)
    ocr_fallback_confidence: float = Field(default=60.0, ge=0.0, le=100.0)
    text_quality_min_chars: int = Field(default=100, ge=0)
    text_quality_min_dict_ratio: Probability = 0.35
    ocr_upscale_factor: float = Field(default=2.0, ge=1.0, le=8.0)
    ocr_upscale_width_threshold: int = Field(default=1200, ge=0)
    ocr_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "Deadline for one Tesseract run. Enforced in code because Celery's task "
            "time limits are not available on every platform the worker runs on."
        ),
    )
    ocr_cache_enabled: bool = True
    tesseract_cmd: str | None = None
    tessdata_prefix: str | None = Field(
        default=None,
        description=(
            "Directory holding *.traineddata. Unset means Tesseract's own default, "
            "which is correct in the container image."
        ),
    )
    soffice_cmd: str = "soffice"
    use_libmagic: bool | None = Field(
        default=None,
        description=(
            "Use python-magic for content sniffing. None auto-detects: enabled "
            "everywhere except Windows, where importing it without the native "
            "library hangs the process rather than raising."
        ),
    )

    # -- Confidence thresholds ------------------------------------------------
    confidence_auto_approve: Probability = 1.00
    """Row confidence at which a row is exported without anyone looking at it.

    The default of 1.00 means nobody is skipped: an automated reading is capped below
    1.0, so no row can clear it. An office that would rather spend its review time on
    the doubtful rows can lower this - 0.90 is the specification's suggestion."""

    confidence_review_floor: Probability = 0.60

    # -- Retention ------------------------------------------------------------
    retention_days: int = Field(default=90, ge=1)
    retention_sweep_hour: int = Field(default=3, ge=0, le=23)
    retention_enabled: bool = True

    # -- Rate limiting --------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_upload_per_minute: int = Field(default=120, ge=1)
    rate_limit_export_per_minute: int = Field(default=10, ge=1)
    rate_limit_login_per_minute: int = Field(default=10, ge=1)
    rate_limit_default_per_minute: int = Field(default=600, ge=1)

    # -- ClamAV ---------------------------------------------------------------
    clamav_enabled: bool = False
    clamav_host: str = "clamav"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=30.0, gt=0)

    # -- Seed -----------------------------------------------------------------
    seed_enabled: bool = True
    seed_workspace_name: str = "Demo Records Office"
    seed_admin_email: str = "admin@example.com"
    seed_admin_password: SecretStr = SecretStr("admin12345")
    seed_operator_email: str = "operator@example.com"
    seed_operator_password: SecretStr = SecretStr("operator12345")
    seed_viewer_email: str = "viewer@example.com"
    seed_viewer_password: SecretStr = SecretStr("viewer12345")

    # -- Server ---------------------------------------------------------------
    gunicorn_workers: int = Field(default=4, ge=1)
    gunicorn_timeout: int = Field(default=120, ge=10)
    api_port: int = Field(default=8000, ge=1, le=65535)
    web_port: int = Field(default=3000, ge=1, le=65535)

    # ------------------------------------------------------------------ hooks
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Parse a list field from an environment string.

        Accepts both conventions operators reach for - a comma-separated list and
        a JSON array - because ``NoDecode`` turns off pydantic-settings' built-in
        JSON handling for this field, and silently mangling a JSON array into
        ``['["a"', '"b"]']`` would be worse than the crash it replaced.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Value looks like a JSON array but could not be parsed. "
                    "Use either a JSON array or a comma-separated list."
                ) from exc
            if not isinstance(decoded, list):
                raise ValueError("Expected a JSON array or a comma-separated list.")
            return [str(item).strip() for item in decoded if str(item).strip()]

        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator(
        "cookie_domain",
        "s3_sse_kms_key_id",
        "tesseract_cmd",
        "tessdata_prefix",
        "use_libmagic",
        mode="before",
    )
    @classmethod
    def _empty_string_to_none(cls, value: object) -> object:
        """Treat an empty env var the same as an unset one.

        ``use_libmagic`` needs this most: it is ``bool | None``, and ``.env.example``
        ships it blank to mean "auto-detect", which pydantic would otherwise reject
        as an unparseable boolean and refuse to boot.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("s3_endpoint_url", mode="before")
    @classmethod
    def _blank_endpoint_means_real_aws(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_coherence(self) -> Settings:
        if self.confidence_review_floor > self.confidence_auto_approve:
            raise ValueError(
                "CONFIDENCE_REVIEW_FLOOR must be <= CONFIDENCE_AUTO_APPROVE "
                f"(got {self.confidence_review_floor} > {self.confidence_auto_approve})"
            )
        if self.celery_task_soft_time_limit >= self.celery_task_time_limit:
            raise ValueError(
                "CELERY_TASK_SOFT_TIME_LIMIT must be < CELERY_TASK_TIME_LIMIT so the "
                "soft signal arrives before the hard kill"
            )
        if self.ocr_high_dpi < self.ocr_dpi:
            raise ValueError("OCR_HIGH_DPI must be >= OCR_DPI")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        if self.s3_sse is S3ServerSideEncryption.AWS_KMS and not self.s3_sse_kms_key_id:
            raise ValueError("S3_SSE=aws:kms requires S3_SSE_KMS_KEY_ID")

        if self.app_env is AppEnv.PRODUCTION:
            secret = self.secret_key.get_secret_value()
            if secret.startswith(_INSECURE_SECRET_PREFIX) or len(secret) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a strong random value (>=32 chars) "
                    "when APP_ENV=production"
                )
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true when APP_ENV=production")
            if self.db_echo:
                raise ValueError("DB_ECHO must be false when APP_ENV=production")
            if self.seed_enabled:
                raise ValueError("SEED_ENABLED must be false when APP_ENV=production")
        return self

    # ------------------------------------------------------------- properties
    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def docs_enabled(self) -> bool:
        """Interactive API docs are hidden in production."""
        return self.app_env is not AppEnv.PRODUCTION

    @property
    def ocr_language_list(self) -> list[str]:
        """``eng+urd`` -> ``['eng', 'urd']``."""
        return [part for part in self.ocr_languages.split("+") if part]

    @property
    def repo_root(self) -> Path:
        return _REPO_ROOT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
