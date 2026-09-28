"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { RowSelectionState } from "@tanstack/react-table";
import {
  ArrowLeft,
  Check,
  Combine,
  Keyboard,
  Search,
  Table2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { KeyboardHelp } from "@/components/review/keyboard-help";
import { type CellPosition, ResultsGrid } from "@/components/review/results-grid";
import { RowDetailPane } from "@/components/review/row-detail-pane";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatch } from "@/hooks/use-batches";
import { useApproveRows, useBatchRows, useCorrectRow, useReprocessRows } from "@/hooks/use-rows";
import { useMergeUnits } from "@/hooks/use-units";
import { useSession } from "@/hooks/use-session";
import { ApiError } from "@/lib/api";
import { buildGridColumns, distinctCertificateTypes } from "@/lib/grid-columns";
import { flattenRowPages, totalFromPages } from "@/lib/rows-optimistic";
import { type RowFilters, hasAnyRowFilter, normalizeRowFilters } from "@/lib/rows-query";
import { roleSatisfies } from "@/lib/schemas/auth";
import { CERTIFICATE_TYPE_LABEL, type CertificateType } from "@/lib/schemas/batches";
import {
  type ReviewStatus,
  REVIEW_STATUS_LABEL,
  type RowSummary,
  flagLabel,
} from "@/lib/schemas/rows";
import { checkMerge } from "@/lib/units";

const REVIEW_STATUSES: readonly ReviewStatus[] = [
  "NEEDS_REVIEW",
  "FAILED",
  "AUTO_APPROVED",
  "MANUALLY_APPROVED",
];

const CERTIFICATE_TYPES: readonly CertificateType[] = ["BIRTH", "MARRIAGE", "DEATH", "OTHER"];

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.userMessage;
  return error instanceof Error ? error.message : fallback;
}

export default function BatchResultsPage() {
  const params = useParams<{ id: string }>();
  const batchId = params.id;

  const [filters, setFilters] = React.useState<RowFilters>({});
  const [searchBox, setSearchBox] = React.useState("");
  const [selected, setSelected] = React.useState<CellPosition>({ rowIndex: 0, columnIndex: 0 });
  const [editing, setEditing] = React.useState(false);
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});
  const [showHelp, setShowHelp] = React.useState(false);

  // Debounced so typing does not fire a query per keystroke against a batch that
  // may hold thousands of rows.
  React.useEffect(() => {
    const timer = setTimeout(() => {
      setFilters((current) => {
        const next = searchBox.trim() || undefined;
        return (current.search ?? undefined) === next ? current : { ...current, search: next };
      });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchBox]);

  const batch = useBatch(batchId);
  const query = useBatchRows(batchId, filters);
  const rows = React.useMemo(() => flattenRowPages(query.data), [query.data]);
  const total = totalFromPages(query.data);

  const session = useSession();
  const canEdit = Boolean(
    session.data && roleSatisfies(session.data.user.role, "OPERATOR"),
  );

  const correct = useCorrectRow(batchId, filters);
  const approveRows = useApproveRows(batchId, filters);
  const reprocess = useReprocessRows();
  const merge = useMergeUnits();

  /*
   * Columns follow the rows on screen. With a type filter set it is that type's
   * fields; without one the grid holds several types at once, so it shows the union
   * of their fields rather than picking one type's and hiding the rest.
   */
  const columns = React.useMemo(
    () => buildGridColumns(filters.type ? [filters.type] : distinctCertificateTypes(rows)),
    [filters.type, rows],
  );

  /*
   * Flags offered in the filter come from the rows that are loaded, not from a copy
   * of the server's flag list: a hardcoded copy drifts the moment the pipeline adds
   * a check. The cost is that a flag only present further down a long batch is not
   * offered until those rows load.
   */
  const flagOptions = React.useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) for (const flag of row.flags) seen.add(flag);
    return [...seen].sort();
  }, [rows]);

  const safeSelected: CellPosition = {
    rowIndex: Math.min(selected.rowIndex, Math.max(rows.length - 1, 0)),
    columnIndex: Math.min(selected.columnIndex, Math.max(columns.length - 1, 0)),
  };
  const selectedRow = rows[safeSelected.rowIndex] ?? null;
  const selectedColumn = columns[safeSelected.columnIndex];
  const selectedFieldName = selectedColumn?.kind === "field" ? selectedColumn.id : null;

  const changeFilter = React.useCallback((change: Partial<RowFilters>) => {
    setFilters((current) => ({ ...current, ...change }));
  }, []);

  /*
   * Any change of filter is a new result set, so the selected cell goes back to the
   * top rather than pointing into rows that are no longer there. Keyed on the
   * normalised filters so clearing a box that was already empty does not count.
   */
  const filterKey = JSON.stringify(normalizeRowFilters(filters));
  React.useEffect(() => {
    setSelected({ rowIndex: 0, columnIndex: 0 });
    setEditing(false);
  }, [filterKey]);

  const handleCommit = React.useCallback(
    (row: RowSummary, fieldName: string, value: string) => {
      const next = value.trim();
      const current = row.fields[fieldName] ?? "";
      // Nothing changed: no request, and no audit entry claiming an edit happened.
      if (next === current.trim()) return;
      correct.mutate(
        { rowId: row.id, fields: { [fieldName]: next || null } },
        {
          onError: (error) =>
            toast.error(errorMessage(error, "The correction could not be saved.")),
        },
      );
    },
    [correct],
  );

  const handleApproveRow = React.useCallback(
    (row: RowSummary) => {
      if (!canEdit) {
        toast.error("Your account can look at rows but not change them.");
        return;
      }
      correct.mutate(
        { rowId: row.id, fields: {}, approve: true },
        {
          onSuccess: () => toast.success(`Certificate ${row.serial_no} approved.`),
          onError: (error) => toast.error(errorMessage(error, "The row could not be approved.")),
        },
      );
    },
    [canEdit, correct],
  );

  const handleReprocessRow = React.useCallback(
    (row: RowSummary) => {
      reprocess.mutate([row.id], {
        onSuccess: () => toast.success("This certificate is being read again."),
        onError: (error) =>
          toast.error(errorMessage(error, "The certificate could not be queued.")),
      });
    },
    [reprocess],
  );

  const selectField = React.useCallback(
    (fieldName: string) => {
      const index = columns.findIndex((column) => column.id === fieldName);
      if (index === -1) return;
      setSelected((current) => ({ rowIndex: current.rowIndex, columnIndex: index }));
    },
    [columns],
  );

  const chosenIds = React.useMemo(
    () => Object.keys(rowSelection).filter((id) => rowSelection[id]),
    [rowSelection],
  );
  const chosenRows = React.useMemo(
    () => rows.filter((row) => rowSelection[row.id]),
    [rows, rowSelection],
  );
  const mergeCheck = checkMerge(chosenRows);

  const approveChosen = () => {
    approveRows.mutate(chosenIds, {
      onSuccess: (result) => {
        setRowSelection({});
        if (result.failed.length === 0) {
          toast.success(
            `${result.approved.length} ${result.approved.length === 1 ? "certificate" : "certificates"} approved.`,
          );
          return;
        }
        toast.warning(
          `${result.approved.length} approved, ${result.failed.length} could not be: ${result.failed[0]?.message ?? ""}`,
        );
      },
      onError: (error) => toast.error(errorMessage(error, "Nothing could be approved.")),
    });
  };

  const mergeChosen = () => {
    if (!mergeCheck.ok) return;
    merge.mutate(mergeCheck.unitIds, {
      onSuccess: () => {
        setRowSelection({});
        toast.success("Joined into one certificate. It is being read again.");
      },
      onError: (error) => toast.error(errorMessage(error, "They could not be joined.")),
    });
  };

  const isBusy = correct.isPending || approveRows.isPending || reprocess.isPending;

  return (
    <AppShell wide>
      <PageHeader
        icon={Table2}
        title={batch.data ? batch.data.name : "Results"}
        description="Check what was read from each certificate, fix anything wrong, and approve it."
        actions={
          <>
            <Button variant="outline" onClick={() => setShowHelp((open) => !open)}>
              <Keyboard aria-hidden="true" />
              Keyboard help
            </Button>
            <Button variant="outline" asChild>
              <Link href={`/batches/${batchId}`}>
                <ArrowLeft aria-hidden="true" />
                Back to files
              </Link>
            </Button>
          </>
        }
      />

      {showHelp ? (
        <div className="mt-6">
          <KeyboardHelp onClose={() => setShowHelp(false)} />
        </div>
      ) : null}

      <div className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div>
          <Label htmlFor="filter-status">Checked or not</Label>
          <Select
            id="filter-status"
            className="mt-1.5"
            value={filters.status ?? ""}
            onChange={(event) =>
              changeFilter({ status: (event.target.value || undefined) as ReviewStatus | undefined })
            }
          >
            <option value="">Every row</option>
            {REVIEW_STATUSES.map((status) => (
              <option key={status} value={status}>
                {REVIEW_STATUS_LABEL[status]}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="filter-type">Certificate type</Label>
          <Select
            id="filter-type"
            className="mt-1.5"
            value={filters.type ?? ""}
            onChange={(event) =>
              changeFilter({
                type: (event.target.value || undefined) as CertificateType | undefined,
              })
            }
          >
            <option value="">Every type</option>
            {CERTIFICATE_TYPES.map((type) => (
              <option key={type} value={type}>
                {CERTIFICATE_TYPE_LABEL[type]}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="filter-flag">Check that failed</Label>
          <Select
            id="filter-flag"
            className="mt-1.5"
            value={filters.flag ?? ""}
            disabled={flagOptions.length === 0}
            onChange={(event) => changeFilter({ flag: event.target.value || undefined })}
          >
            <option value="">Any</option>
            {flagOptions.map((flag) => (
              <option key={flag} value={flag}>
                {flagLabel(flag)}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="filter-search">Find a value</Label>
          <div className="relative mt-1.5">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              id="filter-search"
              type="search"
              value={searchBox}
              onChange={(event) => setSearchBox(event.target.value)}
              placeholder="Name, number, file"
              className="pl-12"
            />
          </div>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-4">
        <p className="text-base text-muted-foreground" aria-live="polite">
          {query.isPending ? (
            "Counting certificates"
          ) : (
            <>
              <span className="font-bold text-foreground tabular-nums">
                {(total ?? rows.length).toLocaleString()}
              </span>{" "}
              {(total ?? rows.length) === 1 ? "certificate" : "certificates"}
              {total !== null && rows.length < total
                ? ` · ${rows.length.toLocaleString()} loaded so far`
                : ""}
            </>
          )}
        </p>

        {chosenIds.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border-2 border-primary-border bg-primary-surface px-4 py-2.5">
            <span className="text-base font-semibold text-primary-surface-foreground tabular-nums">
              {chosenIds.length} chosen
            </span>
            <Button
              size="sm"
              disabled={!canEdit || approveRows.isPending}
              onClick={approveChosen}
            >
              <Check aria-hidden="true" />
              Approve chosen
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!canEdit || !mergeCheck.ok || merge.isPending}
              title={mergeCheck.ok ? "Join these into one certificate" : mergeCheck.reason}
              onClick={mergeChosen}
            >
              <Combine aria-hidden="true" />
              Join into one
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRowSelection({})}>
              <X aria-hidden="true" />
              Clear
            </Button>
          </div>
        ) : null}
      </div>

      {query.isError ? (
        <Alert variant="destructive" className="mt-6">
          <AlertTitle>The rows could not be loaded</AlertTitle>
          <AlertDescription>
            {errorMessage(query.error, "Try again in a moment.")}
          </AlertDescription>
        </Alert>
      ) : null}

      {!canEdit && session.data ? (
        <Alert className="mt-6">
          <AlertTitle>You can read this batch but not change it</AlertTitle>
          <AlertDescription>
            Ask an administrator for an operator account to correct or approve certificates.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_28rem]">
        <div className="min-w-0">
          {query.isPending ? (
            <div className="space-y-2">
              {[0, 1, 2, 3, 4, 5].map((index) => (
                <Skeleton key={index} className="h-16 w-full" />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>Nothing to review here</CardTitle>
                <CardDescription>
                  {hasAnyRowFilter(filters)
                    ? "No certificate matches these filters. Try clearing one of them."
                    : "This batch has no certificates yet. They appear as the files are read."}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex gap-3">
                <Button variant="outline" asChild>
                  <Link href={`/batches/${batchId}`}>Back to files</Link>
                </Button>
                {hasAnyRowFilter(filters) ? (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setSearchBox("");
                      setFilters({});
                    }}
                  >
                    Clear the filters
                  </Button>
                ) : null}
              </CardContent>
            </Card>
          ) : (
            <ResultsGrid
              columns={columns}
              rows={rows}
              selected={safeSelected}
              onSelectedChange={setSelected}
              editing={editing}
              onEditingChange={setEditing}
              onCommit={handleCommit}
              onApproveRow={handleApproveRow}
              rowSelection={rowSelection}
              onRowSelectionChange={setRowSelection}
              canEdit={canEdit}
              hasMore={Boolean(query.hasNextPage)}
              isLoadingMore={query.isFetchingNextPage}
              onLoadMore={() => void query.fetchNextPage()}
              onShowHelp={() => setShowHelp((open) => !open)}
            />
          )}
        </div>

        <div className="min-w-0">
          <RowDetailPane
            row={selectedRow}
            selectedFieldName={selectedFieldName}
            onSelectField={selectField}
            canEdit={canEdit}
            onApprove={handleApproveRow}
            onReprocess={handleReprocessRow}
            isBusy={isBusy}
          />
        </div>
      </div>
    </AppShell>
  );
}
