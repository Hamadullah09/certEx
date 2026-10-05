/**
 * The results grid's columns, built from the generated field specs.
 *
 * The field list is never written out here: it comes from
 * `src/lib/schemas/fields.generated.ts`, which is generated from the Python
 * specs, so adding a field to a certificate type in the pipeline adds a column to
 * the grid and nothing has to be kept in step by hand.
 */

import {
  type CertificateType,
  type FieldSpec,
  COMMON_FIELDS,
  fieldsFor,
} from "@/lib/schemas/fields.generated";
import type { RowSummary } from "@/lib/schemas/rows";

export type GridColumnKind = "serial" | "type" | "field";

export interface GridColumn {
  /** Column id, and for a field column the field's machine name. */
  id: string;
  label: string;
  kind: GridColumnKind;
  /** Present on field columns only. */
  spec?: FieldSpec;
  /** Fixed pixel width: the virtualised body and the sticky header share it. */
  width: number;
  numeric?: boolean;
}

export const SERIAL_COLUMN_ID = "serial_no";
export const TYPE_COLUMN_ID = "certificate_type";

/**
 * Column widths by field kind.
 *
 * A name or an address in Urdu is both longer and taller than its English
 * equivalent, so those columns are the widest; dates and identifiers are fixed
 * formats and do not need the room.
 */
const WIDTH_BY_KIND: Record<FieldSpec["kind"], number> = {
  text: 230,
  name: 260,
  date: 160,
  time: 140,
  id_number: 200,
  reference: 200,
  sex: 120,
  number: 140,
  address: 320,
};

export const SERIAL_COLUMN_WIDTH = 96;
export const TYPE_COLUMN_WIDTH = 150;

/**
 * Certificate types present in these rows, in the order the field specs declare
 * them, so a mixed-type grid does not reorder its columns as rows load.
 */
export function distinctCertificateTypes(
  rows: readonly Pick<RowSummary, "certificate_type">[],
): CertificateType[] {
  const present = new Set(rows.map((row) => row.certificate_type));
  const order: CertificateType[] = ["BIRTH", "MARRIAGE", "DEATH", "OTHER"];
  return order.filter((type) => present.has(type));
}

/**
 * Field specs for one or more certificate types, de-duplicated.
 *
 * With no type filter the grid shows rows of several types at once, so the
 * columns are the union of their fields in export order - common fields first,
 * because every type declares those first and they therefore win the ordering.
 * With no types at all (an empty batch, or one still processing) the common
 * fields stand in: every certificate has them whatever it turns out to be.
 */
export function fieldSpecsFor(types: readonly CertificateType[]): FieldSpec[] {
  if (types.length === 0) return [...COMMON_FIELDS];
  const seen = new Set<string>();
  const specs: FieldSpec[] = [];
  for (const type of types) {
    for (const spec of fieldsFor(type)) {
      if (seen.has(spec.name)) continue;
      seen.add(spec.name);
      specs.push(spec);
    }
  }
  return specs;
}

/**
 * The grid's columns.
 *
 * `serial_no` is first and `certificate_type` second, always: the serial number
 * is what a clerk cross-references against the paper file, and the type decides
 * what the rest of the row even means. Both are also the first two columns of the
 * CSV export, so the screen and the download read the same way.
 */
export function buildGridColumns(types: readonly CertificateType[]): GridColumn[] {
  const columns: GridColumn[] = [
    { id: SERIAL_COLUMN_ID, label: "No.", kind: "serial", width: SERIAL_COLUMN_WIDTH, numeric: true },
    { id: TYPE_COLUMN_ID, label: "Type", kind: "type", width: TYPE_COLUMN_WIDTH },
  ];
  for (const spec of fieldSpecsFor(types)) {
    columns.push({
      id: spec.name,
      label: spec.label,
      kind: "field",
      spec,
      width: WIDTH_BY_KIND[spec.kind],
      numeric: spec.kind === "number",
    });
  }
  return columns;
}

/**
 * What to call one field on screen.
 *
 * Needed because a row carries two different things that both look like a label. The
 * field's *name* is what the office calls it everywhere - in the grid heading, in the
 * export column, on the register entry. The row's `label` is the words printed beside
 * that value on *this particular scan*, which is provenance: useful for explaining
 * where a value came from, useless as a heading, because the same field then appears
 * as "Date of registration" on one certificate and "Reg. date" on the next.
 *
 * So: the office's name for the field, and only the printed words if this build has
 * never heard of the field at all.
 */
export function fieldLabel(
  name: string,
  types: readonly CertificateType[],
  printed?: string | null,
): string {
  const spec = fieldSpecsFor(types).find((candidate) => candidate.name === name);
  if (spec) return spec.label;
  if (printed) return printed;
  const spaced = name.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Total width of the grid, so the scroller knows how wide its content is. */
export function totalColumnWidth(columns: readonly GridColumn[]): number {
  return columns.reduce((sum, column) => sum + column.width, 0);
}

/** Distance from the grid's left edge to the start of column `index`. */
export function columnOffset(columns: readonly GridColumn[], index: number): number {
  let offset = 0;
  for (let position = 0; position < index && position < columns.length; position += 1) {
    offset += columns[position]?.width ?? 0;
  }
  return offset;
}

export interface ColumnViewport {
  /** Left edge of the column being moved to, in grid coordinates. */
  offset: number;
  width: number;
  scrollLeft: number;
  viewportWidth: number;
  /** Width of the frozen left-hand strip, which a column must not hide behind. */
  frozenWidth?: number;
}

/**
 * The scroll position that brings a column into view, or the current one if it is
 * already there.
 *
 * Arrowing across the grid has to move the scroller, and the first columns are
 * frozen, so "visible" means clear of that frozen strip - not merely inside the
 * scroll port, where a column would sit underneath the serial number.
 */
export function keepColumnInView({
  offset,
  width,
  scrollLeft,
  viewportWidth,
  frozenWidth = 0,
}: ColumnViewport): number {
  if (offset < scrollLeft + frozenWidth) return Math.max(0, offset - frozenWidth);
  if (offset + width > scrollLeft + viewportWidth) {
    return Math.max(0, offset + width - viewportWidth);
  }
  return scrollLeft;
}
