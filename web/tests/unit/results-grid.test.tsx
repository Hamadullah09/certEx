import * as React from "react";
import type { RowSelectionState } from "@tanstack/react-table";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { type CellPosition, ResultsGrid } from "@/components/review/results-grid";
import { buildGridColumns } from "@/lib/grid-columns";
import type { RowSummary } from "@/lib/schemas/rows";

/*
 * jsdom lays nothing out: every element measures zero, and the virtualiser renders
 * no rows at all unless its scroll element has a height. These two shims give it one
 * - a window's worth - so the assertions below are about the grid's own behaviour
 * rather than about jsdom's missing layout engine.
 */
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;

  HTMLElement.prototype.getBoundingClientRect = function boundingRect(): DOMRect {
    return {
      width: 1200,
      height: 600,
      top: 0,
      left: 0,
      right: 1200,
      bottom: 600,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect;
  };
});

const columns = buildGridColumns(["BIRTH"]);
const NAME_COLUMN = columns.findIndex((column) => column.id === "child_full_name");

function makeRow(overrides: Partial<RowSummary>): RowSummary {
  return {
    id: "row-1",
    unit_id: "unit-1",
    document_id: "doc-1",
    batch_id: "batch-1",
    serial_no: 1,
    file_name: "births.pdf",
    page_start: 1,
    page_end: 1,
    certificate_type: "BIRTH",
    review_status: "NEEDS_REVIEW",
    row_confidence: 0.72,
    flags: [],
    fields: { child_full_name: "Tarig Ahmed" },
    field_confidences: { child_full_name: 0.61 },
    extra_fields: {},
    ocr_used: false,
    detected_language: null,
    reviewed_at: null,
    updated_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

const rows: RowSummary[] = [
  makeRow({ id: "row-1", serial_no: 1 }),
  makeRow({
    id: "row-2",
    serial_no: 2,
    fields: { child_full_name: "عائشہ بی بی" },
    field_confidences: { child_full_name: 0.42 },
  }),
  makeRow({ id: "row-3", serial_no: 3, review_status: "MANUALLY_APPROVED" }),
];

interface HarnessProps {
  onCommit?: (row: RowSummary, field: string, value: string) => void;
  onApproveRow?: (row: RowSummary) => void;
  startAt?: CellPosition;
  canEdit?: boolean;
}

/** Holds the state the grid is controlled by, as the results page does. */
function Harness({ onCommit, onApproveRow, startAt, canEdit = true }: HarnessProps) {
  const [selected, setSelected] = React.useState<CellPosition>(
    startAt ?? { rowIndex: 0, columnIndex: NAME_COLUMN },
  );
  const [editing, setEditing] = React.useState(false);
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});

  return (
    <>
      <p data-testid="selection">{`${selected.rowIndex}:${selected.columnIndex}`}</p>
      <p data-testid="editing">{String(editing)}</p>
      <p data-testid="chosen">{Object.keys(rowSelection).sort().join(",")}</p>
      <ResultsGrid
        columns={columns}
        rows={rows}
        selected={selected}
        onSelectedChange={setSelected}
        editing={editing}
        onEditingChange={setEditing}
        onCommit={onCommit ?? (() => {})}
        onApproveRow={onApproveRow ?? (() => {})}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        canEdit={canEdit}
        hasMore={false}
        isLoadingMore={false}
        onLoadMore={() => {}}
        onShowHelp={() => {}}
      />
    </>
  );
}

function grid(): HTMLElement {
  return screen.getByRole("grid");
}

describe("ResultsGrid columns", () => {
  it("shows the serial number first and the certificate type second", () => {
    render(<Harness />);
    const headers = screen.getAllByRole("columnheader");
    // The first header is the row-choosing checkbox, which is not a data column.
    expect(headers[1]).toHaveTextContent("No.");
    expect(headers[2]).toHaveTextContent("Type");
  });

  it("names the field columns as the generated specs label them", () => {
    render(<Harness />);
    const headers = screen.getAllByRole("columnheader");
    expect(headers[3]).toHaveTextContent("Certificate number");
    expect(screen.getAllByRole("columnheader").some((header) => header.textContent?.includes("Name of child"))).toBe(
      true,
    );
  });
});

describe("ResultsGrid cells", () => {
  it("shows the confidence as a number beside the shading", () => {
    render(<Harness />);
    const cells = screen.getAllByRole("gridcell", {
      name: /Name of child: Tarig Ahmed, 61% - medium confidence/,
    });
    expect(cells[0]).toHaveTextContent("61%");
    expect(cells[0]?.className).toContain("bg-confidence-medium");
  });

  it("shades a low-confidence value from the low end of the ramp", () => {
    render(<Harness />);
    const cell = screen.getByRole("gridcell", { name: /Name of child: عائشہ بی بی, 42%/ });
    expect(cell.className).toContain("bg-confidence-low");
  });

  it("renders an Urdu value right to left and tagged as Urdu", () => {
    render(<Harness />);
    const cell = screen.getByRole("gridcell", { name: /Name of child: عائشہ بی بی/ });
    const value = within(cell).getByText("عائشہ بی بی");
    expect(value).toHaveAttribute("dir", "rtl");
    expect(value).toHaveAttribute("lang", "ur");
  });

  it("says empty rather than showing nothing at all", () => {
    render(<Harness />);
    expect(
      screen.getAllByRole("gridcell", { name: /Date of birth: empty/ }).length,
    ).toBeGreaterThan(0);
  });
});

describe("ResultsGrid keyboard", () => {
  it("moves down with the arrow key and with j", () => {
    render(<Harness />);
    grid().focus();
    fireEvent.keyDown(grid(), { key: "ArrowDown" });
    expect(screen.getByTestId("selection")).toHaveTextContent(`1:${NAME_COLUMN}`);
    fireEvent.keyDown(grid(), { key: "j" });
    expect(screen.getByTestId("selection")).toHaveTextContent(`2:${NAME_COLUMN}`);
  });

  it("moves up with k and stops at the first row", () => {
    render(<Harness startAt={{ rowIndex: 1, columnIndex: NAME_COLUMN }} />);
    fireEvent.keyDown(grid(), { key: "k" });
    expect(screen.getByTestId("selection")).toHaveTextContent(`0:${NAME_COLUMN}`);
    fireEvent.keyDown(grid(), { key: "k" });
    expect(screen.getByTestId("selection")).toHaveTextContent(`0:${NAME_COLUMN}`);
  });

  it("moves across the columns and stops at the ends", () => {
    render(<Harness startAt={{ rowIndex: 0, columnIndex: 0 }} />);
    fireEvent.keyDown(grid(), { key: "ArrowLeft" });
    expect(screen.getByTestId("selection")).toHaveTextContent("0:0");
    fireEvent.keyDown(grid(), { key: "ArrowRight" });
    expect(screen.getByTestId("selection")).toHaveTextContent("0:1");
    fireEvent.keyDown(grid(), { key: "End" });
    expect(screen.getByTestId("selection")).toHaveTextContent(`0:${columns.length - 1}`);
    fireEvent.keyDown(grid(), { key: "Home" });
    expect(screen.getByTestId("selection")).toHaveTextContent("0:0");
  });

  it("opens the editor on Enter and closes it on Escape without saving", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.keyDown(grid(), { key: "Enter" });
    expect(screen.getByTestId("editing")).toHaveTextContent("true");

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Tariq Ahmed" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.getByTestId("editing")).toHaveTextContent("false");
  });

  it("saves on Enter", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.keyDown(grid(), { key: "Enter" });
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Tariq Ahmed" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onCommit).toHaveBeenCalledWith(
      expect.objectContaining({ id: "row-1" }),
      "child_full_name",
      "Tariq Ahmed",
    );
    expect(screen.getByTestId("editing")).toHaveTextContent("false");
  });

  it("saves and moves to the next field on Tab", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.keyDown(grid(), { key: "Enter" });
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Tariq" } });
    fireEvent.keyDown(input, { key: "Tab" });

    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("selection")).toHaveTextContent(`0:${NAME_COLUMN + 1}`);
  });

  it("does not open an editor for a reviewer who may not correct rows", () => {
    render(<Harness canEdit={false} />);
    fireEvent.keyDown(grid(), { key: "Enter" });
    expect(screen.getByTestId("editing")).toHaveTextContent("false");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("approves the selected certificate with a", () => {
    const onApproveRow = vi.fn();
    render(<Harness onApproveRow={onApproveRow} startAt={{ rowIndex: 1, columnIndex: 0 }} />);
    fireEvent.keyDown(grid(), { key: "a" });
    expect(onApproveRow).toHaveBeenCalledWith(expect.objectContaining({ id: "row-2" }));
  });

  it("ticks and unticks a row with the space bar", () => {
    render(<Harness startAt={{ rowIndex: 2, columnIndex: 0 }} />);
    fireEvent.keyDown(grid(), { key: " " });
    expect(screen.getByTestId("chosen")).toHaveTextContent("row-3");
    fireEvent.keyDown(grid(), { key: " " });
    expect(screen.getByTestId("chosen")).toHaveTextContent("");
  });

  it("leaves the arrow keys to the editor while a cell is being typed in", () => {
    render(<Harness />);
    fireEvent.keyDown(grid(), { key: "Enter" });
    const input = screen.getByRole("textbox");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    // Still on the same cell: the caret moved, not the selection.
    expect(screen.getByTestId("selection")).toHaveTextContent(`0:${NAME_COLUMN}`);
  });
});

describe("ResultsGrid selection", () => {
  it("chooses every loaded row from the header checkbox", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Choose every row shown/ }));
    expect(screen.getByTestId("chosen")).toHaveTextContent("row-1,row-2,row-3");
  });

  it("chooses one row from its own checkbox", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Choose certificate number 2/ }));
    expect(screen.getByTestId("chosen")).toHaveTextContent("row-2");
  });
});
