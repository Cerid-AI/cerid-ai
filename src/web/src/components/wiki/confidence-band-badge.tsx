// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { CheckCircle, Minus, AlertCircle, HelpCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ConfidenceBand } from "@/lib/types/wiki"

interface ConfidenceBandBadgeProps {
  band: ConfidenceBand
  className?: string
}

const BAND_CONFIG: Record<
  ConfidenceBand,
  {
    label: string
    Icon: React.ElementType
    className: string
  }
> = {
  high: {
    label: "high",
    Icon: CheckCircle,
    className: "bg-green-500/15 text-green-700 dark:text-green-400",
  },
  medium: {
    label: "medium",
    Icon: Minus,
    className: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  },
  low: {
    label: "low",
    Icon: AlertCircle,
    className: "bg-red-500/15 text-red-700 dark:text-red-400",
  },
  unknown: {
    label: "unknown",
    Icon: HelpCircle,
    className: "bg-muted text-muted-foreground",
  },
}

export function ConfidenceBandBadge({ band, className }: ConfidenceBandBadgeProps) {
  const { label, Icon, className: bandClass } = BAND_CONFIG[band]
  return (
    <span
      aria-label={`Confidence: ${label}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        bandClass,
        className,
      )}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label}
    </span>
  )
}
