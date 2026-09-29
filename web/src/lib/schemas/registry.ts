import { z } from "zod";

/**
 * Certificate types and their field schemas.
 *
 * Nothing here hardcodes Birth, Marriage or Death. The navigation is whatever the
 * server says this workspace holds, in the order it says, because an office can add
 * a type - domicile, succession - and it has to appear without a release.
 */

/** What a field means, as distinct from what it is called. */
export const fieldRoleSchema = z.enum([
  "none",
  "identifier",
  "secondary_reference",
  "subject_name",
  "party_name",
  "father_name",
  "mother_name",
  "spouse_name",
  "birth_date",
  "death_date",
  "marriage_date",
  "event_date",
  "registration_date",
  "issue_date",
  "age",
  "sex",
  "address",
  "place",
  "issuing_authority",
]);
export type FieldRole = z.infer<typeof fieldRoleSchema>;

export const fieldKindSchema = z.enum([
  "text",
  "name",
  "date",
  "time",
  "id_number",
  "reference",
  "sex",
  "number",
  "address",
]);
export type FieldKind = z.infer<typeof fieldKindSchema>;

/** One entry in the register navigation. */
export const certificateTypeSummarySchema = z.object({
  id: z.string().uuid(),
  key: z.string(),
  name: z.string(),
  description: z.string().nullish(),
  /** Which built-in classifier label this maps to, when it maps to one. */
  classifier_key: z.string().nullish(),
  position: z.number().int(),
  is_active: z.boolean(),
});
export type CertificateTypeSummary = z.infer<typeof certificateTypeSummarySchema>;

export const certificateTypeListSchema = z.array(certificateTypeSummarySchema);

export const fieldDefinitionSchema = z.object({
  name: z.string(),
  label: z.string(),
  kind: fieldKindSchema,
  role: fieldRoleSchema,
  required: z.boolean(),
  searchable: z.boolean(),
  unique: z.boolean(),
  description: z.string().nullish(),
  labels_en: z.array(z.string()),
  labels_ur: z.array(z.string()),
});
export type FieldDefinition = z.infer<typeof fieldDefinitionSchema>;

export const schemaVersionStatusSchema = z.enum(["DRAFT", "PUBLISHED", "ARCHIVED"]);

export const schemaVersionDetailSchema = z.object({
  id: z.string().uuid(),
  schema_id: z.string().uuid(),
  version: z.number().int(),
  status: schemaVersionStatusSchema,
  notes: z.string().nullish(),
  published_at: z.string().nullish(),
  created_at: z.string(),
  fields: z.array(fieldDefinitionSchema),
});
export type SchemaVersionDetail = z.infer<typeof schemaVersionDetailSchema>;

export const schemaSummarySchema = z.object({
  id: z.string().uuid(),
  certificate_type_id: z.string().uuid(),
  name: z.string(),
  description: z.string().nullish(),
  is_default: z.boolean(),
  created_at: z.string(),
  latest_version: z.number().int().nullish(),
  latest_version_id: z.string().uuid().nullish(),
});
export type SchemaSummary = z.infer<typeof schemaSummarySchema>;

export const schemaListSchema = z.array(schemaSummarySchema);

/**
 * A field's role, in words, for a column heading or a caption.
 *
 * Only the roles a screen needs to name are here; the rest fall back to the field's
 * own label, which is what the schema builder set.
 */
export const FIELD_ROLE_LABEL: Partial<Record<FieldRole, string>> = {
  identifier: "Certificate number",
  secondary_reference: "Registration number",
  subject_name: "Name",
  party_name: "Party",
  father_name: "Father",
  mother_name: "Mother",
  spouse_name: "Spouse",
  birth_date: "Date of birth",
  death_date: "Date of death",
  marriage_date: "Date of marriage",
  event_date: "Date",
  registration_date: "Registered",
  issue_date: "Issued",
  issuing_authority: "Issued by",
};
