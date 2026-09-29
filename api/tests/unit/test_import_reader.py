"""Reading a clerk's CSV.

Nothing about a spreadsheet a person saved can be assumed: the encoding, the
delimiter, the column order, the byte-order mark and the quoting are all whatever the
machine that wrote it chose. None of that is an error and all of it has to work. What
must be caught is a column the schema cannot place, because filing half a file and
stopping leaves an office with a register it cannot trust.
"""

from __future__ import annotations

import csv
import io

import pytest

from certex.enums import CertificateType, FieldRole
from certex.fields import FieldKind, FieldSchema, FieldSpec, builtin_schema
from certex.imports.reader import (
    MAX_FIELD_CHARS,
    decode_stream,
    detect_delimiter,
    plan_headers,
    read_rows,
)
from certex.imports.template import GUIDANCE_MARKER, template_csv, template_headers

pytestmark = pytest.mark.unit


def spec(name: str, role: FieldRole = FieldRole.NONE, *, required: bool = False) -> FieldSpec:
    return FieldSpec(
        name=name,
        label=name.replace("_", " ").title(),
        kind=FieldKind.TEXT,
        role=role,
        required=required,
    )


def small_schema() -> FieldSchema:
    return FieldSchema(
        fields=(
            spec("certificate_number", FieldRole.IDENTIFIER, required=True),
            spec("child_name", FieldRole.SUBJECT_NAME, required=True),
            spec("village"),
        ),
        certificate_type=CertificateType.BIRTH,
    )


def rows_of(
    text: str, schema: FieldSchema | None = None, *, delimiter: str = ","
) -> list[dict[str, str]]:
    """Every data row of a CSV given as a string, mapped onto field names."""
    used = schema or small_schema()
    lines, _encoding = decode_stream(io.BytesIO(text.encode("utf-8")))
    plan = plan_headers(used, next(lines).split(delimiter))
    return [row.values for row in read_rows(lines, plan, delimiter=delimiter)]


class TestEncoding:
    def test_plain_utf8(self) -> None:
        lines, encoding = decode_stream(io.BytesIO(b"a,b\n1,2\n"))
        assert encoding == "utf-8"
        assert list(lines) == ["a,b", "1,2"]

    def test_a_byte_order_mark_is_not_part_of_the_first_heading(self) -> None:
        """Excel writes one, and a heading of "﻿certificate_number" matches nothing."""
        payload = "﻿certificate_number,child_name\nBC-1,Ayesha\n".encode()
        lines, encoding = decode_stream(io.BytesIO(payload))
        assert encoding == "utf-8-sig"
        assert next(lines) == "certificate_number,child_name"

    def test_windows_1252_is_read_rather_than_refused(self) -> None:
        payload = "certificate_number,child_name\nBC-1,Ren\xe9\n".encode("cp1252")
        lines, encoding = decode_stream(io.BytesIO(payload))
        assert encoding in ("cp1252", "latin-1")
        assert "Ren" in list(lines)[1]

    def test_urdu_survives(self) -> None:
        payload = "certificate_number,child_name\nBC-1,عائشہ\n"
        lines, _encoding = decode_stream(io.BytesIO(payload.encode()))
        assert list(lines)[1].endswith("عائشہ")

    def test_lines_are_not_all_read_at_once(self) -> None:
        """A generator, not a list: the point is that a 300 MB file never lands in memory."""
        lines, _encoding = decode_stream(io.BytesIO(b"a\nb\nc\n"))
        assert next(lines) == "a"

    def test_crlf_endings_leave_no_stray_carriage_return(self) -> None:
        lines, _encoding = decode_stream(io.BytesIO(b"a,b\r\n1,2\r\n"))
        assert [line.rstrip("\r") for line in lines] == ["a,b", "1,2"]


class TestDelimiter:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("a,b,c", ","),
            ("a;b;c", ";"),
            ("a\tb\tc", "\t"),
            ("a|b|c", "|"),
            ("single", ","),
        ],
    )
    def test_the_header_decides(self, header: str, expected: str) -> None:
        assert detect_delimiter(header) == expected

    def test_urdu_in_the_header_does_not_confuse_it(self) -> None:
        assert detect_delimiter("سند نمبر,نام") == ","


class TestHeaderPlan:
    def test_field_keys_match(self) -> None:
        plan = plan_headers(small_schema(), ["certificate_number", "child_name", "village"])
        assert plan.usable
        assert plan.columns == ("certificate_number", "child_name", "village")

    def test_labels_match_too(self) -> None:
        """A register kept for years has "Certificate Number" at the top, not a key."""
        plan = plan_headers(small_schema(), ["Certificate Number", "Child Name", "Village"])
        assert plan.usable
        assert plan.columns == ("certificate_number", "child_name", "village")

    def test_case_spacing_and_underscores_do_not_matter(self) -> None:
        plan = plan_headers(small_schema(), ["  CERTIFICATE-NUMBER ", "child name", "Village"])
        assert plan.usable

    def test_column_order_does_not_matter(self) -> None:
        plan = plan_headers(small_schema(), ["village", "child_name", "certificate_number"])
        assert plan.usable
        assert plan.columns == ("village", "child_name", "certificate_number")

    def test_an_unknown_column_makes_the_file_unusable(self) -> None:
        plan = plan_headers(small_schema(), ["certificate_number", "child_name", "favourite"])
        assert not plan.usable
        assert plan.unknown == ("favourite",)

    def test_a_missing_required_column_makes_it_unusable(self) -> None:
        plan = plan_headers(small_schema(), ["certificate_number", "village"])
        assert not plan.usable
        assert "child_name" in plan.missing_required

    def test_a_missing_identifier_is_reported_by_name(self) -> None:
        plan = plan_headers(small_schema(), ["child_name", "village"])
        assert "certificate_number" in plan.missing_required

    def test_two_columns_for_one_field_are_refused(self) -> None:
        plan = plan_headers(
            small_schema(), ["certificate_number", "Certificate Number", "child_name"]
        )
        assert not plan.usable
        assert plan.duplicated == ("certificate_number",)

    def test_a_blank_heading_is_ignored_not_reported(self) -> None:
        """A trailing comma in the header is a spreadsheet artefact, not a column."""
        plan = plan_headers(small_schema(), ["certificate_number", "child_name", "village", ""])
        assert plan.usable
        assert plan.columns[-1] is None


class TestRows:
    def test_values_land_under_their_field_names(self) -> None:
        rows = rows_of("certificate_number,child_name,village\nBC-1,Ayesha Noor,Shahdara\n")
        assert rows == [
            {"certificate_number": "BC-1", "child_name": "Ayesha Noor", "village": "Shahdara"}
        ]

    def test_row_numbers_are_the_lines_a_person_sees(self) -> None:
        """The header is line 1, so the first data row is line 2."""
        lines, _encoding = decode_stream(
            io.BytesIO(b"certificate_number,child_name\nBC-1,A\nBC-2,B\n")
        )
        plan = plan_headers(small_schema(), next(lines).split(","))
        assert [row.row_number for row in read_rows(lines, plan, delimiter=",")] == [2, 3]

    def test_blank_rows_are_skipped_silently(self) -> None:
        rows = rows_of("certificate_number,child_name\nBC-1,A\n\n,\nBC-2,B\n")
        assert [row["certificate_number"] for row in rows] == ["BC-1", "BC-2"]

    def test_quoted_values_with_commas_survive(self) -> None:
        rows = rows_of('certificate_number,child_name\nBC-1,"Noor, Ayesha"\n')
        assert rows[0]["child_name"] == "Noor, Ayesha"

    def test_surrounding_space_is_trimmed(self) -> None:
        rows = rows_of("certificate_number,child_name\n  BC-1 ,  Ayesha  \n")
        assert rows[0] == {"certificate_number": "BC-1", "child_name": "Ayesha"}

    def test_an_empty_cell_is_absent_rather_than_blank(self) -> None:
        rows = rows_of("certificate_number,child_name,village\nBC-1,Ayesha,\n")
        assert "village" not in rows[0]

    def test_a_row_with_extra_values_is_flagged_not_silently_truncated(self) -> None:
        lines, _encoding = decode_stream(
            io.BytesIO(b"certificate_number,child_name\nBC-1,A,leftover\n")
        )
        plan = plan_headers(small_schema(), next(lines).split(","))
        row = next(iter(read_rows(lines, plan, delimiter=",")))
        assert row.too_many_columns

    def test_a_short_row_is_read_as_far_as_it_goes(self) -> None:
        lines, _encoding = decode_stream(
            io.BytesIO(b"certificate_number,child_name,village\nBC-1,A\n")
        )
        plan = plan_headers(small_schema(), next(lines).split(","))
        row = next(iter(read_rows(lines, plan, delimiter=",")))
        assert row.too_few_columns
        assert row.values == {"certificate_number": "BC-1", "child_name": "A"}

    def test_an_enormous_cell_is_bounded(self) -> None:
        payload = "certificate_number,child_name\nBC-1," + "x" * (MAX_FIELD_CHARS + 500) + "\n"
        lines, _encoding = decode_stream(io.BytesIO(payload.encode()))
        plan = plan_headers(small_schema(), next(lines).split(","))
        row = next(iter(read_rows(lines, plan, delimiter=",")))
        assert row.oversized
        assert len(row.values["child_name"]) == MAX_FIELD_CHARS

    def test_semicolon_files_read_the_same(self) -> None:
        rows = rows_of("certificate_number;child_name\nBC-1;Ayesha\n", delimiter=";")
        assert rows[0]["child_name"] == "Ayesha"


class TestTemplate:
    def test_the_headers_are_the_field_keys_in_schema_order(self) -> None:
        schema = builtin_schema(CertificateType.BIRTH)
        assert template_headers(schema) == list(schema.names)

    def test_the_template_round_trips_through_the_reader(self) -> None:
        """The file the system hands out must be a file the system accepts."""
        schema = builtin_schema(CertificateType.MARRIAGE)
        text = template_csv(schema)
        lines, encoding = decode_stream(io.BytesIO(text.encode("utf-8")))
        assert encoding == "utf-8-sig"
        header = next(lines)
        plan = plan_headers(schema, header.split(","))
        assert plan.usable, plan

    def test_the_guidance_row_is_not_read_as_data(self) -> None:
        schema = small_schema()
        text = template_csv(schema) + "BC-1,Ayesha,Shahdara\r\n"
        lines, _encoding = decode_stream(io.BytesIO(text.encode("utf-8")))
        plan = plan_headers(schema, next(lines).split(","))
        rows = list(read_rows(lines, plan, delimiter=","))
        assert [row.values["certificate_number"] for row in rows] == ["BC-1"]

    def test_the_guidance_row_marks_itself(self) -> None:
        """Read as CSV, because a cell holding a comma arrives quoted."""
        text = template_csv(small_schema(), include_bom=False)
        second = next(csv.reader(io.StringIO(text.splitlines()[1])))
        assert all(cell.startswith(GUIDANCE_MARKER) for cell in second)

    def test_the_guidance_names_the_identifier(self) -> None:
        text = template_csv(small_schema())
        assert "certificate number" in text.splitlines()[1].lower()

    def test_a_heading_that_could_be_a_formula_is_neutralised(self) -> None:
        """Excel executes a cell beginning with = or -, whatever it is a heading for."""
        schema = FieldSchema(
            fields=(spec("certificate_number", FieldRole.IDENTIFIER, required=True),)
        )
        text = template_csv(schema, include_bom=False)
        assert not text.lstrip().startswith("=")
