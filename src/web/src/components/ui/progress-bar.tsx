// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { cn } from "@/lib/utils"

type Variant = "default" | "success" | "warning" | "danger"
type Size = "sm" | "md" | "lg"

interface ProgressBarProps {
  pct: number
  variant?: Variant
  size?: Size
  label?: string
  className?: string
  /** Override the fill color (e.g. tier-derived `bg-emerald-500`). Wins over `variant`. */
  fillClassName?: string
}

const TRACK_HEIGHT: Record<Size, string> = {
  sm: "h-1",
  md: "h-1.5",
  lg: "h-2",
}

const FILL_COLOR: Record<Variant, string> = {
  default: "bg-primary",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
}

export function ProgressBar({
  pct,
  variant = "default",
  size = "md",
  label,
  className,
  fillClassName,
}: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cn(
        "w-full overflow-hidden rounded-full bg-muted",
        TRACK_HEIGHT[size],
        className,
      )}
    >
      <div
        // drift-allowed: bar fill width is runtime data — the only legitimate
        // inline-style use in the codebase per docs/CONVENTIONS.md.
        style={{ width: `${clamped}%` }}
        className={cn(
          "h-full rounded-full transition-[width] duration-300 ease-out",
          fillClassName ?? FILL_COLOR[variant],
        )}
      />
    </div>
  )
}
