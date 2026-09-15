"""PII redaction tests.

Acceptance criterion: "No PII appears anywhere in the application logs."

These tests treat that literally. The final test renders real log records through
the configured pipeline and asserts that a corpus of realistic certificate field
values - names in Latin and Urdu script, national ID numbers, addresses, dates -
does not appear anywhere in the emitted bytes.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from certex.config import LogFormat, get_settings
from certex.logging_setup import (
    MAX_LOGGED_STRING,
    REDACTED,
    SUPPRESSED_EVENT_PREFIX,
    PIIRedactionFilter,
    configure_logging,
    get_logger,
    redact_event,
    redact_event_message,
    redact_text,
    safe_error,
)

pytestmark = pytest.mark.unit


# Realistic values of the kind this system extracts. None may ever reach a sink.
PII_CORPUS = {
    "latin_name": "Fatima Zahra Siddiqui",
    "urdu_name": "فاطمہ زہرا",
    "cnic": "42101-1234567-8",
    "cnic_bare": "4210112345678",
    "email": "fatima.siddiqui@example.com",
    "address": "House 14, Street 7, Gulberg III, Lahore",
    "printed_date": "14/03/1987",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
}


class TestRedactText:
    def test_scrubs_national_id(self) -> None:
        assert "42101-1234567-8" not in redact_text(f"found {PII_CORPUS['cnic']} on page 2")

    def test_scrubs_bare_long_digit_runs(self) -> None:
        assert PII_CORPUS["cnic_bare"] not in redact_text(PII_CORPUS["cnic_bare"])

    def test_scrubs_email(self) -> None:
        assert "fatima.siddiqui" not in redact_text(PII_CORPUS["email"])

    def test_scrubs_jwt(self) -> None:
        result = redact_text(f"Authorization failed for {PII_CORPUS['jwt']}")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "[JWT]" in result

    def test_scrubs_bearer_header(self) -> None:
        assert "abcdefghijklmnop" not in redact_text("Bearer abcdefghijklmnop123")

    def test_scrubs_arabic_script(self) -> None:
        result = redact_text(f"name is {PII_CORPUS['urdu_name']}")
        assert PII_CORPUS["urdu_name"] not in result
        assert "[NON-LATIN]" in result

    def test_scrubs_printed_dates(self) -> None:
        assert "14/03/1987" not in redact_text("DOB 14/03/1987")

    def test_preserves_iso_timestamps(self) -> None:
        """Log timestamps and retention cutoffs are ISO and are not PII."""
        assert "2026-01-31" in redact_text("retention cutoff 2026-01-31")

    def test_scrubs_inline_credentials_in_urls(self) -> None:
        result = redact_text("postgresql://certex:hunter2@postgres:5432/certex")
        assert "hunter2" not in result
        assert "[CREDS]@" in result

    def test_caps_length(self) -> None:
        result = redact_text("a" * (MAX_LOGGED_STRING + 500))
        assert len(result) < MAX_LOGGED_STRING + 60
        assert "+500 chars" in result

    def test_leaves_ordinary_text_alone(self) -> None:
        message = "extracted 38 fields from unit 4 using template rules"
        assert redact_text(message) == message


class TestRedactEvent:
    def test_sensitive_keys_are_replaced_wholesale(self) -> None:
        for key in (
            "value",
            "old_value",
            "new_value",
            "raw_text",
            "source_snippet",
            "full_name",
            "id_number",
            "original_filename",
            "password",
            "api_key",
            "authorization",
            "extra_fields",
        ):
            result = redact_event(None, "info", {"event": "x", key: PII_CORPUS["latin_name"]})
            assert result[key] == REDACTED, f"{key} was not redacted"

    def test_safe_keys_pass_through(self) -> None:
        event = {
            "event": "extraction.completed",
            "document_id": "9f1c2d3e-0000-0000-0000-000000000001",
            "field_name": "date_of_birth",
            "row_confidence": 0.93,
            "page_count": 12,
            "certificate_type": "BIRTH",
        }
        result = redact_event(None, "info", dict(event))
        assert result == event

    def test_unknown_key_with_pii_is_scrubbed(self) -> None:
        """A key nobody added to the allowlist must not leak by default."""
        result = redact_event(None, "info", {"event": "x", "some_new_field": PII_CORPUS["cnic"]})
        assert PII_CORPUS["cnic"] not in str(result["some_new_field"])

    def test_nested_structures_are_walked(self) -> None:
        result = redact_event(
            None,
            "info",
            {
                "event": "x",
                "context": {
                    "father_details": {"full_name": PII_CORPUS["latin_name"]},
                    "ids": [PII_CORPUS["cnic"], PII_CORPUS["cnic"]],
                },
            },
        )
        rendered = json.dumps(result, default=str)
        assert PII_CORPUS["latin_name"] not in rendered
        assert PII_CORPUS["cnic"] not in rendered

    def test_bytes_are_summarised_not_dumped(self) -> None:
        result = redact_event(None, "info", {"event": "x", "blob": b"scan-bytes-here"})
        assert result["blob"] == "[bytes:15]"

    def test_long_collections_are_truncated(self) -> None:
        result = redact_event(None, "info", {"event": "x", "items": list(range(100))})
        assert isinstance(result["items"], list)
        assert len(result["items"]) == 26
        assert result["items"][-1] == "[+75 more]"

    def test_event_message_is_scrubbed(self) -> None:
        result = redact_event(None, "info", {"event": f"could not parse {PII_CORPUS['cnic']}"})
        assert PII_CORPUS["cnic"] not in result["event"]

    def test_first_party_freetext_event_is_suppressed(self) -> None:
        """Pattern scrubbing cannot recognise an address, so prose is suppressed."""
        result = redact_event(
            None,
            "info",
            {
                "logger": "certex.pipeline.validate",
                "event": f"rejected record for {PII_CORPUS['address']}",
            },
        )
        assert result["event"].startswith(SUPPRESSED_EVENT_PREFIX)
        assert PII_CORPUS["address"] not in result["event"]

    def test_first_party_static_event_name_survives(self) -> None:
        result = redact_event(
            None,
            "info",
            {"logger": "certex.pipeline.validate", "event": "validation.rejected"},
        )
        assert result["event"] == "validation.rejected"

    def test_third_party_prose_is_kept_and_scrubbed(self) -> None:
        """We do not control library messages, so they keep prose but get scrubbed."""
        result = redact_event(
            None,
            "info",
            {"logger": "uvicorn.error", "event": f"connection from {PII_CORPUS['email']}"},
        )
        assert "connection from" in result["event"]
        assert PII_CORPUS["email"] not in result["event"]

    def test_arbitrary_object_is_not_stringified(self) -> None:
        class Leaky:
            def __str__(self) -> str:  # pragma: no cover - must never be called
                return PII_CORPUS["latin_name"]

        result = redact_event(None, "info", {"event": "x", "obj": Leaky()})
        assert result["obj"] == "[Leaky]"
        assert PII_CORPUS["latin_name"] not in str(result["obj"])


class TestStdlibFilter:
    def test_scrubs_record_message(self) -> None:
        record = logging.LogRecord(
            name="sqlalchemy.engine",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=f"INSERT INTO extractions VALUES ('{PII_CORPUS['cnic']}')",
            args=(),
            exc_info=None,
        )
        assert PIIRedactionFilter().filter(record) is True
        assert PII_CORPUS["cnic"] not in record.getMessage()

    def test_scrubs_record_args(self) -> None:
        record = logging.LogRecord(
            name="sqlalchemy.engine",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="params %s",
            args=(PII_CORPUS["email"],),
            exc_info=None,
        )
        PIIRedactionFilter().filter(record)
        assert PII_CORPUS["email"] not in record.getMessage()


class TestEventMessagePolicy:
    @pytest.mark.parametrize(
        "name",
        [
            "extraction.completed",
            "http.request",
            "auth.refresh_token_reuse",
            "worker.ready",
            "Starting",
        ],
    )
    def test_conforming_names_pass(self, name: str) -> None:
        assert redact_event_message(name, first_party=True) == name

    @pytest.mark.parametrize(
        "message",
        [
            "processed 4 pages",
            "could not read file Ahmed Khan.pdf",
            "validation failed for House 14, Street 7, Lahore",
        ],
    )
    def test_messages_with_whitespace_are_suppressed(self, message: str) -> None:
        result = redact_event_message(message, first_party=True)
        assert result.startswith(SUPPRESSED_EVENT_PREFIX)

    def test_suppression_digest_is_stable(self) -> None:
        """The same call site correlates across records without leaking content."""
        first = redact_event_message("failed for X", first_party=True)
        second = redact_event_message("failed for X", first_party=True)
        assert first == second
        assert redact_event_message("failed for Y", first_party=True) != first


class TestSafeError:
    def test_includes_class_and_scrubs_message(self) -> None:
        rendered = safe_error(ValueError(f"bad CNIC {PII_CORPUS['cnic']}"))
        assert rendered.startswith("ValueError:")
        assert PII_CORPUS["cnic"] not in rendered


@pytest.fixture
def captured_logs() -> Iterator[io.StringIO]:
    """Reconfigure logging to JSON into a buffer, then restore."""
    settings = get_settings().model_copy(update={"log_format": LogFormat.JSON})
    configure_logging(settings, force=True)

    buffer = io.StringIO()
    root = logging.getLogger()
    original = list(root.handlers)
    formatter = original[0].formatter if original else None

    handler = logging.StreamHandler(buffer)
    if formatter is not None:
        handler.setFormatter(formatter)
    handler.addFilter(PIIRedactionFilter())
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)

    try:
        yield buffer
    finally:
        root.handlers = original
        structlog.contextvars.clear_contextvars()
        configure_logging(get_settings(), force=True)


class TestEndToEnd:
    """The acceptance criterion, asserted against real rendered output."""

    def test_no_pii_reaches_the_sink(self, captured_logs: io.StringIO) -> None:
        logger = get_logger("certex.test")

        logger.info(
            "extraction.completed",
            document_id="1f0d9e2a-0000-4000-8000-000000000001",
            field_name="father_full_name",
            value=PII_CORPUS["latin_name"],
            new_value=PII_CORPUS["urdu_name"],
            id_number=PII_CORPUS["cnic"],
            original_filename="Fatima Zahra Siddiqui - birth.pdf",
            row_confidence=0.91,
        )
        logger.warning(
            f"validation failed for {PII_CORPUS['cnic']} at {PII_CORPUS['address']}",
            source_snippet=PII_CORPUS["address"],
        )
        logger.info(
            "llm.request",
            prompt=f"Extract fields from: {PII_CORPUS['latin_name']}",
            model="claude-sonnet-4-5",
        )

        try:
            raise ValueError(f"unparseable date {PII_CORPUS['printed_date']}")
        except ValueError:
            logger.exception("pipeline.stage_failed", stage="validate")

        output = captured_logs.getvalue()
        assert output, "expected log output to be captured"

        for label, value in PII_CORPUS.items():
            assert value not in output, f"{label} leaked into the logs: {value!r}"

        # The non-PII telemetry must survive, or the redaction is useless.
        assert "extraction.completed" in output
        assert "father_full_name" in output
        assert "claude-sonnet-4-5" in output
        assert "1f0d9e2a-0000-4000-8000-000000000001" in output

    def test_exception_tracebacks_are_scrubbed(self, captured_logs: io.StringIO) -> None:
        logger = get_logger("certex.test")
        try:
            raise RuntimeError(f"OCR failed on {PII_CORPUS['urdu_name']}")
        except RuntimeError:
            logger.exception("ocr.failed", page_number=3)

        output = captured_logs.getvalue()
        assert PII_CORPUS["urdu_name"] not in output
        assert "ocr.failed" in output
