"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight, ImageOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageImage } from "@/hooks/use-rows";
import { type Rect, type Size, bboxRect } from "@/lib/bbox";
import { ApiError } from "@/lib/api";
import type { Bbox } from "@/lib/schemas/rows";

export interface PagePreviewProps {
  documentId: string;
  /** Pages of the source file this certificate covers. */
  pages: number[];
  pageNumber: number;
  onPageChange: (page: number) => void;
  /** Box to draw, in page fractions, or null when the value has no position. */
  bbox: Bbox | null;
  fieldLabel: string | null;
  fileName: string;
}

/** How small a drawn box may get before it stops being findable by eye. */
const MIN_BOX_PIXELS = 8;

/**
 * The page a value was read from, with the value's box drawn over it.
 *
 * The box arrives as fractions of the page, so it has to be scaled to however
 * large the image ended up - which changes when the window is resized, when the
 * pane is dragged, and when a page of a different shape is loaded. The pane
 * therefore measures the container with a `ResizeObserver` and the image by its own
 * natural size, and recomputes; see `lib/bbox.ts` for the arithmetic.
 */
export function PagePreview({
  documentId,
  pages,
  pageNumber,
  onPageChange,
  bbox,
  fieldLabel,
  fileName,
}: PagePreviewProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [containerSize, setContainerSize] = React.useState<Size>({ width: 0, height: 0 });
  const [naturalSize, setNaturalSize] = React.useState<Size>({ width: 0, height: 0 });
  const [objectUrl, setObjectUrl] = React.useState<string | null>(null);

  const image = usePageImage(documentId, pageNumber);

  // The blob is cached by the query; the object URL is not, because it has to be
  // revoked or the browser holds every page a reviewer has looked at all day.
  React.useEffect(() => {
    const blob = image.data;
    if (!blob) {
      setObjectUrl(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image.data]);

  React.useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) setContainerSize({ width: box.width, height: box.height });
    });
    observer.observe(element);
    setContainerSize({ width: element.clientWidth, height: element.clientHeight });
    return () => observer.disconnect();
  }, []);

  const box: Rect | null =
    bbox && naturalSize.width > 0 && containerSize.width > 0
      ? bboxRect(bbox, naturalSize, containerSize, MIN_BOX_PIXELS)
      : null;

  const position = pages.indexOf(pageNumber);
  const previousPage = position > 0 ? pages[position - 1] : undefined;
  const nextPage = position >= 0 && position < pages.length - 1 ? pages[position + 1] : undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-semibold text-muted-foreground">
          {pages.length > 1
            ? `Page ${pageNumber} of this certificate (${pages.length} pages)`
            : `Page ${pageNumber}`}
        </p>
        {pages.length > 1 ? (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="Previous page"
              disabled={previousPage === undefined}
              onClick={() => previousPage !== undefined && onPageChange(previousPage)}
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="Next page"
              disabled={nextPage === undefined}
              onClick={() => nextPage !== undefined && onPageChange(nextPage)}
            >
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </div>

      <div
        ref={containerRef}
        className="relative h-[24rem] w-full overflow-hidden rounded-lg border-2 border-border bg-muted"
      >
        {image.isPending ? <Skeleton className="size-full rounded-none" /> : null}

        {image.isError ? (
          <div className="flex size-full flex-col items-center justify-center gap-2 p-6 text-center">
            <ImageOff aria-hidden="true" className="size-8 text-muted-foreground" />
            <p className="text-base font-semibold text-foreground">
              This page cannot be shown
            </p>
            <p className="max-w-sm text-sm text-muted-foreground">
              {image.error instanceof ApiError
                ? image.error.userMessage
                : "The page image could not be loaded."}
            </p>
          </div>
        ) : null}

        {objectUrl ? (
          // A plain <img>: the source is a blob from an authenticated request, which
          // cannot be handed to Next's image optimiser, and must not be cached by it.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={objectUrl}
            alt={`Page ${pageNumber} of ${fileName}`}
            onLoad={(event) =>
              setNaturalSize({
                width: event.currentTarget.naturalWidth,
                height: event.currentTarget.naturalHeight,
              })
            }
            className="absolute inset-0 size-full object-contain"
          />
        ) : null}

        {box ? (
          <div
            // Decorative: the value and its confidence are read out by the field
            // list beside this pane, so the box is not announced twice.
            aria-hidden="true"
            className="pointer-events-none absolute rounded-sm bg-warning/20 [outline:3px_solid_var(--warning)]"
            style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
          />
        ) : null}
      </div>

      <p className="text-sm text-muted-foreground">
        {bbox
          ? `The box shows where ${fieldLabel ?? "this value"} was read.`
          : fieldLabel
            ? `${fieldLabel} has no place on the page - it was typed in, or was not found.`
            : "Choose a field to see where it was read."}
      </p>
    </div>
  );
}
