"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import {
  type CertificateDetail,
  type SearchResponse,
  certificateDetailSchema,
  duplicateCandidateListSchema,
  searchResponseSchema,
} from "@/lib/schemas/certificates";
import {
  type CertificateTypeSummary,
  type SchemaVersionDetail,
  certificateTypeListSchema,
  schemaVersionDetailSchema,
} from "@/lib/schemas/registry";

/**
 * Reading the register: the navigation, a search, and one entry.
 *
 * The navigation comes from the server rather than from a constant, because the
 * types a workspace holds are its own - the three standard ones are seeded, and an
 * office can add a fourth.
 */

export const registerKeys = {
  all: ["register"] as const,
  types: ["register", "types"] as const,
  search: (params: SearchParams) => ["register", "search", params] as const,
  certificate: (id: string) => ["register", "certificate", id] as const,
  schemaVersion: (id: string) => ["register", "schema-version", id] as const,
  duplicates: (id: string) => ["register", "certificate", id, "duplicates"] as const,
};

export interface SearchParams {
  q?: string;
  certificateTypeId?: string;
  /** Only entries read from this batch. */
  batchId?: string;
  /** A configured column to search by name - any column a batch defines. */
  field?: string;
  fieldValue?: string;
  name?: string;
  fatherName?: string;
  eventDateFrom?: string;
  eventDateTo?: string;
  needsReview?: boolean;
  duplicatesOnly?: boolean;
  limit?: number;
  offset?: number;
}

export function useCertificateTypes() {
  return useQuery<CertificateTypeSummary[]>({
    queryKey: registerKeys.types,
    queryFn: () => apiFetch("/api/v1/certificate-types", certificateTypeListSchema),
    // The types change when an administrator adds one, which is rare, and the
    // navigation is on every screen - so it is held rather than refetched.
    staleTime: 5 * 60 * 1000,
  });
}

function searchQueryString(params: SearchParams): string {
  const query = new URLSearchParams();
  if (params.q?.trim()) query.set("q", params.q.trim());
  if (params.certificateTypeId) query.set("certificate_type_id", params.certificateTypeId);
  if (params.batchId) query.set("batch_id", params.batchId);
  if (params.field && params.fieldValue?.trim()) {
    query.set("field", params.field);
    query.set("field_value", params.fieldValue.trim());
  }
  if (params.name?.trim()) query.set("name", params.name.trim());
  if (params.fatherName?.trim()) query.set("father_name", params.fatherName.trim());
  if (params.eventDateFrom) query.set("event_date_from", params.eventDateFrom);
  if (params.eventDateTo) query.set("event_date_to", params.eventDateTo);
  if (params.needsReview !== undefined) query.set("needs_review", String(params.needsReview));
  if (params.duplicatesOnly) query.set("duplicates_only", "true");
  query.set("limit", String(params.limit ?? 25));
  query.set("offset", String(params.offset ?? 0));
  return query.toString();
}

/** True when a search would ask the server anything at all. */
export function hasSearchTerms(params: SearchParams): boolean {
  return Boolean(
    params.q?.trim() ||
      (params.field && params.fieldValue?.trim()) ||
      params.batchId ||
      params.name?.trim() ||
      params.fatherName?.trim() ||
      params.eventDateFrom ||
      params.eventDateTo ||
      params.needsReview !== undefined ||
      params.duplicatesOnly ||
      params.certificateTypeId,
  );
}

export function useCertificateSearch(params: SearchParams, options: { enabled?: boolean } = {}) {
  return useQuery<SearchResponse>({
    queryKey: registerKeys.search(params),
    queryFn: () =>
      apiFetch(`/api/v1/certificates/search?${searchQueryString(params)}`, searchResponseSchema),
    enabled: options.enabled ?? hasSearchTerms(params),
    // A search result is a snapshot of a register that is still being written to;
    // holding it briefly keeps paging back and forth from re-querying every time.
    staleTime: 30 * 1000,
  });
}

export function useCertificate(id: string) {
  return useQuery<CertificateDetail>({
    queryKey: registerKeys.certificate(id),
    queryFn: () => apiFetch(`/api/v1/certificates/${id}`, certificateDetailSchema),
    enabled: Boolean(id),
  });
}

/**
 * The field definitions an entry was read under.
 *
 * Needed because a field's machine name is not its name. "father_id_number" is what
 * the column is called; "Father's CNIC" is what it is called on the certificate, and
 * an office that invents its own type names its own fields - so the labels have to
 * come from the schema the entry was filed against, not from a table compiled into
 * this build.
 */
export function useSchemaVersion(versionId: string | null | undefined) {
  return useQuery<SchemaVersionDetail>({
    queryKey: registerKeys.schemaVersion(versionId ?? ""),
    queryFn: () =>
      apiFetch(`/api/v1/schema-versions/${versionId}`, schemaVersionDetailSchema),
    enabled: Boolean(versionId),
    // A published version never changes; only a new one is ever created.
    staleTime: Infinity,
  });
}

export function useDuplicateCandidates(id: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: registerKeys.duplicates(id),
    queryFn: () =>
      apiFetch(`/api/v1/certificates/${id}/duplicates`, duplicateCandidateListSchema),
    enabled: Boolean(id) && (options.enabled ?? true),
  });
}
