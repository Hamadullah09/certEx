"use client";

import * as React from "react";
import { useDropzone } from "react-dropzone";
import { FolderOpen, UploadCloud } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DROPZONE_ACCEPT } from "@/lib/schemas/batches";
import { cn } from "@/lib/utils";

interface UploadDropzoneProps {
  onDrop: (files: File[]) => void;
  disabled?: boolean;
}

/**
 * Drag-and-drop target for files and whole folders.
 *
 * Folder upload is the common case here - a records office has a directory of
 * scans, not a hand-picked selection - so a separate control sets
 * `webkitdirectory`, which react-dropzone's input cannot carry alongside the
 * normal multi-file input.
 *
 * The dropzone is also a real button for keyboard users: react-dropzone wires up
 * Enter and Space to open the picker, and the visible focus ring comes from the
 * global `:focus-visible` rule.
 */
export function UploadDropzone({ onDrop, disabled = false }: UploadDropzoneProps) {
  const folderInputRef = React.useRef<HTMLInputElement>(null);

  const handleDrop = React.useCallback(
    (accepted: File[], rejected: { file: File }[]) => {
      // Screening happens in the page so that everything a user dropped is
      // accounted for, including what the accept filter turned away.
      onDrop([...accepted, ...rejected.map((entry) => entry.file)]);
    },
    [onDrop],
  );

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    onDrop: handleDrop,
    accept: DROPZONE_ACCEPT,
    disabled,
    multiple: true,
    // Screened in the page instead, so an oversized file gets an explanation
    // rather than being silently ignored.
    maxSize: undefined,
    noClick: false,
    noKeyboard: false,
  });

  function handleFolderPick(event: React.ChangeEvent<HTMLInputElement>): void {
    const files = Array.from(event.target.files ?? []);
    if (files.length) onDrop(files);
    // Reset so picking the same folder twice still fires a change event.
    event.target.value = "";
  }

  return (
    <div>
      <div
        {...getRootProps()}
        aria-label="Drop certificates here, or activate to browse for files"
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center rounded-xl border-[3px] border-dashed px-6 py-14 text-center transition-colors",
          // At rest the target is already tinted rather than an empty outline,
          // so it reads as somewhere to put something.
          "border-primary-border bg-primary-surface/50 hover:border-primary hover:bg-primary-surface",
          isDragActive && !isDragReject && "border-primary bg-primary-surface",
          isDragReject && "border-destructive bg-destructive-surface",
          // Greyed out rather than faded: opacity would drag the label's
          // contrast down at the moment it has to explain why nothing happens.
          disabled &&
            "cursor-not-allowed border-border bg-muted text-muted-foreground hover:border-border hover:bg-muted [&_svg]:text-muted-foreground",
        )}
      >
        <input {...getInputProps()} />
        <UploadCloud aria-hidden="true" className="size-12 text-primary" />
        <p className="mt-4 text-xl font-bold">
          {isDragActive ? "Drop the files to add them" : "Drag certificates here"}
        </p>
        <p className="mt-1.5 text-base text-muted-foreground">
          PDF, Word, images or a ZIP — or click to browse
        </p>
      </div>

      <div className="mt-4 flex justify-center">
        <input
          ref={folderInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFolderPick}
          // Non-standard but universally supported for directory upload; React
          // needs them lowercased to pass them through to the DOM.
          {...{ webkitdirectory: "", directory: "" }}
        />
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          onClick={() => folderInputRef.current?.click()}
        >
          <FolderOpen aria-hidden="true" />
          Select a folder
        </Button>
      </div>
    </div>
  );
}
