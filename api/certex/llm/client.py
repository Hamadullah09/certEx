"""Talking to a language model, behind a boundary the rest of the system can replace.

The fallback layer exists for the fields nothing else could find: a form the rules
engine has no synonym for, a handwritten line, a layout no template matches. It is the
*last* layer, so its job is to be useful when it works and harmless when it does not.

Four decisions shape this module.

**A protocol, not a vendor.** Everything above this file depends on
:class:`LLMClient`, which is one method. Anthropic ships today; a deployment that must
keep certificate text inside its own network can supply its own implementation without
touching the extraction code.

**Plain HTTP rather than a vendor SDK.** The one call this needs is a POST with a JSON
body, and httpx is already a dependency. Adding an SDK would add a dependency to every
image - including the worker images that do the OCR - to save about forty lines, and
would put a second retry policy underneath the one the pipeline already has.

**Tool use, not prose.** The model is given a tool whose schema is the fields being
asked for, and its answer is read from the tool call. A model asked for JSON in prose
returns JSON in prose *most* of the time, and the exceptions arrive at three in the
morning.

**Nothing about the content is ever logged.** The prompt is a certificate: names,
parents' names, an identity number. Log lines here carry counts, hashes, token usage
and error types, and never a prompt, a response, or a value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx

from certex.config import Settings, get_settings
from certex.core.errors import AppError
from certex.logging_setup import get_logger, safe_error

__all__ = [
    "ANTHROPIC_VERSION",
    "AnthropicClient",
    "DisabledClient",
    "LLMClient",
    "LLMRequest",
    "LLMResponse",
    "LLMUnavailableError",
    "get_llm_client",
]

logger = get_logger(__name__)

ANTHROPIC_VERSION: Final = "2023-06-01"
"""The API version header. Pinned: an unpinned version changes response shapes."""

_TOOL_NAME: Final = "record_certificate_fields"


class LLMUnavailableError(AppError):
    """The fallback could not answer.

    Not an error the caller has to handle as a failure: the layer above records that
    the fallback was unavailable and the row goes to review with whatever the other
    layers read.
    """

    status = 503
    title = "Language model unavailable"
    remediation = "The row was kept with whatever the other layers could read."


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One ask: some source text, and the fields to find in it."""

    system: str
    prompt: str
    tool_schema: dict[str, Any]
    """JSON Schema for the tool's input - one property per field being asked for."""

    image_media_type: str | None = None
    image_base64: str | None = None
    """A page image, when the office has allowed vision. Sent alongside the text."""

    @property
    def has_image(self) -> bool:
        return bool(self.image_base64 and self.image_media_type)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """What came back, already reduced to field name -> value."""

    values: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    from_cache: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.values


class LLMClient(Protocol):
    """The whole of what extraction needs from a language model."""

    @property
    def enabled(self) -> bool:
        """Whether asking is worth the round trip."""
        ...

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Answer one request, or raise :class:`LLMUnavailableError`."""
        ...


class DisabledClient:
    """The client used when no fallback is configured.

    Says so rather than raising, so the extraction path has one branch - "is there a
    fallback" - instead of a try/except around every call.
    """

    @property
    def enabled(self) -> bool:
        return False

    def complete(self, request: LLMRequest) -> LLMResponse:
        del request
        raise LLMUnavailableError("No language model is configured for this deployment.")


class AnthropicClient:
    """Messages API over HTTP, using tool use to fix the response shape."""

    __slots__ = ("_settings",)

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.llm_enabled and self._settings.llm_api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self.enabled:
            raise LLMUnavailableError("The language model fallback is not configured.")

        payload = self._body(request)
        last_error: Exception | None = None
        for attempt in range(1, self._settings.llm_max_attempts + 1):
            try:
                return self._send(payload, attempt=attempt)
            except (httpx.HTTPError, LLMUnavailableError, ValueError) as exc:
                last_error = exc
                # One retry covers a dropped connection and a rate-limit blip. More
                # than that is the pipeline's job, not this method's.
                logger.info(
                    "llm.attempt_failed",
                    attempt=attempt,
                    max_retries=self._settings.llm_max_attempts,
                    error_type=safe_error(exc),
                )
        raise LLMUnavailableError(
            "The language model did not answer.",
            remediation="The row was kept with whatever the other layers could read.",
        ) from last_error

    # ------------------------------------------------------------------ request
    def _body(self, request: LLMRequest) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if request.has_image:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": request.image_media_type,
                        "data": request.image_base64,
                    },
                }
            )
        content.append({"type": "text", "text": request.prompt})

        return {
            "model": self._settings.llm_model,
            "max_tokens": self._settings.llm_max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": content}],
            "tools": [
                {
                    "name": _TOOL_NAME,
                    "description": (
                        "Record the fields found on this certificate. Omit any field "
                        "that is not printed on it."
                    ),
                    "input_schema": request.tool_schema,
                }
            ],
            # The answer has to be the tool call; left to choose, a model sometimes
            # explains itself instead, and prose is not a field set.
            "tool_choice": {"type": "tool", "name": _TOOL_NAME},
            # Nothing is creative here: the task is to read what is printed.
            "temperature": 0,
        }

    def _send(self, payload: dict[str, Any], *, attempt: int) -> LLMResponse:
        key = self._settings.llm_api_key
        if key is None:  # pragma: no cover - enabled checks this
            raise LLMUnavailableError("No API key is configured.")

        with httpx.Client(timeout=self._settings.llm_timeout_seconds) as client:
            response = client.post(
                f"{self._settings.llm_base_url.rstrip('/')}/v1/messages",
                json=payload,
                headers={
                    "x-api-key": key.get_secret_value(),
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )

        if response.status_code >= 400:
            # The body can quote the prompt back, so only the status is recorded.
            logger.info("llm.request_rejected", status_code=response.status_code, attempt=attempt)
            raise LLMUnavailableError(f"The model returned HTTP {response.status_code}.")

        return self._read(response.json())

    # ----------------------------------------------------------------- response
    def _read(self, body: dict[str, Any]) -> LLMResponse:
        """Pull the tool input out of the reply, tolerating everything around it."""
        values: dict[str, str] = {}
        for block in body.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            raw = block.get("input")
            if isinstance(raw, str):
                # Some models hand back the input as a JSON string.
                try:
                    raw = json.loads(raw)
                except ValueError:
                    continue
            if not isinstance(raw, dict):
                continue
            for name, value in raw.items():
                if isinstance(value, str) and value.strip():
                    values[str(name)] = value.strip()

        usage = body.get("usage") or {}
        result = LLMResponse(
            values=values,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=str(body.get("model") or self._settings.llm_model),
        )
        logger.info(
            "llm.answered",
            model=result.model,
            provider=self._settings.llm_provider,
            count=len(result.values),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return result


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """The client this deployment is configured for.

    Returns the disabled client unless a fallback is switched on *and* a key exists,
    so a half-configured deployment behaves like one with no fallback rather than
    failing every document.
    """
    active = settings or get_settings()
    if not active.llm_enabled or active.llm_api_key is None:
        return DisabledClient()
    if active.llm_provider != "anthropic":
        logger.warning("llm.provider_unknown", provider=active.llm_provider)
        return DisabledClient()
    return AnthropicClient(active)
