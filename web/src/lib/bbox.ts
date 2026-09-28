/**
 * Turning a field's bounding box into pixels over a rendered page image.
 *
 * The API gives a box as fractions of the page, which is the only form that
 * survives the image being rendered at whatever size the reviewer's window
 * happens to be. Two steps are needed: work out which part of the pane the image
 * actually covers (it is letterboxed, because a certificate is portrait and the
 * pane is not), then place the box inside that area.
 *
 * Both steps are pure and live here rather than inside a component, so they can be
 * checked against known numbers instead of by dragging a window.
 */

import type { Bbox } from "@/lib/schemas/rows";

export interface Size {
  width: number;
  height: number;
}

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export const EMPTY_RECT: Rect = { left: 0, top: 0, width: 0, height: 0 };

/**
 * Where an image of `natural` proportions sits inside `container` under
 * `object-fit: contain` - the same rectangle the browser paints it into, centred
 * on both axes with the leftover space split evenly.
 */
export function containedImageRect(natural: Size, container: Size): Rect {
  if (
    natural.width <= 0 ||
    natural.height <= 0 ||
    container.width <= 0 ||
    container.height <= 0
  ) {
    return EMPTY_RECT;
  }

  const scale = Math.min(container.width / natural.width, container.height / natural.height);
  const width = natural.width * scale;
  const height = natural.height * scale;
  return {
    left: (container.width - width) / 2,
    top: (container.height - height) / 2,
    width,
    height,
  };
}

function clampFraction(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * A fractional box as pixels inside `area`.
 *
 * Coordinates are clamped to the page and ordered, because a box whose corners
 * arrive the other way round - which OCR on a rotated scan can produce - would
 * otherwise come out with a negative width and disappear.
 *
 * `minSize` keeps a box that is a single thin character from rendering as an
 * invisible hairline.
 */
export function scaleBbox(bbox: Bbox, area: Rect, minSize = 0): Rect {
  if (area.width <= 0 || area.height <= 0) return EMPTY_RECT;

  const x0 = clampFraction(Math.min(bbox.x0, bbox.x1));
  const x1 = clampFraction(Math.max(bbox.x0, bbox.x1));
  const y0 = clampFraction(Math.min(bbox.y0, bbox.y1));
  const y1 = clampFraction(Math.max(bbox.y0, bbox.y1));

  const width = Math.max((x1 - x0) * area.width, minSize);
  const height = Math.max((y1 - y0) * area.height, minSize);
  return {
    left: area.left + x0 * area.width,
    top: area.top + y0 * area.height,
    // A box widened to `minSize` must not be pushed past the right-hand edge of
    // the image, or it will point at the margin instead of at the value.
    width: Math.min(width, area.width),
    height: Math.min(height, area.height),
  };
}

/** `scaleBbox` for the common case: fractions straight onto a laid-out image. */
export function bboxRect(bbox: Bbox, image: Size, container: Size, minSize = 0): Rect {
  return scaleBbox(bbox, containedImageRect(image, container), minSize);
}
