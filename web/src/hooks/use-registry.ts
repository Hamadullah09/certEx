"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { type SchemaSummary, schemaListSchema } from "@/lib/schemas/registry";

export const registryKeys = {
  all: ["registry"] as const,
  schemas: (certificateTypeId: string) => ["registry", "schemas", certificateTypeId] as const,
};

/** Every schema defined for one certificate category. */
export function useSchemas(certificateTypeId: string | null | undefined) {
  return useQuery<SchemaSummary[]>({
    queryKey: registryKeys.schemas(certificateTypeId ?? ""),
    queryFn: () =>
      apiFetch(
        `/api/v1/schemas?certificate_type_id=${certificateTypeId}`,
        schemaListSchema,
      ),
    enabled: Boolean(certificateTypeId),
    staleTime: 60_000,
  });
}

/**
 * The schema a new batch in this category starts from.
 *
 * The category's default where it has one, otherwise the newest it has. A category an
 * administrator has just added has neither, and the caller offers an empty column list
 * instead - which is correct, not an error: nobody has said yet what is on those forms.
 */
export function useDefaultSchemaVersion(certificateTypeId: string | null | undefined) {
  const schemas = useSchemas(certificateTypeId);
  const chosen =
    (schemas.data ?? []).find((schema) => schema.is_default) ?? (schemas.data ?? [])[0];
  return { ...schemas, data: chosen };
}
