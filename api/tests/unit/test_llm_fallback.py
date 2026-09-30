"""The last extraction layer, and the check that keeps it honest.

A language model asked to read a certificate will sometimes produce a value that is
plausible, well-formed, and printed nowhere on the document - a father's name completed
from a common pattern, a date that fits the others. In a register that is worse than a
blank field, because a blank field is obviously missing and an invented one is not.

So most of this file is about verification: what counts as "the document says this", what
does not, and what happens to a value that fails. The rest is about the layer being
unable to do harm - it cannot overwrite a better layer, cannot run when nothing was read,
and cannot fail a document by being absent.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from certex.config import Settings
from certex.enums import CertificateType, ExtractionMethod, FieldRole, ValidationFlag
from certex.fields import FieldKind, FieldSchema, FieldSpec, builtin_schema
from certex.llm.client import (
    DisabledClient,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    get_llm_client,
)
from certex.llm.prompts import (
    build_prompt,
    build_tool_schema,
    prompt_digest,
    verify_values,
)
from certex.pipeline.extract.candidates import Candidate, FieldSource
from certex.pipeline.extract.llm_fallback import missing_fields, run_llm_fallback
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.text.layout_builder import PositionedWord, build_layout

pytestmark = pytest.mark.unit

WORKSPACE = uuid.uuid4()


def spec(
    name: str,
    role: FieldRole = FieldRole.NONE,
    *,
    kind: FieldKind = FieldKind.TEXT,
    required: bool = False,
) -> FieldSpec:
    return FieldSpec(
        name=name,
        label=name.replace("_", " ").title(),
        kind=kind,
        role=role,
        required=required,
    )


def small_schema() -> FieldSchema:
    return FieldSchema(
        fields=(
            spec(
                "certificate_number", FieldRole.IDENTIFIER, kind=FieldKind.REFERENCE, required=True
            ),
            spec("child_full_name", FieldRole.SUBJECT_NAME, kind=FieldKind.NAME, required=True),
            spec("father_full_name", FieldRole.FATHER_NAME, kind=FieldKind.NAME),
            spec("village"),
        ),
        certificate_type=CertificateType.BIRTH,
    )


def page(text: str, *, page_number: int = 1) -> UnitPage:
    """One page whose layout reads as the given text.

    Built through ``build_layout`` rather than by hand so the page is assembled the way
    the pipeline assembles one - the fallback reads ``layout.text``, and a hand-made
    layout could disagree with what the real one would say.
    """
    words = text.split()
    positioned = [
        PositionedWord(
            text=word,
            x0=float(index * 50),
            y0=100.0,
            x1=float(index * 50 + 40),
            y1=112.0,
            line_key=(0,),
            order=index,
        )
        for index, word in enumerate(words)
    ]
    return UnitPage(
        page_number=page_number,
        layout=build_layout(
            positioned,
            page_number=page_number,
            width=595.0,
            height=842.0,
            unit="pt",
            engine="pymupdf",
            engine_order=True,
        ),
    )


def found(**values: str) -> dict[str, Candidate]:
    return {
        name: Candidate(
            field=name,
            value=value,
            confidence=0.9,
            method=ExtractionMethod.RULE,
            source=FieldSource(page_number=1, snippet=value),
        )
        for name, value in values.items()
    }


def enabled_settings(**overrides: Any) -> Settings:
    """Settings with the fallback switched on, and nothing else changed."""
    return Settings(
        llm_enabled=True,
        llm_api_key="test-key",  # type: ignore[arg-type]
        llm_cache_enabled=False,
        **overrides,
    )


class StubClient:
    """A client that answers with whatever the test prepared."""

    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.values = values or {}
        self.fail = fail
        self.requests: list[LLMRequest] = []

    @property
    def enabled(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.fail:
            raise LLMUnavailableError("nothing doing")
        return LLMResponse(values=dict(self.values), model="stub", input_tokens=10, output_tokens=5)


class TestVerification:
    def test_a_value_printed_on_the_page_is_verified(self) -> None:
        checked, unverified = verify_values(
            {"child_full_name": "Ayesha Noor Malik"},
            source_text="Name of Child: Ayesha Noor Malik",
        )
        assert checked["child_full_name"].verified
        assert unverified == []

    def test_punctuation_and_spacing_do_not_matter(self) -> None:
        """A model legitimately rewrites "35201 1234567 1" as a formatted number."""
        checked, unverified = verify_values(
            {"father_id_number": "35201-1234567-1"},
            source_text="Father's CNIC 35201 1234567 1",
        )
        assert checked["father_id_number"].verified
        assert unverified == []

    def test_case_does_not_matter(self) -> None:
        checked, _ = verify_values({"village": "Shahdara"}, source_text="VILLAGE: SHAHDARA")
        assert checked["village"].verified

    def test_a_value_the_page_does_not_contain_is_not_verified(self) -> None:
        """The whole point: an invented father is caught."""
        checked, unverified = verify_values(
            {"father_full_name": "Muhammad Aslam"},
            source_text="Name of Child: Ayesha Noor Malik",
        )
        assert not checked["father_full_name"].verified
        assert unverified == ["father_full_name"]

    def test_one_extra_word_is_enough_to_fail(self) -> None:
        checked, _ = verify_values(
            {"child_full_name": "Ayesha Noor Malik Butt"},
            source_text="Name of Child: Ayesha Noor Malik",
        )
        assert not checked["child_full_name"].verified

    def test_words_out_of_order_still_verify(self) -> None:
        """A reordering is a reading of the same words, not an invention."""
        checked, _ = verify_values(
            {"child_full_name": "Malik Ayesha"},
            source_text="Ayesha Noor Malik",
        )
        assert checked["child_full_name"].verified

    def test_urdu_values_verify(self) -> None:
        name = "عائشہ نور"
        checked, unverified = verify_values(
            {"child_full_name": name}, source_text=f"بچے کا نام {name}"
        )
        assert checked["child_full_name"].verified
        assert unverified == []

    def test_an_empty_value_is_dropped_rather_than_flagged(self) -> None:
        checked, unverified = verify_values({"village": "  "}, source_text="anything")
        assert checked == {}
        assert unverified == []

    def test_a_punctuation_only_value_is_dropped(self) -> None:
        checked, _ = verify_values({"village": "---"}, source_text="anything")
        assert checked == {}


class TestWhatIsAsked:
    def test_only_empty_fields_are_asked_about(self) -> None:
        schema = small_schema()
        already = found(certificate_number="BC-1", child_full_name="Ayesha")
        assert missing_fields(schema, already, limit=10) == ["father_full_name", "village"]

    def test_required_fields_come_first(self) -> None:
        """A truncated ask should spend its budget on what a certificate needs."""
        schema = small_schema()
        assert missing_fields(schema, {}, limit=2) == ["certificate_number", "child_full_name"]

    def test_a_blank_value_counts_as_missing(self) -> None:
        schema = small_schema()
        already = found(child_full_name="   ")
        assert "child_full_name" in missing_fields(schema, already, limit=10)

    def test_nothing_is_asked_when_everything_was_read(self) -> None:
        schema = small_schema()
        already = found(
            certificate_number="BC-1",
            child_full_name="Ayesha",
            father_full_name="Tariq",
            village="Shahdara",
        )
        assert missing_fields(schema, already, limit=10) == []

    def test_the_tool_schema_describes_each_field(self) -> None:
        schema = builtin_schema(CertificateType.BIRTH)
        tool = build_tool_schema(schema, ["certificate_number", "child_full_name"])
        assert set(tool["properties"]) == {"certificate_number", "child_full_name"}
        assert tool["additionalProperties"] is False
        assert "certificate" in tool["properties"]["certificate_number"]["description"].lower()

    def test_nothing_in_the_tool_schema_is_required(self) -> None:
        """A required field invites a model to fill it in, which is the failure mode."""
        tool = build_tool_schema(small_schema(), ["father_full_name"])
        assert "required" not in tool

    def test_the_prompt_carries_the_text_and_the_missing_fields(self) -> None:
        prompt = build_prompt("Name of Child: Ayesha", ["father_full_name"], max_chars=1000)
        assert "Name of Child: Ayesha" in prompt
        assert "father_full_name" in prompt

    def test_a_long_document_is_truncated(self) -> None:
        prompt = build_prompt("x" * 5000, ["village"], max_chars=100)
        assert "truncated" in prompt
        assert len(prompt) < 1000


class TestCacheKey:
    def test_the_same_question_gives_the_same_key(self) -> None:
        tool = build_tool_schema(small_schema(), ["village"])
        first = prompt_digest("m", "text", tool)
        second = prompt_digest("m", "text", tool)
        assert first == second

    def test_a_different_model_is_a_different_question(self) -> None:
        tool = build_tool_schema(small_schema(), ["village"])
        assert prompt_digest("a", "text", tool) != prompt_digest("b", "text", tool)

    def test_different_fields_are_a_different_question(self) -> None:
        one = build_tool_schema(small_schema(), ["village"])
        two = build_tool_schema(small_schema(), ["village", "father_full_name"])
        assert prompt_digest("m", "text", one) != prompt_digest("m", "text", two)

    def test_different_text_is_a_different_question(self) -> None:
        tool = build_tool_schema(small_schema(), ["village"])
        assert prompt_digest("m", "one", tool) != prompt_digest("m", "two", tool)


class TestTheLayer:
    def test_a_verified_value_becomes_a_candidate(self) -> None:
        client = StubClient({"father_full_name": "Tariq Mahmood"})
        result = run_llm_fallback(
            [page("Name of Child: Ayesha Noor. Father: Tariq Mahmood")],
            schema=small_schema(),
            found=found(certificate_number="BC-1", child_full_name="Ayesha Noor"),
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=client,
        )
        assert result.candidates["father_full_name"].value == "Tariq Mahmood"
        assert result.candidates["father_full_name"].method is ExtractionMethod.LLM
        assert result.flags == ()

    def test_an_invented_value_is_kept_but_flagged(self) -> None:
        client = StubClient({"father_full_name": "Muhammad Aslam"})
        result = run_llm_fallback(
            [page("Name of Child: Ayesha Noor")],
            schema=small_schema(),
            found=found(certificate_number="BC-1"),
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=client,
        )
        assert "father_full_name" in result.candidates
        assert ValidationFlag.VALUE_UNVERIFIED in result.flags
        assert result.unverified_fields == ("father_full_name",)

    def test_an_unverified_value_is_scored_far_lower(self) -> None:
        verified = run_llm_fallback(
            [page("Father: Tariq Mahmood")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=StubClient({"father_full_name": "Tariq Mahmood"}),
        )
        invented = run_llm_fallback(
            [page("Father: Tariq Mahmood")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=StubClient({"father_full_name": "Somebody Else"}),
        )
        assert (
            invented.candidates["father_full_name"].confidence
            < verified.candidates["father_full_name"].confidence
        )

    def test_a_field_nobody_asked_about_is_ignored(self) -> None:
        """A model volunteering an answer is answering a question nobody put."""
        client = StubClient({"village": "Shahdara", "child_full_name": "Ayesha Noor"})
        result = run_llm_fallback(
            [page("Village: Shahdara. Name of Child: Ayesha Noor")],
            schema=small_schema(),
            found=found(child_full_name="Ayesha Noor"),
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=client,
        )
        assert "village" in result.candidates
        assert "child_full_name" not in result.candidates

    def test_it_does_not_run_when_the_fallback_is_off(self) -> None:
        client = StubClient({"village": "Shahdara"})
        result = run_llm_fallback(
            [page("Village: Shahdara")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=Settings(llm_enabled=False),
            client=client,
        )
        assert not result.ran
        assert client.requests == []

    def test_it_does_not_run_when_nothing_was_read(self) -> None:
        """With no text there is nothing to verify against, so asking would be asking
        a model to invent a certificate."""
        client = StubClient({"village": "Shahdara"})
        result = run_llm_fallback(
            [page("")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=client,
        )
        assert not result.ran
        assert client.requests == []

    def test_it_does_not_run_when_every_field_was_read(self) -> None:
        client = StubClient()
        result = run_llm_fallback(
            [page("anything")],
            schema=small_schema(),
            found=found(
                certificate_number="BC-1",
                child_full_name="Ayesha",
                father_full_name="Tariq",
                village="Shahdara",
            ),
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=client,
        )
        assert not result.ran
        assert client.requests == []

    def test_an_unavailable_model_flags_the_row_rather_than_failing_it(self) -> None:
        result = run_llm_fallback(
            [page("Village: Shahdara")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=StubClient(fail=True),
        )
        assert result.candidates == {}
        assert ValidationFlag.LLM_UNAVAILABLE in result.flags
        assert result.ran, "it did ask, and should say so"

    def test_the_ask_is_bounded(self) -> None:
        client = StubClient()
        run_llm_fallback(
            [page("anything")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=enabled_settings(llm_max_fields_per_request=1),
            client=client,
        )
        assert len(client.requests[0].tool_schema["properties"]) == 1

    def test_no_image_is_sent_unless_vision_is_allowed(self) -> None:
        client = StubClient()
        run_llm_fallback(
            [page("Village: Shahdara")],
            schema=small_schema(),
            found={},
            workspace_id=WORKSPACE,
            settings=enabled_settings(),
            client=client,
        )
        assert client.requests[0].has_image is False


class TestClientSelection:
    def test_nothing_configured_gives_the_disabled_client(self) -> None:
        client = get_llm_client(Settings())
        assert isinstance(client, DisabledClient)
        assert client.enabled is False

    def test_a_key_that_went_missing_after_boot_still_disables(self) -> None:
        """Defence behind the boot guard, which is the real protection.

        ``LLM_ENABLED=true`` without a key refuses to start, so this state cannot be
        reached through configuration - which is why the settings here are built by
        copying past validation. The branch is kept because a half-configured client
        should behave like no client rather than failing every document.
        """
        settings = enabled_settings().model_copy(update={"llm_api_key": None})
        assert isinstance(get_llm_client(settings), DisabledClient)

    def test_an_unknown_provider_is_disabled_rather_than_guessed_at(self) -> None:
        client = get_llm_client(
            Settings(llm_enabled=True, llm_api_key="k", llm_provider="somebody-else")  # type: ignore[arg-type]
        )
        assert isinstance(client, DisabledClient)

    def test_a_configured_deployment_gets_a_working_client(self) -> None:
        client = get_llm_client(enabled_settings())
        assert client.enabled is True

    def test_the_disabled_client_raises_rather_than_answering(self) -> None:
        with pytest.raises(LLMUnavailableError):
            DisabledClient().complete(
                LLMRequest(system="s", prompt="p", tool_schema={"type": "object"})
            )
