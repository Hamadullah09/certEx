import { describe, expect, it } from "vitest";

import { EMPTY_RECT, bboxRect, containedImageRect, scaleBbox } from "@/lib/bbox";

// A portrait page in a wider pane: the image is letterboxed left and right, which
// is the case the review pane is always in.
const PORTRAIT = { width: 100, height: 200 };
const PANE = { width: 100, height: 100 };

describe("containedImageRect", () => {
  it("centres a portrait image in a square pane", () => {
    expect(containedImageRect(PORTRAIT, PANE)).toEqual({
      left: 25,
      top: 0,
      width: 50,
      height: 100,
    });
  });

  it("centres a landscape image vertically", () => {
    expect(containedImageRect({ width: 200, height: 100 }, PANE)).toEqual({
      left: 0,
      top: 25,
      width: 100,
      height: 50,
    });
  });

  it("fills the pane when the proportions match", () => {
    expect(containedImageRect({ width: 500, height: 500 }, PANE)).toEqual({
      left: 0,
      top: 0,
      width: 100,
      height: 100,
    });
  });

  it("is empty before the image or the pane has been measured", () => {
    expect(containedImageRect({ width: 0, height: 0 }, PANE)).toEqual(EMPTY_RECT);
    expect(containedImageRect(PORTRAIT, { width: 0, height: 0 })).toEqual(EMPTY_RECT);
  });
});

describe("scaleBbox", () => {
  const area = { left: 25, top: 0, width: 50, height: 100 };

  it("places a fractional box inside the image, not inside the pane", () => {
    expect(scaleBbox({ x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.4 }, area)).toEqual({
      left: 30,
      top: 20,
      width: 20,
      height: 20,
    });
  });

  it("clamps a box that runs off the page", () => {
    expect(scaleBbox({ x0: -0.5, y0: -1, x1: 1.5, y1: 2 }, area)).toEqual({
      left: 25,
      top: 0,
      width: 50,
      height: 100,
    });
  });

  it("orders corners that arrived the other way round", () => {
    const forwards = scaleBbox({ x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.4 }, area);
    expect(scaleBbox({ x0: 0.5, y0: 0.4, x1: 0.1, y1: 0.2 }, area)).toEqual(forwards);
  });

  it("grows a hairline box to the minimum so it can still be seen", () => {
    const box = scaleBbox({ x0: 0.5, y0: 0.5, x1: 0.5, y1: 0.5 }, area, 8);
    expect(box.width).toBe(8);
    expect(box.height).toBe(8);
  });

  it("never widens a box past the edge of the image", () => {
    const box = scaleBbox({ x0: 0, y0: 0, x1: 1, y1: 1 }, area, 200);
    expect(box.width).toBe(50);
    expect(box.height).toBe(100);
  });

  it("is empty when there is nothing rendered to draw on", () => {
    expect(scaleBbox({ x0: 0, y0: 0, x1: 1, y1: 1 }, EMPTY_RECT)).toEqual(EMPTY_RECT);
  });
});

describe("bboxRect", () => {
  it("scales and offsets in one step", () => {
    expect(bboxRect({ x0: 0, y0: 0, x1: 1, y1: 0.5 }, PORTRAIT, PANE)).toEqual({
      left: 25,
      top: 0,
      width: 50,
      height: 50,
    });
  });
});
