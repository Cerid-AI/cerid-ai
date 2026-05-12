// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Crown } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export interface TierOption {
  id: string
  label: string
  Icon?: LucideIcon
  description: string
  locked?: boolean
  /** Label rendered inside a Pro/Locked `<Badge>` when locked. Defaults to "Pro". */
  lockedReason?: string
}

interface TierSelectorProps {
  value: string
  onChange: (id: string) => void
  options: TierOption[]
  ariaLabel: string
  /** Override the active-state class (default is `border-brand bg-brand/5`).
   *  Pipeline pane uses `border-primary bg-primary/5` to fit its surrounding density. */
  activeClassName?: string
  className?: string
}

/**
 * Three-card radiogroup for selecting a preset or tier. Replaces the
 * hand-rolled tier-card grids previously found in settings-pane.tsx
 * (Quick/Balanced/Maximum) and pipeline-section.tsx (Efficient/Balanced/Maximum).
 */
export function TierSelector({
  value,
  onChange,
  options,
  ariaLabel,
  activeClassName = "border-brand bg-brand/5",
  className,
}: TierSelectorProps) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn("grid grid-cols-3 gap-2", className)}
    >
      {options.map((opt) => {
        const isActive = opt.id === value
        const isLocked = opt.locked === true
        const lockedLabel = opt.lockedReason ?? "Pro"
        return (
          <button
            key={opt.id}
            type="button"
            role="radio"
            aria-checked={isActive}
            disabled={isLocked}
            onClick={() => !isLocked && onChange(opt.id)}
            className={cn(
              "rounded-lg border p-3 text-left transition-colors",
              isLocked
                ? "opacity-50 cursor-not-allowed border-muted"
                : isActive
                  ? activeClassName
                  : "border-muted hover:border-muted-foreground/30",
            )}
          >
            <div className="flex items-center gap-1.5">
              {opt.Icon && (
                // `text-primary` (rather than `text-brand`) so the icon
                // matches whichever active accent the caller picked via
                // `activeClassName` (pipeline pane overrides to primary).
                <opt.Icon className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
              )}
              <span className="text-sm font-medium">{opt.label}</span>
              {isLocked && (
                <Badge
                  variant="outline"
                  className="text-label-xs px-1.5 py-0 text-gold border-gold"
                >
                  <Crown className="mr-0.5 h-2.5 w-2.5" aria-hidden="true" />
                  {lockedLabel}
                </Badge>
              )}
            </div>
            <p className="mt-1 text-label-sm leading-tight text-muted-foreground">
              {opt.description}
            </p>
          </button>
        )
      })}
    </div>
  )
}
