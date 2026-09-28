"use client";

import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * The keyboard map, written out.
 *
 * Shown inline rather than in a dialog: a reviewer looks this up while working, and
 * a modal would take the focus away from the grid they are trying to use it on.
 */
const SHORTCUTS: readonly { keys: string; action: string }[] = [
  { keys: "Arrow keys", action: "Move from box to box" },
  { keys: "j / k", action: "Next / previous certificate" },
  { keys: "Enter", action: "Type in this box" },
  { keys: "Escape", action: "Forget what you just typed" },
  { keys: "Tab", action: "Next field" },
  { keys: "a", action: "Approve this certificate" },
  { keys: "Space", action: "Tick this row, to approve several at once" },
  { keys: "Page Up / Page Down", action: "Jump ten certificates" },
  { keys: "Home / End", action: "First / last column" },
  { keys: "?", action: "Show or hide this list" },
];

export function KeyboardHelp({ onClose }: { onClose: () => void }) {
  return (
    <Card className="border-primary-border/60 bg-primary-surface/40">
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <h2 className="text-xl">Keyboard shortcuts</h2>
          <Button variant="ghost" size="icon-sm" aria-label="Hide keyboard shortcuts" onClick={onClose}>
            <X aria-hidden="true" />
          </Button>
        </div>
        <dl className="mt-4 grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.keys} className="flex items-baseline gap-3">
              <dt className="shrink-0">
                <kbd className="rounded-md border border-border bg-card px-2 py-1 font-mono text-sm font-semibold text-foreground">
                  {shortcut.keys}
                </kbd>
              </dt>
              <dd className="text-base">{shortcut.action}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
