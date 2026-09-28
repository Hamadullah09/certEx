"""The frontend's copy of the field schema.

The generated TypeScript is checked in so the web build needs no Python, which means it
can fall behind. This test is the thing that stops that: add a field in Python without
regenerating, and it fails here rather than as a blank column nobody notices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.enums import CertificateType
from certex.fields import fields_for
from certex.fields.typescript import GENERATED_TYPES_PATH, render_typescript

pytestmark = pytest.mark.unit

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GENERATED = REPOSITORY_ROOT / GENERATED_TYPES_PATH


class TestTheCheckedInFile:
    def test_it_exists(self) -> None:
        assert GENERATED.is_file(), (
            f"{GENERATED_TYPES_PATH} is missing. Run: python -m certex.cli generate-types"
        )

    def test_it_matches_the_python_schema(self) -> None:
        assert GENERATED.read_text(encoding="utf-8") == render_typescript(), (
            f"{GENERATED_TYPES_PATH} is out of date. Run: python -m certex.cli generate-types"
        )

    def test_it_says_not_to_edit_it(self) -> None:
        assert "Do not edit" in GENERATED.read_text(encoding="utf-8")


class TestWhatIsGenerated:
    def test_every_field_of_every_type_is_there(self) -> None:
        rendered = render_typescript()
        for certificate_type in CertificateType:
            for spec in fields_for(certificate_type):
                assert f'name: "{spec.name}"' in rendered, spec.name

    def test_labels_come_across_for_the_ui(self) -> None:
        assert '"Name of child"' in render_typescript()

    def test_required_fields_are_marked(self) -> None:
        rendered = render_typescript()
        assert 'name: "certificate_number", label: "Certificate number"' in rendered
        assert rendered.count("required: true") >= 3

    def test_label_synonyms_stay_on_the_python_side(self) -> None:
        # Matching printed labels is the extractor's business, not the grid's.
        rendered = render_typescript()
        assert "labels_ur" not in rendered
        assert "بچے کا نام" not in rendered

    def test_generating_twice_gives_the_same_file(self) -> None:
        assert render_typescript() == render_typescript()

    def test_the_file_ends_with_a_newline(self) -> None:
        # Otherwise every regeneration shows as a change in the editor's diff.
        assert render_typescript().endswith("\n")
