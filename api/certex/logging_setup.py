"""Structured logging with mandatory PII redaction.

This system processes birth, marriage and death certificates. Field *values* are
personal identity data and must never reach a log sink. Logging identifiers
(document id, unit id, field *name*) is fine and is what operators actually need
for debugging.

The redaction model is deliberately an **allowlist**, not a blocklist:

* A key in :data:`SAFE_KEYS` passes through verbatim. These are ids, counts,
  enum names, durations, engine names - nothing derived from document content.
* A key matching :data:`_SENSITIVE_KEY_RE` is replaced wholesale with
  :data:`REDACTED`.
* Every other string is pattern-scrubbed and length-capped.

The consequence is that code which invents a new key carrying document content
gets scrubbed by default instead of leaking until someone notices.

The ``event`` message gets a second rule, because pattern scrubbing cannot help
there: an address or a person's name has no regex shape, so
``logger.info(f"failed for {name} at {address}")`` would sail straight through.
Events emitted by **first-party** loggers must therefore be *static event names*
containing no whitespace - ``extraction.completed``, not a sentence. Anything
else is suppressed outright and replaced with a stable digest. Data belongs in
structured fields, where the allowlist governs it. Records from third-party
loggers keep their prose and are pattern-scrubbed, since we do not control them.

The same chain is applied to stdlib logging (uvicorn, sqlalchemy, celery, boto3)
so third-party records cannot bypass it.
"""

from __future__ import annotations

import hashlib
import logging
import logging.config
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from certex.config import LogFormat, Settings, get_settings

__all__ = [
    "MAX_LOGGED_STRING",
    "REDACTED",
    "SAFE_KEYS",
    "SUPPRESSED_EVENT_PREFIX",
    "PIIRedactionFilter",
    "bind_request_context",
    "clear_request_context",
    "configure_logging",
    "get_logger",
    "redact_event",
    "redact_event_message",
    "redact_text",
    "safe_error",
]

REDACTED: Final = "[REDACTED]"
SUPPRESSED_EVENT_PREFIX: Final = "[suppressed-freetext-event"
MAX_LOGGED_STRING: Final = 512

# ---------------------------------------------------------------------------
# Allowlist: keys whose values are structurally incapable of holding PII.
# ---------------------------------------------------------------------------
SAFE_KEYS: Final[frozenset[str]] = frozenset(
    {
        # structlog / stdlib machinery
        "event",  # scrubbed separately, never passed through raw
        "level",
        "levelname",
        "logger",
        "logger_name",
        "timestamp",
        "exc_info",
        "exception",
        "stack_info",
        "pathname",
        "lineno",
        "func_name",
        "process",
        "thread_name",
        # request context
        "request_id",
        "correlation_id",
        "trace_id",
        "method",
        "path",
        "route",
        "status_code",
        "duration_ms",
        "client_ip",
        "user_agent_family",
        # domain identifiers - UUIDs and enum-ish labels only
        "workspace_id",
        "user_id",
        "actor_id",
        "role",
        "batch_id",
        "document_id",
        "unit_id",
        "page_id",
        "extraction_id",
        "template_id",
        "export_id",
        "correction_id",
        "task_id",
        "task_name",
        "job_id",
        "retry",
        "attempt",
        "max_retries",
        # pipeline telemetry
        "stage",
        "status",
        "prior_status",
        "queue",
        "field_name",
        "field_names",
        "certificate_type",
        "review_status",
        "flag",
        "flags",
        "extraction_method",
        "boundary_method",
        "classification_method",
        "extraction_source",
        "ocr_engine",
        "psm",
        "language",
        "languages",
        "dpi",
        "page_number",
        "page_start",
        "page_end",
        "page_count",
        "unit_count",
        "file_count",
        "row_count",
        "column_count",
        "processed_count",
        "failed_count",
        "skipped_count",
        "duplicate_count",
        "byte_size",
        "bytes_written",
        "chunk_index",
        "chunk_total",
        "mime_type",
        "format",
        "schema_version",
        "template_fingerprint",
        "sha256",
        "storage_key",
        "cache_hit",
        "cache_key",
        "model",
        "provider",
        "input_tokens",
        "output_tokens",
        "confidence",
        "row_confidence",
        "type_confidence",
        "boundary_confidence",
        "mean_confidence",
        "ocr_mean_confidence",
        "threshold",
        "count",
        "total",
        "elapsed_ms",
        "error_code",
        "error_type",
        "reason_code",
        "rss_mb",
        "action",
        "entity_type",
        "entity_id",
        # The other side of a decision about two records - resolving a duplicate,
        # superseding an entry. An id, like every other key in this list.
        "related_id",
    }
)

# ---------------------------------------------------------------------------
# Explicit denylist: replaced wholesale, never pattern-scrubbed.
# ---------------------------------------------------------------------------
_SENSITIVE_KEY_RE: Final = re.compile(
    r"""(?ix)
    (?:^|_)(?:
          password | passwd | secret | token | api[_-]?key | authorization | cookie
        | credential | private[_-]?key | signature | session
    )(?:$|_)
    |
    (?:
          ^value$ | ^values$ | ^old_value$ | ^new_value$ | ^raw_value$
        | ^text$ | ^raw_text$ | ^ocr_text$ | ^page_text$ | ^body$ | ^content$
        | ^snippet$ | ^source_snippet$ | ^prompt$ | ^completion$ | ^response$
        | ^fields$ | ^field_values$ | ^payload$ | ^row$ | ^rows$ | ^record$
        | ^filename$ | ^original_filename$ | ^file_name$ | ^source_file_name$
        | ^email$ | ^address$ | ^full_name$ | ^cnic$ | ^id_number$ | ^remarks$
        | ^layout_blocks$ | ^extra_fields$ | ^lines$ | ^words$ | ^tokens$
    )
    """
)

# ---------------------------------------------------------------------------
# Defence-in-depth patterns applied to any remaining free text.
# ---------------------------------------------------------------------------
_SCRUBBERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # JWT / bearer-ish tokens
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), "[JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [TOKEN]"),
    # Email addresses
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    # Pakistani CNIC, both punctuated and bare
    (re.compile(r"\b\d{5}-\d{7}-\d\b"), "[NID]"),
    (re.compile(r"\b\d{11,}\b"), "[NUM]"),
    # Dates as printed on certificates. ISO dates are left alone because log
    # timestamps and retention cutoffs are legitimately ISO and carry no PII.
    (re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"), "[DATE]"),
    # Any run of Arabic-script characters in a log line came from a document.
    (re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]{2,}"), "[NON-LATIN]"),
    # Connection strings with inline credentials
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"), r"\1[CREDS]@"),
)


# A first-party event name: one dotted token, no whitespace. Interpolated prose
# cannot satisfy this, which is exactly the point.
_EVENT_NAME_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*$")

_FIRST_PARTY_LOGGER_PREFIXES: Final = ("certex", "worker")


def _is_first_party(logger_name: str) -> bool:
    return logger_name.split(".")[0] in _FIRST_PARTY_LOGGER_PREFIXES


def redact_event_message(message: str, *, first_party: bool) -> str:
    """Apply the event-message policy.

    First-party events must be static names. A non-conforming message is replaced
    by a digest so the same call site stays correlatable across records without
    its content ever being written.
    """
    if not first_party:
        return redact_text(message)
    if _EVENT_NAME_RE.match(message):
        return message
    digest = hashlib.sha256(message.encode("utf-8", "replace")).hexdigest()[:10]
    return f"{SUPPRESSED_EVENT_PREFIX}:{digest}]"


def redact_text(text: str, *, max_length: int = MAX_LOGGED_STRING) -> str:
    """Scrub PII-shaped substrings from free text and cap its length."""
    scrubbed = text
    for pattern, replacement in _SCRUBBERS:
        scrubbed = pattern.sub(replacement, scrubbed)
    if len(scrubbed) > max_length:
        return f"{scrubbed[:max_length]}...[+{len(scrubbed) - max_length} chars]"
    return scrubbed


def _redact_value(key: str, value: object, depth: int = 0) -> object:
    """Redact one value according to its key, recursing into containers."""
    if _SENSITIVE_KEY_RE.search(key):
        return REDACTED

    # Guard against pathological nesting in a log call.
    if depth >= 4:
        return f"[depth-limited:{type(value).__name__}]"

    if isinstance(value, str):
        if key in SAFE_KEYS:
            # Safe keys are still length-capped: a 5 MB "raw_text" mistakenly
            # bound to a safe key would otherwise flood the sink.
            return value if len(value) <= MAX_LOGGED_STRING else redact_text(value)
        return redact_text(value)

    if isinstance(value, Mapping):
        return {
            str(sub_key): _redact_value(str(sub_key), sub_value, depth + 1)
            for sub_key, sub_value in value.items()
        }

    if isinstance(value, bytes | bytearray | memoryview):
        return f"[bytes:{len(bytes(value))}]"

    if isinstance(value, list | tuple | set | frozenset):
        items: Sequence[object] = list(value)
        if len(items) > 25:
            return [_redact_value(key, item, depth + 1) for item in items[:25]] + [
                f"[+{len(items) - 25} more]"
            ]
        return [_redact_value(key, item, depth + 1) for item in items]

    if isinstance(value, bool | int | float | None):
        return value

    # Unknown object: never risk its __str__ carrying document content.
    return f"[{type(value).__name__}]"


def redact_event(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor enforcing the redaction policy on every record."""
    first_party = _is_first_party(str(event_dict.get("logger") or ""))
    cleaned: EventDict = {}
    for key, value in event_dict.items():
        key_str = str(key)
        if key_str == "event":
            cleaned[key_str] = (
                redact_event_message(value, first_party=first_party)
                if isinstance(value, str)
                else value
            )
        elif key_str in ("exc_info", "stack_info", "_record", "_from_structlog"):
            # Traceback rendering is handled downstream; see _redact_exception.
            cleaned[key_str] = value
        else:
            cleaned[key_str] = _redact_value(key_str, value)
    return cleaned


def _redact_rendered_exception(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Scrub the *rendered* traceback string produced by format_exc_info.

    A traceback frame can echo a local variable or an exception message holding a
    field value, so the rendered text gets the same pattern scrubbing as any other
    free text. This runs after ``format_exc_info``.
    """
    rendered = event_dict.get("exception")
    if isinstance(rendered, str):
        event_dict["exception"] = redact_text(rendered, max_length=8192)
    return event_dict


def safe_error(exc: BaseException) -> str:
    """Render an exception for logging: class name plus scrubbed message."""
    message = redact_text(str(exc), max_length=256)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class PIIRedactionFilter(logging.Filter):
    """stdlib logging filter scrubbing ``record.msg`` and ``record.args``.

    structlog handles records created through its own loggers. Libraries that call
    ``logging`` directly (sqlalchemy echoing SQL, botocore, celery) go through
    this filter instead, so no path into a handler is unscrubbed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg, max_length=2048)
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = {
                    key: _redact_value(str(key), value) for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_value("", value) for value in record.args)
        return True


# ---------------------------------------------------------------------------
# Request-scoped context
# ---------------------------------------------------------------------------
def bind_request_context(**values: object) -> None:
    """Bind ids that should appear on every subsequent record in this context."""
    structlog.contextvars.bind_contextvars(**values)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_NOISY_LOGGERS: Final[Mapping[str, str]] = {
    "uvicorn.access": "WARNING",
    "botocore": "WARNING",
    "boto3": "WARNING",
    "s3transfer": "WARNING",
    "urllib3": "WARNING",
    "asyncio": "WARNING",
    "multipart": "WARNING",
    "pdfminer": "ERROR",
    "PIL": "WARNING",
    "httpx": "WARNING",
    "httpcore": "WARNING",
}

_configured = False


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.format_exc_info,
        _redact_rendered_exception,
        # Redaction runs last so it sees every key any earlier processor added.
        redact_event,
    ]


def configure_logging(settings: Settings | None = None, *, force: bool = False) -> None:
    """Install the structlog + stdlib logging configuration.

    Idempotent: safe to call from the FastAPI lifespan, from a Celery worker
    bootstrap hook, and from test fixtures.
    """
    global _configured
    if _configured and not force:
        return

    active = settings or get_settings()
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if active.log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(PIIRedactionFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(active.log_level)

    for name, level in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)

    # SQLAlchemy echo is routed through the redaction filter but stays off unless
    # explicitly enabled, because statement parameters can contain field values.
    logging.getLogger("sqlalchemy.engine").setLevel("INFO" if active.db_echo else "WARNING")

    _configured = True


def get_logger(name: str | None = None, **initial: object) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Call :func:`configure_logging` first."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    if initial:
        return logger.bind(**initial)
    return logger


def iter_sensitive_key_examples() -> Iterable[str]:
    """Keys used by the test suite to assert the denylist stays wired up."""
    return (
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
    )
