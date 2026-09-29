"""Comparison keys for numbers and names.

These keys decide which records are brought together for a person to look at. Too
strict and a duplicate slips into the register unnoticed; too loose and every
search drowns in namesakes. The cases below are the ones that actually occur in
Pakistani civil records: the same number printed with different separators, Urdu
digits, and names that differ by an honorific or a middle name.
"""

from __future__ import annotations

import pytest

from certex.certificates import name_key, name_tokens, number_key, shares_a_name

pytestmark = pytest.mark.unit


class TestNumberKey:
    @pytest.mark.parametrize(
        "printed",
        [
            "REG/LHR/2019/88213",
            "REG-LHR-2019-88213",
            "reg lhr 2019 88213",
            "  REG / LHR / 2019 / 88213  ",
            "Reg.LHR.2019.88213",
        ],
    )
    def test_separators_and_case_do_not_matter(self, printed: str) -> None:
        assert number_key(printed) == "reglhr201988213"

    def test_urdu_digits_read_as_the_same_number(self) -> None:
        """A bilingual certificate prints the number in both scripts."""
        assert number_key("BC-۲۰۱۹-۴۲") == number_key("BC-2019-42")

    def test_different_numbers_stay_different(self) -> None:
        assert number_key("BC-2019-42") != number_key("BC-2019-43")

    def test_nothing_in_gives_nothing_out(self) -> None:
        assert number_key(None) == ""
        assert number_key("") == ""
        assert number_key("   ") == ""
        assert number_key("///---") == ""

    def test_urdu_letters_survive(self) -> None:
        """An office that prints its prefix in Urdu still gets a usable key."""
        assert number_key("سند/2019/42") == "سند201942"

    def test_the_key_is_bounded(self) -> None:
        assert len(number_key("A" * 5000)) == 200


class TestNameTokens:
    def test_words_are_kept_in_order(self) -> None:
        assert name_tokens("Ahmed Ali Khan") == ("ahmed", "ali", "khan")

    def test_extra_spacing_collapses(self) -> None:
        assert name_tokens("  Ahmed   Ali  ") == ("ahmed", "ali")

    @pytest.mark.parametrize(
        "printed",
        ["Mr Ahmed Ali", "Dr. Ahmed Ali", "Syed Ahmed Ali", "Hafiz Ahmed Ali", "Late Ahmed Ali"],
    )
    def test_a_leading_title_is_dropped(self, printed: str) -> None:
        assert name_tokens(printed) == ("ahmed", "ali")

    def test_a_family_name_that_is_also_a_title_is_kept(self) -> None:
        """Malik at the end of a name is the family name, not an honorific."""
        assert name_tokens("Tariq Mahmood Malik") == ("tariq", "mahmood", "malik")

    def test_a_title_only_counts_at_the_front(self) -> None:
        assert name_tokens("Malik Tariq Mahmood") == ("tariq", "mahmood")

    def test_urdu_names_tokenise(self) -> None:
        assert name_tokens("احمد علی") == (
            "احمد",
            "علی",
        )

    def test_punctuation_is_not_a_name(self) -> None:
        assert name_tokens("---") == ()
        assert name_tokens(None) == ()


class TestNameKey:
    def test_it_reads_as_the_words_joined(self) -> None:
        assert name_key("Mr  Ahmed   Ali ") == "ahmed ali"

    def test_two_spellings_of_one_name_stay_close(self) -> None:
        """Not equal - that is the trigram index's job - but close enough for it."""
        left, right = name_key("Muhammad Ahmed"), name_key("Mohammad Ahmed")
        assert left != right
        assert left.split()[-1] == right.split()[-1]

    def test_it_is_bounded(self) -> None:
        assert len(name_key("Ahmed " * 500)) <= 200


class TestSharesAName:
    def test_the_same_name_shares(self) -> None:
        assert shares_a_name("Ahmed Ali", "ahmed ali")

    def test_a_missing_middle_name_still_shares(self) -> None:
        """The commonest reason one person's two certificates disagree."""
        assert shares_a_name("Ahmed Ali", "Muhammad Ahmed Ali")

    def test_an_honorific_does_not_break_it(self) -> None:
        assert shares_a_name("Mr Ahmed Ali", "Ahmed Ali")

    def test_different_people_do_not_share(self) -> None:
        assert not shares_a_name("Ahmed Ali", "Bilal Hussain")

    def test_one_word_in_common_is_not_enough(self) -> None:
        """Otherwise every Ahmed in the register would be the same person."""
        assert not shares_a_name("Ahmed Ali", "Ahmed Hussain")

    def test_an_absent_name_shares_with_nothing(self) -> None:
        assert not shares_a_name(None, "Ahmed Ali")
        assert not shares_a_name("Ahmed Ali", "")
