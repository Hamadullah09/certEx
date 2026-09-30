"""The last layer: asking a model for the fields nothing else could find.

It runs only for fields still missing after the template and the rules engine, because
those two are cheaper, deterministic, and already know this office's forms. What is left
is the genuinely hard remainder - a layout no template matches, a label with no synonym,
a line the OCR half read.

Three properties make this safe to have in a register:

**It cannot overwrite.** Candidates from here carry ``ExtractionMethod.LLM``, which the
merge ranks below rules and templates, and it is only asked about fields that are empty.

**It cannot invent.** Every value is looked for in the text that was sent. One that
cannot be found is kept and flagged ``VALUE_UNVERIFIED``, so a reviewer sees a value
with a warning rather than a value that reads like any other.

**It cannot stop a document.** A model that is slow, absent, rate-limited or
misconfigured produces a flag on the row and the other layers' values, never a failed
document.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from certex.config import Settings, get_settings
from certex.enums import ExtractionMethod, ValidationFlag
from certex.fields import FieldSchema
from certex.llm.cache import LLMCache
from certex.llm.client import LLMClient, LLMRequest, LLMUnavailableError, get_llm_client
from certex.llm.prompts import (
    SYSTEM_PROMPT,
    build_prompt,
    build_tool_schema,
    prompt_digest,
    verify_values,
)
from certex.logging_setup import get_logger
from certex.pipeline.extract.candidates import Candidate, FieldSource
from certex.pipeline.extract.rules import UnitPage

__all__ = ["FallbackResult", "missing_fields", "run_llm_fallback"]

logger = get_logger(__name__)

_LLM_CONFIDENCE = 0.55
"""Confidence given to a verified fallback value.

Below anything the rules engine produces, and below the review floor an office is
likely to set, because a value read by a model with no label to anchor it is a value
worth a person's glance even when it is right.
"""

_UNVERIFIED_CONFIDENCE = 0.2
"""A value the document does not appear to contain. Low enough that it cannot carry a
row over any sane threshold on its own."""


@dataclass(frozen=True, slots=True)
class FallbackResult:
    """What the fallback added, and what should be said about it."""

    candidates: dict[str, Candidate] = field(default_factory=dict)
    flags: tuple[ValidationFlag, ...] = ()
    unverified_fields: tuple[str, ...] = ()
    asked: int = 0
    from_cache: bool = False

    @property
    def ran(self) -> bool:
        return self.asked > 0


def missing_fields(schema: FieldSchema, found: dict[str, Candidate], *, limit: int) -> list[str]:
    """Which fields to ask about: those with no value, in schema order.

    Required fields first, so a truncated ask spends its budget on the ones a
    certificate is useless without.
    """
    empty = [
        spec
        for spec in schema.fields
        if not (found.get(spec.name) and found[spec.name].value.strip())
    ]
    ordered = sorted(empty, key=lambda spec: (not spec.required, schema.names.index(spec.name)))
    return [spec.name for spec in ordered[:limit]]


def _source_text(pages: Sequence[UnitPage]) -> str:
    """The certificate as the pipeline read it, page by page."""
    return "\n".join(page.layout.text for page in pages).strip()


def run_llm_fallback(
    pages: Sequence[UnitPage],
    *,
    schema: FieldSchema,
    found: dict[str, Candidate],
    workspace_id: uuid.UUID,
    settings: Settings | None = None,
    client: LLMClient | None = None,
    cache: LLMCache | None = None,
) -> FallbackResult:
    """Ask about what is still missing, and keep only what the document supports."""
    active = settings or get_settings()
    if not active.llm_enabled:
        return FallbackResult()

    model = client or get_llm_client(active)
    if not model.enabled:
        return FallbackResult()

    wanted = missing_fields(schema, found, limit=active.llm_max_fields_per_request)
    if not wanted:
        return FallbackResult()

    text = _source_text(pages)
    if not text:
        # Nothing was read, so there is nothing to verify an answer against. Asking
        # anyway would be asking a model to invent a certificate.
        return FallbackResult()

    tool_schema = build_tool_schema(schema, wanted)
    prompt = build_prompt(text, wanted, max_chars=active.llm_max_source_chars)
    digest = prompt_digest(active.llm_model, prompt, tool_schema)

    store = cache
    if store is None and active.llm_cache_enabled:
        store = LLMCache.from_settings(active)
    response = store.get(str(workspace_id), digest) if store else None

    if response is None:
        try:
            response = model.complete(
                LLMRequest(system=SYSTEM_PROMPT, prompt=prompt, tool_schema=tool_schema)
            )
        except LLMUnavailableError:
            logger.info("llm.fallback_unavailable", count=len(wanted))
            return FallbackResult(flags=(ValidationFlag.LLM_UNAVAILABLE,), asked=len(wanted))
        if store is not None:
            store.put(str(workspace_id), digest, response)

    # Only fields that were actually asked for: a model that volunteers something else
    # is answering a question nobody put to it.
    offered = {name: value for name, value in response.values.items() if name in set(wanted)}
    checked, unverified = verify_values(offered, source_text=text)

    candidates: dict[str, Candidate] = {}
    for name, verified in checked.items():
        candidates[name] = Candidate(
            field=name,
            value=verified.value,
            confidence=_LLM_CONFIDENCE if verified.verified else _UNVERIFIED_CONFIDENCE,
            method=ExtractionMethod.LLM,
            source=FieldSource(
                page_number=pages[0].page_number if pages else 1,
                snippet=verified.value,
                label=None,
            ),
        )

    flags: tuple[ValidationFlag, ...] = (ValidationFlag.VALUE_UNVERIFIED,) if unverified else ()
    logger.info(
        "llm.fallback_ran",
        count=len(candidates),
        total=len(wanted),
        failed_count=len(unverified),
        cache_hit=response.from_cache,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    return FallbackResult(
        candidates=candidates,
        flags=flags,
        unverified_fields=tuple(unverified),
        asked=len(wanted),
        from_cache=response.from_cache,
    )
