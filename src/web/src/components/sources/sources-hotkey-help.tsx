// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { useHotkey } from "@/hooks/use-hotkey"

/**
 * Sources hotkey help overlay. Press `?` to surface.
 *
 * Documents the Sources-pane hotkey suite. Bindings that have no
 * target surface yet render with a "coming soon" badge; binding
 * the keys up front lets new surfaces just install a handler.
 *
 * Renders a Liquid-Glass surface? No — Radix Dialog's standard
 * surface is correct here; the Liquid-Glass treatment is reserved
 * for the 9 surfaces enumerated in §5.2. This is a help dialog,
 * not a brand-weight moment.
 */

interface HotkeyEntry {
  keys: string
  action: string
  available: boolean // false until the target surface ships
}

const SOURCES_HOTKEYS: HotkeyEntry[] = [
  { keys: "?", action: "Show this help overlay", available: true },
  { keys: "⌘ 1", action: "Library tab", available: true },
  { keys: "⌘ 2", action: "Activity tab", available: true },
  { keys: "⌘ 3", action: "Meetings tab", available: true },
  { keys: "⌘ 4", action: "Connectors tab", available: true },
  { keys: "⌘ ⇧ S", action: "Open Add Source FAB (radial menu)", available: false },
  { keys: "⌘ ⇧ C", action: "Toggle Constellation view", available: false },
  { keys: "⌘ ⇧ V", action: "Start Voice Note (Core dictation)", available: false },
  { keys: "⌘ ⇧ W", action: "Open Webhook share card for the active source", available: false },
  { keys: "F", action: "Filter — focus connector-list search", available: false },
  { keys: "R", action: "Refresh / re-sync the focused source", available: false },
  { keys: "D", action: "Open detail pane for focused source", available: false },
  { keys: "Esc", action: "Close any open overlay / FAB / wizard / Constellation", available: true },
]

export function SourcesHotkeyHelp() {
  const [open, setOpen] = useState(false)

  useHotkey("?", () => setOpen((prev) => !prev), { allowInInput: false })
  useHotkey("escape", () => setOpen(false), {
    allowInInput: true,
    enabled: open,
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Sources hotkeys</DialogTitle>
          <DialogDescription>
            Keyboard-first navigation for the Sources surface.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          {SOURCES_HOTKEYS.map((entry) => (
            <div
              key={entry.keys}
              className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm"
            >
              <kbd className="rounded border border-border bg-muted/40 px-2 py-0.5 font-mono text-label-xs">
                {entry.keys}
              </kbd>
              <span
                className={
                  entry.available
                    ? "ml-3 grow text-right text-foreground"
                    : "ml-3 grow text-right text-muted-foreground"
                }
              >
                {entry.action}
                {!entry.available && (
                  <span className="ml-2 text-label-xxs uppercase tracking-wide text-muted-foreground">
                    coming soon
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-label-xs text-muted-foreground">
          ⌘ is Cmd on Mac, Ctrl elsewhere. Single-letter hotkeys only
          fire when an input field is not focused.
        </p>
      </DialogContent>
    </Dialog>
  )
}
