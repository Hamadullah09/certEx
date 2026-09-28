"""Deciding whether a page's own text layer can be trusted.

The failure this guards against is silent: a scan carrying an invisible, garbled OCR
layer, or a single "Page 1 of 1" stamp, looks like a text PDF and would produce an
empty row with no error. Every case below is one of those shapes.
"""

from __future__ import annotations

import pytest

from certex.pipeline.text.quality import assess_text, dictionary_words

pytestmark = pytest.mark.unit

CLEAN_ENGLISH = """CERTIFICATE OF BIRTH
GOVERNMENT OF PAKISTAN - LOCAL GOVERNMENT DEPARTMENT
Certificate No.: BC-2019-004471
Registration No.: REG/LHR/2019/88213
Date of Registration: 2019-04-02
Name of Child: Ayesha Noor Malik
Sex: Female
Date of Birth: 1987-03-14
Place of Birth: Services Hospital, Lahore
Father's Name: Tariq Mahmood Malik
Father's CNIC: 35201-1234567-1
Mother's Name: Nasreen Akhtar
Issuing Authority: Union Council 42, Lahore
Registrar: Muhammad Aslam
Date of Issue: 2019-04-09"""

CLEAN_URDU = """پیدائش کا سرٹیفکیٹ
بچے کا نام عائشہ نور ملک
جائے پیدائش سروسز ہسپتال لاہور
والد کا نام طارق محمود ملک
جاری کنندہ یونین کونسل لاہور"""

# A broken font encoding maps glyphs to letters that spell nothing.
GIBBERISH = " ".join(
    ["Xqzjvbk", "Mwphgtz", "Rlxkqvd", "Zdfhwjn", "Tqbvmxs", "Kzwpljc", "Vngxhqf", "Jbtzrmp"] * 3
)

# Latin-1 punctuation: what a PDF with a broken character map yields instead of
# letters. Built from code points so the source file stays legible.
SYMBOL_SOUP = (
    " ".join(
        "".join(chr(code + offset) for offset in range(3)) for code in range(0x00A1, 0x00BF, 3)
    )
    * 4
)


class TestTrustworthyText:
    def test_a_clean_english_certificate_needs_no_ocr(self) -> None:
        quality = assess_text(CLEAN_ENGLISH, min_chars=100, min_dict_ratio=0.35)
        assert not quality.needs_ocr
        assert quality.reason is None
        assert quality.char_count > 300
        assert quality.alnum_ratio > 0.85
        assert quality.dict_hit_rate > 0.9

    def test_a_clean_urdu_certificate_needs_no_ocr(self) -> None:
        quality = assess_text(CLEAN_URDU, min_chars=100, min_dict_ratio=0.35)
        assert not quality.needs_ocr
        assert quality.dict_hit_rate > 0.9

    def test_identifiers_and_dates_count_as_real_content(self) -> None:
        quality = assess_text(
            "2019 2020 35201 1234567 BC2019 UC42", min_chars=10, min_dict_ratio=0.35
        )
        assert quality.dict_hit_rate == 1.0
        assert not quality.needs_ocr


class TestPagesThatNeedOcr:
    def test_an_image_only_page_has_nothing(self) -> None:
        quality = assess_text("", min_chars=100, min_dict_ratio=0.35)
        assert quality.needs_ocr
        assert quality.reason == "too_few_chars"
        assert quality.char_count == 0
        assert quality.alnum_ratio == 0.0
        assert quality.dict_hit_rate == 0.0
        assert quality.token_count == 0

    def test_a_page_stamp_alone_is_not_a_text_layer(self) -> None:
        quality = assess_text("Page 1 of 1", min_chars=100, min_dict_ratio=0.35)
        assert quality.needs_ocr
        assert quality.reason == "too_few_chars"

    def test_gibberish_is_caught_even_when_long_enough(self) -> None:
        quality = assess_text(GIBBERISH, min_chars=100, min_dict_ratio=0.35)
        assert quality.char_count >= 100, "the sample must clear the character threshold"
        assert quality.needs_ocr
        assert quality.reason == "low_dictionary_hits"
        assert quality.dict_hit_rate < 0.1

    def test_symbol_soup_scores_a_low_alphanumeric_ratio(self) -> None:
        quality = assess_text(SYMBOL_SOUP, min_chars=100, min_dict_ratio=0.35)
        assert quality.alnum_ratio < 0.5
        assert quality.needs_ocr

    def test_a_long_alphanumeric_blob_is_not_an_identifier(self) -> None:
        quality = assess_text(
            "QX7ZP2LKMN4TRV9WBH3JD8FGY6C5S0A1E " * 4, min_chars=10, min_dict_ratio=0.35
        )
        assert quality.dict_hit_rate == 0.0
        assert quality.reason == "low_dictionary_hits"


class TestThresholds:
    @pytest.mark.parametrize(("min_chars", "expected"), [(10, False), (1000, True)])
    def test_the_character_threshold_decides(self, min_chars: int, expected: bool) -> None:
        assert (
            assess_text(CLEAN_ENGLISH, min_chars=min_chars, min_dict_ratio=0.35).needs_ocr
            is expected
        )

    @pytest.mark.parametrize(("min_ratio", "expected"), [(0.35, False), (0.999, True)])
    def test_the_dictionary_threshold_decides(self, min_ratio: float, expected: bool) -> None:
        # The sample's own hit rate sits between the two thresholds.
        quality = assess_text(CLEAN_ENGLISH, min_chars=100, min_dict_ratio=min_ratio)
        assert quality.needs_ocr is expected

    def test_too_few_characters_is_reported_before_the_dictionary(self) -> None:
        # Both signals fail; the reason names the one an operator can act on.
        quality = assess_text("Xqzjvbk", min_chars=100, min_dict_ratio=0.35)
        assert quality.reason == "too_few_chars"


class TestTokenCounting:
    def test_single_characters_are_not_words(self) -> None:
        assert assess_text("a b c 1 2", min_chars=1, min_dict_ratio=0.0).token_count == 0

    def test_punctuation_is_not_a_word(self) -> None:
        assert assess_text("--- ... ,,,", min_chars=1, min_dict_ratio=0.0).token_count == 0

    def test_ratios_are_rounded_for_storage(self) -> None:
        quality = assess_text(CLEAN_ENGLISH, min_chars=100, min_dict_ratio=0.35)
        assert quality.alnum_ratio == round(quality.alnum_ratio, 4)
        assert quality.dict_hit_rate == round(quality.dict_hit_rate, 4)


class TestDictionary:
    def test_holds_both_languages(self) -> None:
        words = dictionary_words()
        assert "certificate" in words
        assert "lahore" in words
        assert "پیدائش" in words
        assert "لاہور" in words

    def test_is_loaded_once_per_process(self) -> None:
        assert dictionary_words() is dictionary_words()

    def test_is_normalised_and_lower_case(self) -> None:
        words = dictionary_words()
        assert "" not in words
        assert not any(word != word.lower() for word in list(words)[:5000])
