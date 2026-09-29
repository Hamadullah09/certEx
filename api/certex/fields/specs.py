"""What each kind of certificate contains, and what each field is called.

Every field is declared once, here, with four things that the rest of the system reads
rather than restates:

* **kind** - what sort of value it is. This drives normalisation (a date becomes
  ISO 8601, a name is folded to canonical Unicode), the validation rules that apply,
  and how the review grid renders the cell.
* **labels** - what the field is called on a printed certificate, in English and in
  Urdu, in the variants Pakistani offices actually print. The rules engine matches
  these; a missing synonym is a missing value, so they are generous.
* **required** - whether a row without it is incomplete. Only the fields a certificate
  is useless without are required, because a flag on every row is a flag on none.
* **export order** - the order these become CSV columns.

Adding a field here adds it to the CSV, the review grid, the validation pass and the
generated TypeScript types at once.
"""

from __future__ import annotations

import enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from certex.enums import CertificateType, FieldRole

__all__ = [
    "FIELD_SCHEMA_VERSION",
    "FieldKind",
    "FieldSpec",
    "all_field_specs",
    "common_fields",
    "field_names_for",
    "fields_for",
    "spec_for",
]

FIELD_SCHEMA_VERSION: Final = "1.0"
"""Stored on every extraction. A change here is a change to what a row means."""


class FieldKind(str, enum.Enum):
    """What sort of value a field holds."""

    TEXT = "text"
    NAME = "name"
    DATE = "date"
    TIME = "time"
    ID_NUMBER = "id_number"
    """A national identity number (CNIC in Pakistan), with its own checks."""

    REFERENCE = "reference"
    """A certificate or registration number: an identifier, not a quantity."""

    SEX = "sex"
    NUMBER = "number"
    ADDRESS = "address"


class FieldSpec(BaseModel):
    """One field of one certificate schema."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Machine name; the CSV column header and JSON key.")
    label: str = Field(description="What a person calls it, in the UI and in reports.")
    kind: FieldKind
    required: bool = False
    labels_en: tuple[str, ...] = ()
    labels_ur: tuple[str, ...] = ()
    description: str | None = None

    role: FieldRole = FieldRole.NONE
    """What the field means. Lets generic code apply semantic rules - see FieldRole."""

    searchable: bool = False
    """Indexed for search. Only role-bearing fields can be, since search columns
    are role-mapped; a searchable field with no role is a schema-builder mistake."""

    unique: bool = False
    """Duplicate values are a conflict to be resolved, not silently accepted."""

    @property
    def synonyms(self) -> tuple[str, ...]:
        """Every printed label this field answers to."""
        return (self.label, *self.labels_en, *self.labels_ur)

    @property
    def is_identifier(self) -> bool:
        return self.role is FieldRole.IDENTIFIER


def _spec(
    name: str,
    label: str,
    kind: FieldKind,
    *,
    required: bool = False,
    en: tuple[str, ...] = (),
    ur: tuple[str, ...] = (),
    description: str | None = None,
    role: FieldRole = FieldRole.NONE,
    searchable: bool | None = None,
    unique: bool = False,
) -> FieldSpec:
    return FieldSpec(
        name=name,
        label=label,
        kind=kind,
        required=required,
        labels_en=en,
        labels_ur=ur,
        description=description,
        role=role,
        # A role-bearing field is worth searching on by default; that is most of
        # what a role is for.
        searchable=(role is not FieldRole.NONE) if searchable is None else searchable,
        unique=unique,
    )


# ---------------------------------------------------------------------------
# Common to every certificate
# ---------------------------------------------------------------------------
_COMMON: Final[tuple[FieldSpec, ...]] = (
    _spec(
        "certificate_number",
        "Certificate number",
        FieldKind.REFERENCE,
        required=True,
        en=("certificate no", "certificate number", "cert no", "certificate #", "serial no"),
        ur=("سرٹیفکیٹ نمبر", "سند نمبر"),
        description="The number printed on the certificate itself.",
        role=FieldRole.IDENTIFIER,
        unique=True,
    ),
    _spec(
        "registration_number",
        "Registration number",
        FieldKind.REFERENCE,
        en=("registration no", "registration number", "reg no", "record no", "entry no"),
        ur=("رجسٹریشن نمبر", "اندراج نمبر"),
        description="The number of the entry in the register, where it differs.",
        role=FieldRole.SECONDARY_REFERENCE,
    ),
    _spec(
        "registration_date",
        "Registration date",
        FieldKind.DATE,
        en=("date of registration", "registration date", "registered on", "date of entry"),
        ur=("تاریخ اندراج", "تاریخ رجسٹریشن"),
        role=FieldRole.REGISTRATION_DATE,
    ),
    _spec(
        "issuing_authority",
        "Issuing authority",
        FieldKind.TEXT,
        en=("issuing authority", "issued by", "union council", "local government", "authority"),
        ur=("جاری کنندہ", "یونین کونسل", "ادارہ"),
        role=FieldRole.ISSUING_AUTHORITY,
    ),
    _spec(
        "registrar_name",
        "Registrar",
        FieldKind.NAME,
        en=("registrar", "registrar name", "name of registrar", "secretary"),
        ur=("رجسٹرار", "سیکرٹری"),
    ),
    _spec(
        "date_of_issue",
        "Date of issue",
        FieldKind.DATE,
        en=("date of issue", "issue date", "issued on", "date issued"),
        ur=("تاریخ اجرا", "تاریخ جاری"),
        role=FieldRole.ISSUE_DATE,
    ),
)

# ---------------------------------------------------------------------------
# Birth
# ---------------------------------------------------------------------------
_BIRTH: Final[tuple[FieldSpec, ...]] = (
    _spec(
        "child_full_name",
        "Name of child",
        FieldKind.NAME,
        required=True,
        en=("name of child", "child's name", "child name", "name", "full name"),
        ur=("بچے کا نام", "نام بچہ", "نام"),
        role=FieldRole.SUBJECT_NAME,
    ),
    _spec("sex", "Sex", FieldKind.SEX, en=("sex", "gender"), ur=("جنس",), role=FieldRole.SEX),
    _spec(
        "date_of_birth",
        "Date of birth",
        FieldKind.DATE,
        required=True,
        en=("date of birth", "birth date", "dob", "born on"),
        ur=("تاریخ پیدائش", "تاریخ ولادت"),
        role=FieldRole.BIRTH_DATE,
    ),
    _spec(
        "time_of_birth",
        "Time of birth",
        FieldKind.TIME,
        en=("time of birth", "birth time"),
        ur=("وقت پیدائش",),
    ),
    _spec(
        "place_of_birth",
        "Place of birth",
        FieldKind.TEXT,
        en=("place of birth", "birth place", "hospital", "place"),
        ur=("جائے پیدائش", "مقام پیدائش"),
        role=FieldRole.PLACE,
    ),
    _spec(
        "father_full_name",
        "Father's name",
        FieldKind.NAME,
        en=("father's name", "fathers name", "name of father", "father"),
        ur=("والد کا نام", "نام والد", "ولدیت"),
        role=FieldRole.FATHER_NAME,
    ),
    _spec(
        "father_id_number",
        "Father's CNIC",
        FieldKind.ID_NUMBER,
        en=("father's cnic", "fathers cnic", "father cnic", "father's nic", "father's id"),
        ur=("والد کا شناختی کارڈ نمبر", "شناختی کارڈ نمبر والد"),
    ),
    _spec(
        "mother_full_name",
        "Mother's name",
        FieldKind.NAME,
        en=("mother's name", "mothers name", "name of mother", "mother"),
        ur=("والدہ کا نام", "نام والدہ"),
        role=FieldRole.MOTHER_NAME,
    ),
    _spec(
        "mother_id_number",
        "Mother's CNIC",
        FieldKind.ID_NUMBER,
        en=("mother's cnic", "mothers cnic", "mother cnic", "mother's nic", "mother's id"),
        ur=("والدہ کا شناختی کارڈ نمبر", "شناختی کارڈ نمبر والدہ"),
    ),
    _spec(
        "permanent_address",
        "Permanent address",
        FieldKind.ADDRESS,
        en=("permanent address", "address", "residence", "home address"),
        ur=("مستقل پتہ", "پتہ", "رہائش"),
        role=FieldRole.ADDRESS,
    ),
    _spec(
        "informant_name",
        "Informant",
        FieldKind.NAME,
        en=("informant", "informant name", "reported by", "name of informant"),
        ur=("اطلاع دہندہ", "مخبر"),
    ),
)

# ---------------------------------------------------------------------------
# Death
# ---------------------------------------------------------------------------
_DEATH: Final[tuple[FieldSpec, ...]] = (
    _spec(
        "deceased_full_name",
        "Name of deceased",
        FieldKind.NAME,
        required=True,
        en=("name of deceased", "deceased name", "deceased's name", "name", "full name"),
        ur=("متوفی کا نام", "نام متوفی", "نام"),
        role=FieldRole.SUBJECT_NAME,
    ),
    _spec("sex", "Sex", FieldKind.SEX, en=("sex", "gender"), ur=("جنس",), role=FieldRole.SEX),
    _spec(
        "date_of_birth",
        "Date of birth",
        FieldKind.DATE,
        en=("date of birth", "birth date", "dob"),
        ur=("تاریخ پیدائش",),
        role=FieldRole.BIRTH_DATE,
    ),
    _spec(
        "date_of_death",
        "Date of death",
        FieldKind.DATE,
        required=True,
        en=("date of death", "death date", "died on", "dod"),
        ur=("تاریخ وفات", "تاریخ موت"),
        role=FieldRole.DEATH_DATE,
    ),
    _spec(
        "age_at_death",
        "Age at death",
        FieldKind.NUMBER,
        en=("age at death", "age", "age of deceased"),
        ur=("عمر", "عمر بوقت وفات"),
        role=FieldRole.AGE,
    ),
    _spec(
        "place_of_death",
        "Place of death",
        FieldKind.TEXT,
        en=("place of death", "death place", "hospital", "place"),
        ur=("جائے وفات", "مقام وفات"),
        role=FieldRole.PLACE,
    ),
    _spec(
        "cause_of_death",
        "Cause of death",
        FieldKind.TEXT,
        en=("cause of death", "reason of death", "cause"),
        ur=("وجہ وفات", "سبب وفات"),
    ),
    _spec(
        "father_name",
        "Father's name",
        FieldKind.NAME,
        en=("father's name", "fathers name", "name of father", "father"),
        ur=("والد کا نام", "ولدیت"),
        role=FieldRole.FATHER_NAME,
    ),
    _spec(
        "spouse_name",
        "Spouse's name",
        FieldKind.NAME,
        en=("spouse's name", "spouse name", "husband's name", "wife's name", "spouse"),
        ur=("شریک حیات کا نام", "خاوند کا نام", "بیوی کا نام"),
        role=FieldRole.SPOUSE_NAME,
    ),
    _spec(
        "deceased_id_number",
        "CNIC",
        FieldKind.ID_NUMBER,
        en=("cnic", "cnic no", "nic", "identity card number", "id number"),
        ur=("شناختی کارڈ نمبر", "قومی شناختی کارڈ"),
    ),
    _spec(
        "permanent_address",
        "Permanent address",
        FieldKind.ADDRESS,
        en=("permanent address", "address", "residence"),
        ur=("مستقل پتہ", "پتہ"),
        role=FieldRole.ADDRESS,
    ),
    _spec(
        "informant_name",
        "Informant",
        FieldKind.NAME,
        en=("informant", "informant name", "reported by"),
        ur=("اطلاع دہندہ", "مخبر"),
    ),
)

# ---------------------------------------------------------------------------
# Marriage
# ---------------------------------------------------------------------------
_MARRIAGE: Final[tuple[FieldSpec, ...]] = (
    _spec(
        "date_of_marriage",
        "Date of marriage",
        FieldKind.DATE,
        required=True,
        en=("date of marriage", "marriage date", "date of nikah", "married on"),
        ur=("تاریخ نکاح", "تاریخ شادی"),
        role=FieldRole.MARRIAGE_DATE,
    ),
    _spec(
        "place_of_marriage",
        "Place of marriage",
        FieldKind.TEXT,
        en=("place of marriage", "marriage place", "place of nikah", "venue"),
        ur=("جائے نکاح", "مقام نکاح"),
        role=FieldRole.PLACE,
    ),
    _spec(
        "groom_full_name",
        "Groom's name",
        FieldKind.NAME,
        required=True,
        en=("groom's name", "grooms name", "name of groom", "bridegroom", "husband's name"),
        ur=("دولہا کا نام", "نام دولہا", "خاوند کا نام"),
        role=FieldRole.PARTY_NAME,
    ),
    _spec(
        "groom_date_of_birth",
        "Groom's date of birth",
        FieldKind.DATE,
        en=("groom's date of birth", "groom date of birth", "groom's dob", "groom's age"),
        ur=("دولہا کی تاریخ پیدائش",),
        role=FieldRole.BIRTH_DATE,
    ),
    _spec(
        "groom_id_number",
        "Groom's CNIC",
        FieldKind.ID_NUMBER,
        en=("groom's cnic", "grooms cnic", "groom cnic", "groom's nic", "groom's id"),
        ur=("دولہا کا شناختی کارڈ نمبر",),
    ),
    _spec(
        "groom_father_name",
        "Groom's father",
        FieldKind.NAME,
        en=("groom's father", "grooms father", "father of groom", "groom's father's name"),
        ur=("دولہا کے والد کا نام", "ولدیت دولہا"),
        role=FieldRole.FATHER_NAME,
    ),
    _spec(
        "bride_full_name",
        "Bride's name",
        FieldKind.NAME,
        required=True,
        en=("bride's name", "brides name", "name of bride", "wife's name"),
        ur=("دلہن کا نام", "نام دلہن", "بیوی کا نام"),
        role=FieldRole.PARTY_NAME,
    ),
    _spec(
        "bride_date_of_birth",
        "Bride's date of birth",
        FieldKind.DATE,
        en=("bride's date of birth", "bride date of birth", "bride's dob", "bride's age"),
        ur=("دلہن کی تاریخ پیدائش",),
        role=FieldRole.BIRTH_DATE,
    ),
    _spec(
        "bride_id_number",
        "Bride's CNIC",
        FieldKind.ID_NUMBER,
        en=("bride's cnic", "brides cnic", "bride cnic", "bride's nic", "bride's id"),
        ur=("دلہن کا شناختی کارڈ نمبر",),
    ),
    _spec(
        "bride_father_name",
        "Bride's father",
        FieldKind.NAME,
        en=("bride's father", "brides father", "father of bride", "bride's father's name"),
        ur=("دلہن کے والد کا نام", "ولدیت دلہن"),
        role=FieldRole.FATHER_NAME,
    ),
    _spec(
        "dower_amount",
        "Dower (mehr)",
        FieldKind.NUMBER,
        en=("dower amount", "dower", "mehr", "mahr", "haq mehr"),
        ur=("حق مہر", "مہر"),
    ),
    _spec(
        "witness_1_name",
        "Witness 1",
        FieldKind.NAME,
        en=("witness 1", "witness i", "first witness", "witness one"),
        ur=("گواہ 1", "پہلا گواہ"),
    ),
    _spec(
        "witness_2_name",
        "Witness 2",
        FieldKind.NAME,
        en=("witness 2", "witness ii", "second witness", "witness two"),
        ur=("گواہ 2", "دوسرا گواہ"),
    ),
    _spec(
        "officiant_name",
        "Officiant",
        FieldKind.NAME,
        en=("officiant", "nikah khawan", "solemnised by", "performed by", "qazi"),
        ur=("نکاح خوان", "قاضی"),
    ),
)

_BY_TYPE: Final[dict[CertificateType, tuple[FieldSpec, ...]]] = {
    CertificateType.BIRTH: _BIRTH,
    CertificateType.DEATH: _DEATH,
    CertificateType.MARRIAGE: _MARRIAGE,
    CertificateType.OTHER: (),
}


def common_fields() -> tuple[FieldSpec, ...]:
    """Fields every certificate has, in export order."""
    return _COMMON


def fields_for(certificate_type: CertificateType) -> tuple[FieldSpec, ...]:
    """Every field of a certificate type, common fields first, in export order.

    A unit of unknown type still gets the common fields: a certificate number and an
    issuing authority are worth having even when nothing else could be read.
    """
    return (*_COMMON, *_BY_TYPE[certificate_type])


def field_names_for(certificate_type: CertificateType) -> tuple[str, ...]:
    return tuple(spec.name for spec in fields_for(certificate_type))


def spec_for(certificate_type: CertificateType, name: str) -> FieldSpec | None:
    return next(
        (spec for spec in fields_for(certificate_type) if spec.name == name),
        None,
    )


def all_field_specs() -> dict[CertificateType, tuple[FieldSpec, ...]]:
    """Every type's fields, for the CSV writer and the type generator."""
    return {kind: fields_for(kind) for kind in CertificateType}
