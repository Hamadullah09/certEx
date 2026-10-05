"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, KeyRound, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import type { FieldKind } from "@/lib/schemas/registry";
import { cn } from "@/lib/utils";

/**
 * Defining the columns a batch will be read for.
 *
 * This is the one screen in the app where a decision is permanent: the columns chosen
 * here are pinned to the batch and every document uploaded into it - the first and the
 * millionth - is read for exactly these. So it is built to be edited comfortably before
 * saving and to say plainly what each choice means.
 *
 * Two things are deliberately not hidden from the person using it:
 *
 * **The machine key.** The label is what they type; the key is what becomes the CSV
 * header and the database field. It is derived from the label and shown, because a
 * clerk who later opens the CSV and finds a column called `fathers_name` needs to
 * recognise it. It stays editable, and stops following the label once edited by hand -
 * renaming a column heading should not silently rename the data behind it.
 *
 * **Which column is the certificate number.** The register is keyed on it: an entry
 * without one cannot be filed. The column can be called anything and sit anywhere, but
 * exactly one has to be marked, so it is a visible choice rather than a validation
 * error discovered after a thousand uploads.
 */

export interface ColumnDraft {
  /** Stable across reorders, so React keeps the right input focused. */
  id: string;
  name: string;
  label: string;
  kind: FieldKind;
  isIdentifier: boolean;
  required: boolean;
  /** False once somebody edits the key by hand; the key then stops following. */
  keyFollowsLabel: boolean;
  /**
   * Other wordings this column is printed under, which the reader also matches.
   *
   * It carries real weight. A column labelled "Certificate number" against a form that
   * prints "Certificate No." matches nothing on the label alone, and the value ends up
   * filed as an unrecognised extra - which for the certificate number means the record
   * cannot be filed in the register at all.
   */
  otherWordings: string[];
}

/** What each field kind is for, in words a clerk would use. */
export const FIELD_KINDS: readonly { value: FieldKind; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "name", label: "A person's name" },
  { value: "date", label: "Date" },
  { value: "time", label: "Time" },
  { value: "id_number", label: "CNIC or ID number" },
  { value: "reference", label: "Certificate or file number" },
  { value: "sex", label: "Male / female" },
  { value: "number", label: "Number" },
  { value: "address", label: "Address" },
];

/** Spreadsheet-style column letters: A, B … Z, AA, AB. */
export function columnLetter(index: number): string {
  let letters = "";
  let remaining = index;
  while (remaining >= 0) {
    letters = String.fromCharCode(65 + (remaining % 26)) + letters;
    remaining = Math.floor(remaining / 26) - 1;
  }
  return letters;
}

/**
 * A label turned into a machine key.
 *
 * Must satisfy what the server accepts - lowercase, digits and underscores, starting
 * with a letter - or the batch is refused on save with a message about a field the
 * person never typed.
 */
export function keyFromLabel(label: string): string {
  const cleaned = label
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  if (!cleaned) return "";
  return /^[a-z]/.test(cleaned) ? cleaned : `f_${cleaned}`.slice(0, 64);
}

let nextId = 0;
export function newColumn(partial: Partial<ColumnDraft> = {}): ColumnDraft {
  nextId += 1;
  return {
    id: `column-${nextId}`,
    name: "",
    label: "",
    kind: "text",
    isIdentifier: false,
    required: false,
    keyFollowsLabel: true,
    otherWordings: [],
    ...partial,
  };
}

/** Everything wrong with these columns, in the order a person would fix it. */
export function problemsWith(columns: readonly ColumnDraft[]): string[] {
  const problems: string[] = [];
  if (columns.length === 0) {
    problems.push("Add at least one column.");
    return problems;
  }
  if (columns.some((column) => !column.label.trim())) {
    problems.push("Every column needs a name.");
  }
  if (columns.some((column) => !column.name.trim())) {
    problems.push("Every column needs a key for the CSV. Type one, or change the name.");
  }

  const keys = columns.map((column) => column.name.trim()).filter(Boolean);
  const duplicate = keys.find((key, index) => keys.indexOf(key) !== index);
  if (duplicate) problems.push(`Two columns share the key "${duplicate}". Keys must differ.`);

  const malformed = columns.find(
    (column) => column.name.trim() && !/^[a-z][a-z0-9_]{0,62}[a-z0-9]$/.test(column.name.trim()),
  );
  if (malformed) {
    problems.push(
      `The key "${malformed.name}" cannot be used. Use lowercase letters, digits and underscores, starting with a letter.`,
    );
  }

  const identifiers = columns.filter((column) => column.isIdentifier);
  if (identifiers.length === 0) {
    problems.push("Mark which column holds the certificate number.");
  } else if (identifiers.length > 1) {
    problems.push("Only one column can be the certificate number.");
  }
  return problems;
}

export function ColumnBuilder({
  columns,
  onChange,
  disabled = false,
}: {
  columns: ColumnDraft[];
  onChange: (columns: ColumnDraft[]) => void;
  disabled?: boolean;
}) {
  function update(id: string, change: Partial<ColumnDraft>) {
    onChange(columns.map((column) => (column.id === id ? { ...column, ...change } : column)));
  }

  function move(index: number, by: number) {
    const target = index + by;
    if (target < 0 || target >= columns.length) return;
    const reordered = [...columns];
    const [moved] = reordered.splice(index, 1);
    if (moved) reordered.splice(target, 0, moved);
    onChange(reordered);
  }

  return (
    <div className="space-y-4">
      <ul className="space-y-3">
        {columns.map((column, index) => (
          <li
            key={column.id}
            className={cn(
              "rounded-lg border-2 p-4",
              column.isIdentifier ? "border-primary bg-primary-surface/40" : "border-border",
            )}
          >
            <div className="flex flex-wrap items-start gap-4">
              {/* The column letter, so this reads like the spreadsheet it becomes. */}
              <span
                aria-hidden="true"
                className="mt-7 flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted text-lg font-bold tabular-nums"
              >
                {columnLetter(index)}
              </span>

              <div className="min-w-[14rem] flex-1">
                <Label htmlFor={`${column.id}-label`}>Column {index + 1} name</Label>
                <Input
                  id={`${column.id}-label`}
                  className="mt-1.5"
                  placeholder="Father Name"
                  value={column.label}
                  disabled={disabled}
                  onChange={(event) => {
                    const label = event.target.value;
                    update(column.id, {
                      label,
                      ...(column.keyFollowsLabel ? { name: keyFromLabel(label) } : {}),
                    });
                  }}
                />
                <p className="mt-1 text-sm text-muted-foreground">
                  Also what the app looks for printed on the certificate.
                </p>
              </div>

              <div className="min-w-[12rem] flex-1">
                <Label htmlFor={`${column.id}-key`}>Heading in the CSV</Label>
                <Input
                  id={`${column.id}-key`}
                  className="mt-1.5 font-mono"
                  placeholder="father_name"
                  value={column.name}
                  disabled={disabled}
                  onChange={(event) =>
                    update(column.id, { name: event.target.value, keyFollowsLabel: false })
                  }
                />
              </div>

              <div className="min-w-[11rem]">
                <Label htmlFor={`${column.id}-kind`}>What kind of value</Label>
                <Select
                  id={`${column.id}-kind`}
                  className="mt-1.5"
                  value={column.kind}
                  disabled={disabled}
                  onChange={(event) =>
                    update(column.id, { kind: event.target.value as FieldKind })
                  }
                >
                  {FIELD_KINDS.map((kind) => (
                    <option key={kind.value} value={kind.value}>
                      {kind.label}
                    </option>
                  ))}
                </Select>
              </div>

              <div className="flex gap-1 pt-7">
                <Button
                  variant="outline"
                  size="icon"
                  aria-label={`Move ${column.label || `column ${index + 1}`} up`}
                  disabled={disabled || index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp aria-hidden="true" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  aria-label={`Move ${column.label || `column ${index + 1}`} down`}
                  disabled={disabled || index === columns.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown aria-hidden="true" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  aria-label={`Remove ${column.label || `column ${index + 1}`}`}
                  disabled={disabled}
                  onClick={() => onChange(columns.filter((item) => item.id !== column.id))}
                >
                  <X aria-hidden="true" />
                </Button>
              </div>
            </div>

            <div className="mt-3 border-t border-border-subtle pt-3">
              <Label htmlFor={`${column.id}-wordings`}>
                Other wordings on the form <span className="font-normal">(optional)</span>
              </Label>
              <Input
                id={`${column.id}-wordings`}
                className="mt-1.5"
                placeholder="Certificate No., Cert No, Serial Number"
                value={column.otherWordings.join(", ")}
                disabled={disabled}
                onChange={(event) =>
                  update(column.id, {
                    otherWordings: event.target.value
                      .split(",")
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })
                }
              />
              <p className="mt-1 text-sm text-muted-foreground">
                Separate with commas. Add these when different forms print this value under
                different headings.
              </p>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border-subtle pt-3">
              <label className="flex min-h-11 items-center gap-2.5 text-base font-medium">
                <input
                  type="radio"
                  name="identifier-column"
                  className="size-5"
                  checked={column.isIdentifier}
                  disabled={disabled}
                  onChange={() =>
                    onChange(
                      columns.map((item) => ({
                        ...item,
                        isIdentifier: item.id === column.id,
                      })),
                    )
                  }
                />
                <KeyRound aria-hidden="true" className="size-5 text-primary" />
                This is the certificate number
              </label>

              <label className="flex min-h-11 items-center gap-2.5 text-base font-medium">
                <input
                  type="checkbox"
                  className="size-5"
                  checked={column.required}
                  disabled={disabled}
                  onChange={(event) => update(column.id, { required: event.target.checked })}
                />
                A certificate missing this is incomplete
              </label>
            </div>
          </li>
        ))}
      </ul>

      <Button
        variant="outline"
        size="lg"
        disabled={disabled}
        onClick={() => onChange([...columns, newColumn()])}
      >
        <Plus aria-hidden="true" />
        Add a column
      </Button>
    </div>
  );
}
