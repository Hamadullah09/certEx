// Generated from api/certex/fields/specs.py by `python -m certex.cli generate-types`.
// Do not edit: a test fails when this file and the Python schema disagree.

export const FIELD_SCHEMA_VERSION = "1.0";

export type FieldKind = "text" | "name" | "date" | "time" | "id_number" | "reference" | "sex" | "number" | "address";

export type CertificateType = "BIRTH" | "MARRIAGE" | "DEATH" | "OTHER";

export interface FieldSpec {
  /** Machine name: the JSON key and the CSV column header. */
  readonly name: string;
  /** What a person calls it, in the grid and in the download bar. */
  readonly label: string;
  readonly kind: FieldKind;
  /** A row missing this field is incomplete. */
  readonly required: boolean;
}

/** Fields every certificate has, in export order. */
export const COMMON_FIELDS: readonly FieldSpec[] = [
  { name: "certificate_number", label: "Certificate number", kind: "reference", required: true },
  { name: "registration_number", label: "Registration number", kind: "reference", required: false },
  { name: "registration_date", label: "Registration date", kind: "date", required: false },
  { name: "issuing_authority", label: "Issuing authority", kind: "text", required: false },
  { name: "registrar_name", label: "Registrar", kind: "name", required: false },
  { name: "date_of_issue", label: "Date of issue", kind: "date", required: false },
];

/** Every field of every certificate type, common fields first, in export order. */
export const FIELDS_BY_TYPE: Record<CertificateType, readonly FieldSpec[]> = {
  BIRTH: [
    { name: "certificate_number", label: "Certificate number", kind: "reference", required: true },
    { name: "registration_number", label: "Registration number", kind: "reference", required: false },
    { name: "registration_date", label: "Registration date", kind: "date", required: false },
    { name: "issuing_authority", label: "Issuing authority", kind: "text", required: false },
    { name: "registrar_name", label: "Registrar", kind: "name", required: false },
    { name: "date_of_issue", label: "Date of issue", kind: "date", required: false },
    { name: "child_full_name", label: "Name of child", kind: "name", required: true },
    { name: "sex", label: "Sex", kind: "sex", required: false },
    { name: "date_of_birth", label: "Date of birth", kind: "date", required: true },
    { name: "time_of_birth", label: "Time of birth", kind: "time", required: false },
    { name: "place_of_birth", label: "Place of birth", kind: "text", required: false },
    { name: "father_full_name", label: "Father's name", kind: "name", required: false },
    { name: "father_id_number", label: "Father's CNIC", kind: "id_number", required: false },
    { name: "mother_full_name", label: "Mother's name", kind: "name", required: false },
    { name: "mother_id_number", label: "Mother's CNIC", kind: "id_number", required: false },
    { name: "permanent_address", label: "Permanent address", kind: "address", required: false },
    { name: "informant_name", label: "Informant", kind: "name", required: false },
  ],
  MARRIAGE: [
    { name: "certificate_number", label: "Certificate number", kind: "reference", required: true },
    { name: "registration_number", label: "Registration number", kind: "reference", required: false },
    { name: "registration_date", label: "Registration date", kind: "date", required: false },
    { name: "issuing_authority", label: "Issuing authority", kind: "text", required: false },
    { name: "registrar_name", label: "Registrar", kind: "name", required: false },
    { name: "date_of_issue", label: "Date of issue", kind: "date", required: false },
    { name: "date_of_marriage", label: "Date of marriage", kind: "date", required: true },
    { name: "place_of_marriage", label: "Place of marriage", kind: "text", required: false },
    { name: "groom_full_name", label: "Groom's name", kind: "name", required: true },
    { name: "groom_date_of_birth", label: "Groom's date of birth", kind: "date", required: false },
    { name: "groom_id_number", label: "Groom's CNIC", kind: "id_number", required: false },
    { name: "groom_father_name", label: "Groom's father", kind: "name", required: false },
    { name: "bride_full_name", label: "Bride's name", kind: "name", required: true },
    { name: "bride_date_of_birth", label: "Bride's date of birth", kind: "date", required: false },
    { name: "bride_id_number", label: "Bride's CNIC", kind: "id_number", required: false },
    { name: "bride_father_name", label: "Bride's father", kind: "name", required: false },
    { name: "dower_amount", label: "Dower (mehr)", kind: "number", required: false },
    { name: "witness_1_name", label: "Witness 1", kind: "name", required: false },
    { name: "witness_2_name", label: "Witness 2", kind: "name", required: false },
    { name: "officiant_name", label: "Officiant", kind: "name", required: false },
  ],
  DEATH: [
    { name: "certificate_number", label: "Certificate number", kind: "reference", required: true },
    { name: "registration_number", label: "Registration number", kind: "reference", required: false },
    { name: "registration_date", label: "Registration date", kind: "date", required: false },
    { name: "issuing_authority", label: "Issuing authority", kind: "text", required: false },
    { name: "registrar_name", label: "Registrar", kind: "name", required: false },
    { name: "date_of_issue", label: "Date of issue", kind: "date", required: false },
    { name: "deceased_full_name", label: "Name of deceased", kind: "name", required: true },
    { name: "sex", label: "Sex", kind: "sex", required: false },
    { name: "date_of_birth", label: "Date of birth", kind: "date", required: false },
    { name: "date_of_death", label: "Date of death", kind: "date", required: true },
    { name: "age_at_death", label: "Age at death", kind: "number", required: false },
    { name: "place_of_death", label: "Place of death", kind: "text", required: false },
    { name: "cause_of_death", label: "Cause of death", kind: "text", required: false },
    { name: "father_name", label: "Father's name", kind: "name", required: false },
    { name: "spouse_name", label: "Spouse's name", kind: "name", required: false },
    { name: "deceased_id_number", label: "CNIC", kind: "id_number", required: false },
    { name: "permanent_address", label: "Permanent address", kind: "address", required: false },
    { name: "informant_name", label: "Informant", kind: "name", required: false },
  ],
  OTHER: [
    { name: "certificate_number", label: "Certificate number", kind: "reference", required: true },
    { name: "registration_number", label: "Registration number", kind: "reference", required: false },
    { name: "registration_date", label: "Registration date", kind: "date", required: false },
    { name: "issuing_authority", label: "Issuing authority", kind: "text", required: false },
    { name: "registrar_name", label: "Registrar", kind: "name", required: false },
    { name: "date_of_issue", label: "Date of issue", kind: "date", required: false },
  ],
};

export function fieldsFor(certificateType: CertificateType): readonly FieldSpec[] {
  return FIELDS_BY_TYPE[certificateType] ?? COMMON_FIELDS;
}
