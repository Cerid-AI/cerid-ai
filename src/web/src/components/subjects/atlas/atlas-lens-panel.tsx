// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Lens chip selector for Atlas. Floats top-right of the Atlas surface;
// toggles via the L key (use-atlas-keyboard.ts) or click. Each chip is
// a tri-state toggle: off → on (lens active). Multiple chips active =
// stacked lens transforms, applied bottom-to-top in chip order.

import { LENS_ORDER, type LensId } from "@/lib/graph/lenses"

export interface AtlasLensPanelProps {
  /** Set of currently active lens ids */
  active: Set<LensId>
  /** Toggle a lens on/off */
  onToggle: (id: LensId) => void
  /** Whether the panel is visible at all (L-key toggle) */
  visible: boolean
}

export function AtlasLensPanel({ active, onToggle, visible }: AtlasLensPanelProps) {
  if (!visible) return null

  return (
    <div
      className="absolute right-3 top-3 z-10 rounded-lg border border-border bg-card/95 p-3 shadow-lg backdrop-blur"
      role="group"
      aria-label="Atlas lens controls"
    >
      <div className="mb-2 text-label-xs font-medium uppercase tracking-wide text-muted-foreground">
        Lenses
      </div>
      <ul className="flex flex-col gap-1">
        {LENS_ORDER.map((lens) => {
          const isActive = active.has(lens.id)
          return (
            <li key={lens.id}>
              <button
                type="button"
                onClick={() => onToggle(lens.id)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground/80 hover:bg-accent/40"
                }`}
                aria-pressed={isActive}
                title={lens.description}
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: lens.legendColor }}
                  aria-hidden="true"
                />
                <span className="grow">{lens.label}</span>
                <span className="text-label-xs text-muted-foreground">
                  {isActive ? "on" : "off"}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      <div className="mt-2 text-label-xs text-muted-foreground">
        Press <kbd className="rounded border px-1">L</kbd> to hide
      </div>
    </div>
  )
}
