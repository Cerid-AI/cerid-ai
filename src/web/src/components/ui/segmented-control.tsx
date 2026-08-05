// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useRef } from "react"
import type { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

export interface SegmentedOption<V extends string = string> {
  value: V
  label: string
  icon?: LucideIcon
}

interface SegmentedControlProps<V extends string = string> {
  value: V
  onChange: (value: V) => void
  options: ReadonlyArray<SegmentedOption<V>>
  size?: "sm" | "md"
  ariaLabel: string
  className?: string
}

/**
 * Single-select radiogroup styled as a connected button row. Replaces the
 * hand-rolled `div.flex.rounded-md.border > Button[variant=ghost]` pattern
 * that previously lived in audit-pane.tsx, settings-pane.tsx, and elsewhere.
 *
 * Keyboard:
 * - Left/Right arrows cycle the selection (with wrap).
 * - Tab focus follows the active option (roving tabindex).
 */
export function SegmentedControl<V extends string = string>({
  value,
  onChange,
  options,
  size = "md",
  ariaLabel,
  className,
}: SegmentedControlProps<V>) {
  const groupRef = useRef<HTMLDivElement>(null)
  const hasActiveOption = options.some((o) => o.value === value)

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return
    event.preventDefault()
    const dir = event.key === "ArrowRight" ? 1 : -1
    const next = (index + dir + options.length) % options.length
    onChange(options[next].value)
    // Focus the new active option after render.
    queueMicrotask(() => {
      const buttons = groupRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]')
      buttons?.[next]?.focus()
    })
  }

  const sizeClass =
    size === "sm" ? "h-7 px-2 text-label-sm" : "h-8 px-3 text-xs"

  return (
    <div
      ref={groupRef}
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn("inline-flex rounded-md border bg-background p-0.5", className)}
    >
      {options.map((opt, i) => {
        const isActive = opt.value === value
        const Icon = opt.icon
        // Roving tabindex — only the active option (or the first when none
        // are active) is in the tab order.
        const tabbable = isActive || (!hasActiveOption && i === 0)
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={isActive}
            tabIndex={tabbable ? 0 : -1}
            onClick={() => onChange(opt.value)}
            onKeyDown={(e) => handleKeyDown(e, i)}
            className={cn(
              "inline-flex items-center justify-center gap-1 rounded-sm font-medium transition-colors",
              sizeClass,
              isActive
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
