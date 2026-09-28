"use client";

import * as React from "react";
import {
  type ColumnDef,
  type RowSelectionState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Check, Loader2, ScanLine } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type GridColumn,
  SERIAL_COLUMN_WIDTH,
  columnOffset,
  keepColumnInView,
  totalColumnWidth,
} from "@/lib/grid-columns";
import { CERTIFICATE_TYPE_LABEL } from "@/lib/schemas/batches";
import { type RowSummary, isApproved } from "@/lib/schemas/rows";
import { valueTextAttributes } from "@/lib/text-direction";
import { cn, confidenceClass, describeConfidence, formatConfidence } from "@/lib/utils";

/**
 * The review grid.
 *
 * Tall rows and 18px type, because this is where staff spend their day. The row
 * height is fixed at 64px rather than fitted to content: the virtualiser needs to
 * know how tall a row is before it renders one, and an Urdu value set in Nastaliq
 * needs the headroom - a name in that face is nearly twice the height of the same
 * name in Latin script.
 *
 * Selection is a cell, not a row, and lives in the parent: the review pane shows
 * the selected field's box on the page image, so the two have to agree on what is
 * selected. Focus stays on the scroll container and the selected cell is named by
 * `aria-activedescendant`; moving focus to each of a thousand cells in turn would
 * make a screen reader announce the grid's chrome on every arrow press.
 */

export const GRID_ROW_HEIGHT = 64;
const SELECT_COLUMN_WIDTH = 56;

/** Checkbox and serial number stay put while the rest of the row scrolls. */
const FROZEN_WIDTH = SELECT_COLUMN_WIDTH + SERIAL_COLUMN_WIDTH;

/*
 * Selection is drawn with an inset outline rather than a ring: an outline survives
 * Windows High Contrast mode, and being inset means it is not clipped by the cell's
 * own `overflow: hidden`.
 */
const SELECTED_CELL = "[outline:3px_solid_var(--ring)] [outline-offset:-3px]";

const CHECKBOX_CLASS =
  "size-6 cursor-pointer accent-[var(--primary)] disabled:cursor-not-allowed";

export interface CellPosition {
  rowIndex: number;
  columnIndex: number;
}

export interface ResultsGridProps {
  columns: GridColumn[];
  rows: RowSummary[];
  selected: CellPosition;
  onSelectedChange: (position: CellPosition) => void;
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
  /** Commit one field of one row. The parent PATCHes and rolls back on failure. */
  onCommit: (row: RowSummary, fieldName: string, value: string) => void;
  onApproveRow: (row: RowSummary) => void;
  rowSelection: RowSelectionState;
  onRowSelectionChange: React.Dispatch<React.SetStateAction<RowSelectionState>>;
  /** False for viewers, whose corrections the server would refuse anyway. */
  canEdit: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
  onShowHelp: () => void;
}

function cellText(row: RowSummary, column: GridColumn): string | null {
  if (column.kind === "serial") return String(row.serial_no);
  if (column.kind === "type") return CERTIFICATE_TYPE_LABEL[row.certificate_type];
  return row.fields[column.id] ?? null;
}

function cellConfidence(row: RowSummary, column: GridColumn): number | null {
  if (column.kind !== "field") return null;
  return row.field_confidences[column.id] ?? null;
}

function cellId(rowId: string, columnId: string): string {
  return `cell-${rowId}-${columnId}`;
}

/** Inline editor for one cell. */
function CellEditor({
  initial,
  label,
  onCommit,
  onCommitAndAdvance,
  onCancel,
}: {
  initial: string;
  label: string;
  onCommit: (value: string) => void;
  onCommitAndAdvance: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = React.useState(initial);
  /*
   * Saving or cancelling both move focus back to the grid, which blurs this input
   * while it is still mounted - the state change that unmounts it has not flushed
   * yet. Without this latch that blur would send the correction a second time, and
   * the server would record two edits for one keystroke.
   */
  const settled = React.useRef(false);

  return (
    <Input
      autoFocus
      aria-label={`${label}. Press Enter to save, Escape to cancel.`}
      value={value}
      // The caret and any Urdu typed into it follow what is already in the cell.
      {...valueTextAttributes(value || initial)}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        // Every key stops here: the grid's own handler would otherwise read the
        // arrow keys as "move cell" while the caret is trying to move in the text.
        event.stopPropagation();
        if (event.key === "Escape") {
          event.preventDefault();
          settled.current = true;
          onCancel();
        } else if (event.key === "Enter") {
          event.preventDefault();
          settled.current = true;
          onCommit(value);
        } else if (event.key === "Tab") {
          event.preventDefault();
          settled.current = true;
          onCommitAndAdvance(value);
        }
      }}
      onBlur={() => {
        // Clicking away keeps the typing. Anyone who wants the old value back
        // presses Escape, which is the documented way out and is what the
        // keyboard help says.
        if (settled.current) return;
        settled.current = true;
        onCommit(value);
      }}
      className="h-11 w-full text-base"
    />
  );
}

export function ResultsGrid({
  columns,
  rows,
  selected,
  onSelectedChange,
  editing,
  onEditingChange,
  onCommit,
  onApproveRow,
  rowSelection,
  onRowSelectionChange,
  canEdit,
  hasMore,
  isLoadingMore,
  onLoadMore,
  onShowHelp,
}: ResultsGridProps) {
  const scrollRef = React.useRef<HTMLDivElement | null>(null);

  const tableColumns = React.useMemo<ColumnDef<RowSummary>[]>(
    () =>
      columns.map((column) => ({
        id: column.id,
        header: column.label,
        size: column.width,
        accessorFn: (row) => cellText(row, column),
      })),
    [columns],
  );

  const table = useReactTable({
    data: rows,
    columns: tableColumns,
    getCoreRowModel: getCoreRowModel(),
    // Row ids are the server's row ids, so a selection survives a refetch that
    // reorders or extends the list.
    getRowId: (row) => row.id,
    state: { rowSelection },
    onRowSelectionChange,
    enableRowSelection: true,
  });

  const tableRows = table.getRowModel().rows;

  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => GRID_ROW_HEIGHT,
    overscan: 8,
  });

  const virtualItems = virtualizer.getVirtualItems();
  const lastVisibleIndex = virtualItems.at(-1)?.index ?? 0;

  // Fetch the next page as the bottom of the loaded rows comes into view, so a
  // reviewer scrolling a large batch never meets a "load more" button.
  React.useEffect(() => {
    if (hasMore && !isLoadingMore && lastVisibleIndex >= tableRows.length - 12) {
      onLoadMore();
    }
  }, [hasMore, isLoadingMore, lastVisibleIndex, tableRows.length, onLoadMore]);

  // Refs rather than plain effects: `scrollToIndex` on every render would fight a
  // reviewer who is scrolling with the mouse, so each axis only moves when the
  // selected cell actually changed.
  const lastRowIndex = React.useRef(selected.rowIndex);
  React.useEffect(() => {
    if (lastRowIndex.current === selected.rowIndex) return;
    lastRowIndex.current = selected.rowIndex;
    virtualizer.scrollToIndex(selected.rowIndex, { align: "auto" });
  }, [selected.rowIndex, virtualizer]);

  const lastColumnIndex = React.useRef(selected.columnIndex);
  React.useEffect(() => {
    if (lastColumnIndex.current === selected.columnIndex) return;
    lastColumnIndex.current = selected.columnIndex;
    const element = scrollRef.current;
    const column = columns[selected.columnIndex];
    if (!element || !column) return;
    element.scrollLeft = keepColumnInView({
      offset: SELECT_COLUMN_WIDTH + columnOffset(columns, selected.columnIndex),
      width: column.width,
      scrollLeft: element.scrollLeft,
      viewportWidth: element.clientWidth,
      frozenWidth: FROZEN_WIDTH,
    });
  }, [selected.columnIndex, columns]);

  const focusGrid = React.useCallback(() => {
    scrollRef.current?.focus();
  }, []);

  const move = React.useCallback(
    (rowStep: number, columnStep: number) => {
      const rowIndex = Math.min(Math.max(selected.rowIndex + rowStep, 0), Math.max(tableRows.length - 1, 0));
      const columnIndex = Math.min(
        Math.max(selected.columnIndex + columnStep, 0),
        Math.max(columns.length - 1, 0),
      );
      onSelectedChange({ rowIndex, columnIndex });
    },
    [columns.length, onSelectedChange, selected.columnIndex, selected.rowIndex, tableRows.length],
  );

  /** Tab moves to the next field, wrapping onto the next row's first field. */
  const advance = React.useCallback(
    (step: number) => {
      const firstField = columns.findIndex((column) => column.kind === "field");
      if (firstField === -1) return;
      let columnIndex = selected.columnIndex + step;
      let rowIndex = selected.rowIndex;
      if (columnIndex > columns.length - 1) {
        columnIndex = firstField;
        rowIndex = Math.min(rowIndex + 1, tableRows.length - 1);
      } else if (columnIndex < firstField) {
        columnIndex = columns.length - 1;
        rowIndex = Math.max(rowIndex - 1, 0);
      }
      onSelectedChange({ rowIndex, columnIndex });
    },
    [columns, onSelectedChange, selected.columnIndex, selected.rowIndex, tableRows.length],
  );

  const selectedRow = tableRows[selected.rowIndex]?.original;
  const selectedColumn = columns[selected.columnIndex];
  const isEditableCell = canEdit && selectedColumn?.kind === "field";

  const handleKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      // Only when the container itself has focus: a checkbox or the cell editor
      // inside a row gets to keep its own keys.
      if (event.target !== event.currentTarget) return;
      if (tableRows.length === 0) return;

      switch (event.key) {
        case "ArrowDown":
        case "j":
          event.preventDefault();
          move(1, 0);
          return;
        case "ArrowUp":
        case "k":
          event.preventDefault();
          move(-1, 0);
          return;
        case "ArrowRight":
          event.preventDefault();
          move(0, 1);
          return;
        case "ArrowLeft":
          event.preventDefault();
          move(0, -1);
          return;
        case "PageDown":
          event.preventDefault();
          move(10, 0);
          return;
        case "PageUp":
          event.preventDefault();
          move(-10, 0);
          return;
        case "Home":
          event.preventDefault();
          onSelectedChange({ rowIndex: selected.rowIndex, columnIndex: 0 });
          return;
        case "End":
          event.preventDefault();
          onSelectedChange({ rowIndex: selected.rowIndex, columnIndex: columns.length - 1 });
          return;
        case "Tab":
          event.preventDefault();
          advance(event.shiftKey ? -1 : 1);
          return;
        case "Enter":
          if (isEditableCell) {
            event.preventDefault();
            onEditingChange(true);
          }
          return;
        case "a":
          if (selectedRow) {
            event.preventDefault();
            onApproveRow(selectedRow);
          }
          return;
        case " ":
          if (selectedRow) {
            event.preventDefault();
            onRowSelectionChange((current) => {
              const next = { ...current };
              if (next[selectedRow.id]) delete next[selectedRow.id];
              else next[selectedRow.id] = true;
              return next;
            });
          }
          return;
        case "?":
          event.preventDefault();
          onShowHelp();
          return;
        default:
          return;
      }
    },
    [
      advance,
      columns.length,
      isEditableCell,
      move,
      onApproveRow,
      onEditingChange,
      onRowSelectionChange,
      onSelectedChange,
      onShowHelp,
      selected.rowIndex,
      selectedRow,
      tableRows.length,
    ],
  );

  const commit = React.useCallback(
    (row: RowSummary, column: GridColumn, value: string) => {
      onCommit(row, column.id, value);
      onEditingChange(false);
      focusGrid();
    },
    [focusGrid, onCommit, onEditingChange],
  );

  const allLoadedSelected =
    tableRows.length > 0 && tableRows.every((row) => rowSelection[row.id] === true);
  const someLoadedSelected = tableRows.some((row) => rowSelection[row.id] === true);

  const headerGroups = table.getHeaderGroups();
  const gridWidth = SELECT_COLUMN_WIDTH + totalColumnWidth(columns);

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-soft">
      <div
        ref={scrollRef}
        role="grid"
        tabIndex={0}
        aria-label="Extracted certificates. Use the arrow keys to move, Enter to edit."
        aria-rowcount={tableRows.length}
        aria-colcount={columns.length + 1}
        aria-activedescendant={
          selectedRow && selectedColumn ? cellId(selectedRow.id, selectedColumn.id) : undefined
        }
        onKeyDown={handleKeyDown}
        className="max-h-[68vh] overflow-auto focus-visible:outline-offset-[-3px]"
      >
        <div style={{ width: gridWidth }} className="relative">
          {headerGroups.map((headerGroup) => (
            <div
              key={headerGroup.id}
              role="row"
              className="sticky top-0 z-20 flex border-b-2 border-border bg-muted"
            >
              <div
                role="columnheader"
                aria-colindex={1}
                aria-label="Choose rows"
                className="sticky left-0 z-10 flex shrink-0 items-center justify-center border-r border-border-subtle bg-muted"
                style={{ width: SELECT_COLUMN_WIDTH }}
              >
                <input
                  type="checkbox"
                  className={CHECKBOX_CLASS}
                  checked={allLoadedSelected}
                  ref={(element) => {
                    if (element) element.indeterminate = !allLoadedSelected && someLoadedSelected;
                  }}
                  onChange={() => table.toggleAllRowsSelected(!allLoadedSelected)}
                  aria-label={
                    allLoadedSelected ? "Clear every chosen row" : "Choose every row shown"
                  }
                />
              </div>
              {headerGroup.headers.map((header, index) => {
                const column = columns[index];
                if (!column) return null;
                return (
                  <div
                    key={header.id}
                    role="columnheader"
                    aria-colindex={index + 2}
                    title={column.spec?.required ? `${column.label} (required)` : column.label}
                    style={{ width: column.width }}
                    className={cn(
                      "flex shrink-0 items-center gap-1.5 border-r border-border-subtle px-3 py-3.5",
                      "text-sm font-bold text-foreground",
                      column.numeric && "justify-end",
                      column.kind === "serial" && "sticky left-14 z-10 bg-muted",
                    )}
                  >
                    <span className="truncate">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </span>
                    {column.spec?.required ? (
                      <span aria-hidden="true" className="text-destructive">
                        *
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}

          <div style={{ height: virtualizer.getTotalSize() }} className="relative">
            {virtualItems.map((virtualRow) => {
              const tableRow = tableRows[virtualRow.index];
              if (!tableRow) return null;
              const row = tableRow.original;
              const isSelectedRow = virtualRow.index === selected.rowIndex;
              const chosen = rowSelection[row.id] === true;

              return (
                <div
                  key={row.id}
                  role="row"
                  aria-rowindex={virtualRow.index + 1}
                  aria-selected={chosen}
                  className={cn(
                    "absolute left-0 top-0 flex border-b border-border-subtle",
                    isSelectedRow ? "bg-accent" : "bg-card",
                  )}
                  style={{
                    height: virtualRow.size,
                    transform: `translateY(${virtualRow.start}px)`,
                    width: gridWidth,
                  }}
                >
                  <div
                    role="gridcell"
                    aria-colindex={1}
                    className={cn(
                      "sticky left-0 z-10 flex shrink-0 flex-col items-center justify-center gap-0.5 border-r border-border-subtle",
                      isSelectedRow ? "bg-accent" : "bg-card",
                    )}
                    style={{ width: SELECT_COLUMN_WIDTH }}
                  >
                    <input
                      type="checkbox"
                      className={CHECKBOX_CLASS}
                      checked={chosen}
                      onChange={() => tableRow.toggleSelected()}
                      aria-label={`Choose certificate number ${row.serial_no}`}
                    />
                    {isApproved(row.review_status) ? (
                      <Check
                        aria-label="Already approved"
                        className="size-4 text-success-surface-foreground"
                      />
                    ) : null}
                  </div>

                  {columns.map((column, columnIndex) => {
                    const value = cellText(row, column);
                    const confidence = cellConfidence(row, column);
                    const isSelectedCell = isSelectedRow && columnIndex === selected.columnIndex;
                    const editable = canEdit && column.kind === "field";

                    return (
                      <div
                        key={column.id}
                        role="gridcell"
                        id={cellId(row.id, column.id)}
                        aria-colindex={columnIndex + 2}
                        aria-readonly={!editable}
                        title={
                          column.kind === "field"
                            ? `${column.label}: ${value ?? "empty"} (${describeConfidence(confidence)})`
                            : `${column.label}: ${value ?? "empty"}`
                        }
                        aria-label={
                          column.kind === "field"
                            ? `${column.label}: ${value ?? "empty"}, ${describeConfidence(confidence)}`
                            : `${column.label}: ${value ?? "empty"}`
                        }
                        onClick={() => {
                          onSelectedChange({ rowIndex: virtualRow.index, columnIndex });
                          if (editable) onEditingChange(true);
                        }}
                        className={cn(
                          "flex shrink-0 items-center gap-2 overflow-hidden border-r border-border-subtle px-3",
                          column.kind === "field" && confidenceClass(confidence),
                          column.numeric && "justify-end",
                          column.kind === "serial" &&
                            cn("sticky left-14 z-10", isSelectedRow ? "bg-accent" : "bg-card"),
                          editable && "cursor-text",
                          isSelectedCell && SELECTED_CELL,
                        )}
                      >
                        {isSelectedCell && editing && editable ? (
                          <CellEditor
                            initial={value ?? ""}
                            label={column.label}
                            onCommit={(next) => commit(row, column, next)}
                            onCommitAndAdvance={(next) => {
                              commit(row, column, next);
                              advance(1);
                            }}
                            onCancel={() => {
                              onEditingChange(false);
                              focusGrid();
                            }}
                          />
                        ) : (
                          <>
                            <span
                              {...valueTextAttributes(value)}
                              className={cn(
                                "min-w-0 flex-1 truncate [unicode-bidi:isolate]",
                                column.numeric && "text-right tabular-nums",
                                column.kind === "serial" && "font-semibold tabular-nums",
                                value === null && "text-muted-foreground",
                              )}
                            >
                              {value ?? "—"}
                            </span>
                            {column.kind === "field" && confidence !== null ? (
                              // The number beside the shading: colour alone is no
                              // signal at all for a colour-blind reviewer.
                              <span
                                aria-hidden="true"
                                className="shrink-0 text-xs font-semibold tabular-nums opacity-80"
                              >
                                {formatConfidence(confidence)}
                              </span>
                            ) : null}
                            {column.kind === "type" && row.ocr_used ? (
                              <ScanLine
                                aria-label="Read from a scan"
                                className="size-4 shrink-0 text-muted-foreground"
                              />
                            ) : null}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {hasMore ? (
        <div className="flex items-center justify-center gap-3 border-t border-border-subtle bg-muted/60 px-5 py-3">
          {isLoadingMore ? (
            <span className="flex items-center gap-2 text-base text-muted-foreground">
              <Loader2 aria-hidden="true" className="size-5 animate-spin" />
              Loading more certificates
            </span>
          ) : (
            <Button variant="outline" size="sm" onClick={onLoadMore}>
              Load more certificates
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}
