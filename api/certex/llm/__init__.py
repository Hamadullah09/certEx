"""The optional language-model fallback: the last layer of field extraction.

Off unless a deployment switches it on and supplies a key, because enabling it sends
the text of a certificate to a third party. Everything above it depends on the
:class:`~certex.llm.client.LLMClient` protocol rather than on a vendor, and every value
it returns is checked against the document before it is believed.
"""

from certex.llm.cache import LLMCache
from certex.llm.client import (
    AnthropicClient,
    DisabledClient,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    get_llm_client,
)
from certex.llm.prompts import (
    SYSTEM_PROMPT,
    VerifiedValue,
    build_prompt,
    build_tool_schema,
    prompt_digest,
    verify_values,
)

__all__ = [
    "SYSTEM_PROMPT",
    "AnthropicClient",
    "DisabledClient",
    "LLMCache",
    "LLMClient",
    "LLMRequest",
    "LLMResponse",
    "LLMUnavailableError",
    "VerifiedValue",
    "build_prompt",
    "build_tool_schema",
    "get_llm_client",
    "prompt_digest",
    "verify_values",
]
