"""Text normalisation.

Every case here is a mis-extraction that really happens, not a hypothetical: Word's
PDF export stores Urdu as presentation forms, Arial draws the hyphen and the soft
hyphen with one glyph, and common fonts draw Arabic and Urdu look-alike letters
alike. The test that matters most is the last one: text read from a document and the
same text typed by a reviewer must compare equal, or every correction would look
like a change.

Special characters are written as ``chr(0x...)`` so the file stays readable and no
test depends on an invisible byte surviving an editor.
"""

from __future__ import annotations

import pytest

from certex.pipeline.text.normalize import (
    arabic_script_ratio,
    contains_arabic_script,
    detect_language,
    is_arabic_script,
    normalize_text,
    normalize_word,
    word_tokens,
)

pytestmark = pytest.mark.unit

SOFT_HYPHEN = chr(0x00AD)
NBSP = chr(0x00A0)
ZWSP = chr(0x200B)
ZWNJ = chr(0x200C)
ZWJ = chr(0x200D)
LRM = chr(0x200E)
RLM = chr(0x200F)
RLE = chr(0x202B)
PDF_MARK = chr(0x202C)
BOM = chr(0xFEFF)

ARABIC_YEH = chr(0x064A)
ALEF_MAKSURA = chr(0x0649)
FARSI_YEH = chr(0x06CC)
ARABIC_KAF = chr(0x0643)
KEHEH = chr(0x06A9)
ARABIC_HEH = chr(0x0647)
HEH_GOAL = chr(0x06C1)
TEH_MARBUTA = chr(0x0629)
TEH_MARBUTA_GOAL = chr(0x06C3)
RNOON = chr(0x06BB)
TTEH = chr(0x0679)

ALEF = chr(0x0627)
LAM = chr(0x0644)
BEH = chr(0x0628)

HEH_ISOLATED_FORM = chr(0xFEE9)
BEH_INITIAL_FORM = chr(0xFE91)
LAM_ALEF_LIGATURE = chr(0xFEFB)
FI_LIGATURE = chr(0xFB01)


class TestPresentationForms:
    """NFKC folds one-glyph-per-shape storage back to the letters a person types."""

    def test_isolated_form_becomes_a_letter(self) -> None:
        # Heh in isolated form folds to Arabic heh, which is then canonicalised to
        # the Urdu heh goal a reviewer would type.
        assert normalize_word(HEH_ISOLATED_FORM) == HEH_GOAL

    def test_initial_form_becomes_a_letter(self) -> None:
        assert normalize_word(BEH_INITIAL_FORM) == BEH

    def test_ligature_becomes_its_letters(self) -> None:
        assert normalize_word(LAM_ALEF_LIGATURE) == LAM + ALEF

    def test_latin_ligature_becomes_its_letters(self) -> None:
        assert normalize_word(FI_LIGATURE + "nal") == "final"

    def test_no_break_space_becomes_a_space(self) -> None:
        assert normalize_text(f"Union{NBSP}Council") == "Union Council"


class TestUrduLookAlikes:
    """Arabic-only letters never appear in correctly written Urdu, so they fold."""

    @pytest.mark.parametrize(
        ("extracted", "typed"),
        [
            (ARABIC_YEH, FARSI_YEH),
            (ALEF_MAKSURA, FARSI_YEH),
            (ARABIC_KAF, KEHEH),
            (ARABIC_HEH, HEH_GOAL),
            (TEH_MARBUTA, TEH_MARBUTA_GOAL),
            (RNOON, TTEH),
        ],
    )
    def test_letter_folds(self, extracted: str, typed: str) -> None:
        assert normalize_word(extracted) == typed

    def test_a_name_extracted_with_arabic_letters_equals_the_typed_name(self) -> None:
        # "عائشہ نور ملک" as some fonts hand it back, against the same name typed.
        extracted = "عائش" + ARABIC_HEH + " نور مل" + ARABIC_KAF
        typed = "عائش" + HEH_GOAL + " نور مل" + KEHEH
        assert normalize_text(extracted) == normalize_text(typed)

    def test_urdu_letters_that_are_correct_are_left_alone(self) -> None:
        assert normalize_word(FARSI_YEH + KEHEH + HEH_GOAL) == FARSI_YEH + KEHEH + HEH_GOAL


class TestSoftHyphen:
    """A PDF text layer holds only drawn glyphs, so a soft hyphen there was visible."""

    def test_between_digits_becomes_a_hyphen(self) -> None:
        assert normalize_word(f"BC{SOFT_HYPHEN}2019{SOFT_HYPHEN}004471") == "BC-2019-004471"

    def test_a_cnic_survives(self) -> None:
        raw = f"35201{SOFT_HYPHEN}1234567{SOFT_HYPHEN}1"
        assert normalize_word(raw) == "35201-1234567-1"

    def test_standing_alone_becomes_a_hyphen(self) -> None:
        assert normalize_text(f"PUNJAB {SOFT_HYPHEN} LOCAL") == "PUNJAB - LOCAL"

    def test_between_letters_is_a_hyphenation_hint_and_goes(self) -> None:
        assert normalize_word(f"certi{SOFT_HYPHEN}ficate") == "certificate"

    def test_between_urdu_letters_is_a_hyphenation_hint_and_goes(self) -> None:
        assert normalize_word(f"لاہ{SOFT_HYPHEN}ور") == "لاہور"

    def test_beside_a_digit_becomes_a_hyphen(self) -> None:
        assert normalize_word(f"REG{SOFT_HYPHEN}2019") == "REG-2019"


class TestHyphenLookAlikes:
    @pytest.mark.parametrize("code", [0x2010, 0x2011, 0x2012, 0x2212])
    def test_folds_to_ascii_hyphen(self, code: int) -> None:
        assert normalize_word(f"BC{chr(code)}2019") == "BC-2019"

    def test_an_em_dash_is_not_a_hyphen(self) -> None:
        # An em dash is punctuation a typesetter chose, not a mis-read hyphen.
        assert (
            normalize_word("Lahore" + chr(0x2014) + "Punjab") == "Lahore" + chr(0x2014) + "Punjab"
        )


class TestInvisibleCharacters:
    @pytest.mark.parametrize("invisible", [ZWSP, ZWNJ, ZWJ, LRM, RLM, RLE, PDF_MARK, BOM])
    def test_dropped(self, invisible: str) -> None:
        assert normalize_word(f"Ayesha{invisible}Noor") == "AyeshaNoor"

    def test_a_byte_order_mark_does_not_survive_at_the_front(self) -> None:
        assert normalize_text(f"{BOM}CERTIFICATE OF BIRTH") == "CERTIFICATE OF BIRTH"

    def test_bidi_marks_around_urdu_are_dropped(self) -> None:
        assert normalize_text(f"{RLE}سروسز ہسپتال{PDF_MARK}") == "سروسز ہسپتال"


class TestWhitespace:
    def test_runs_collapse_within_a_line(self) -> None:
        assert normalize_text("Name of  Child:\t\tAyesha") == "Name of Child: Ayesha"

    def test_newlines_are_kept_as_line_structure(self) -> None:
        assert normalize_text("  line one \n\tline two  ") == "line one\nline two"

    def test_blank_lines_are_kept_between_lines(self) -> None:
        assert normalize_text("one\n\ntwo") == "one\n\ntwo"

    def test_outer_whitespace_is_trimmed(self) -> None:
        assert normalize_text("\n\n  Lahore  \n\n") == "Lahore"

    def test_normalising_twice_changes_nothing(self) -> None:
        raw = f"BC{SOFT_HYPHEN}2019  {NBSP} {ZWSP}عائش{ARABIC_HEH}\n\n x "
        once = normalize_text(raw)
        assert normalize_text(once) == once


class TestTokens:
    def test_latin_urdu_and_digits_are_all_tokens(self) -> None:
        assert word_tokens("Sex: F / جنس 42") == ["Sex", "F", "جنس", "42"]

    def test_punctuation_is_not_a_token(self) -> None:
        assert word_tokens("---  ,. ") == []

    def test_a_hyphenated_identifier_splits_on_the_hyphen(self) -> None:
        assert word_tokens("BC-2019-004471") == ["BC", "2019", "004471"]


class TestScriptDetection:
    def test_urdu_letters_are_arabic_script(self) -> None:
        assert is_arabic_script(FARSI_YEH)
        assert contains_arabic_script("Name / نام")

    def test_latin_is_not(self) -> None:
        assert not is_arabic_script("A")
        assert not contains_arabic_script("Name of Child")

    def test_ratio_counts_letters_only(self) -> None:
        assert arabic_script_ratio("نام 12345") == 1.0
        assert arabic_script_ratio("Name") == 0.0
        assert arabic_script_ratio("") == 0.0
        assert 0.4 < arabic_script_ratio("abc نام") < 0.6


class TestLanguageDetection:
    def test_english_page(self) -> None:
        assert detect_language("CERTIFICATE OF BIRTH Name of Child Ayesha") == "eng"

    def test_urdu_page(self) -> None:
        assert detect_language("پیدائش کا سرٹیفکیٹ بچے کا نام عائشہ نور ملک") == "urd"

    def test_bilingual_page(self) -> None:
        assert detect_language("Name of Child / بچے کا نام عائشہ نور ملک") == "eng+urd"

    def test_a_stray_english_word_does_not_make_an_urdu_page_bilingual(self) -> None:
        urdu = "پیدائش کا سرٹیفکیٹ بچے کا نام عائشہ نور ملک سروسز ہسپتال لاہور"
        assert detect_language(f"{urdu} No") == "urd"

    @pytest.mark.parametrize("text", ["", "12", "42 / 7", "ab"])
    def test_too_little_text_to_judge(self, text: str) -> None:
        assert detect_language(text) is None
