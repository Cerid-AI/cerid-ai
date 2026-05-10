// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { ContradictionFinding } from "@/lib/types/wiki"

interface ContradictionItemProps {
  finding: ContradictionFinding
}

const SEVERITY_CONFIG: Record<
  ContradictionFinding["severity"],
  { label: string; className: string }
> = {
  low: {
    label: "Low",
    className: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  },
  medium: {
    label: "Medium",
    className: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  },
  high: {
    label: "High",
    className: "bg-red-500/15 text-red-700 dark:text-red-400",
  },
}

function formatDetectedAt(iso: string): string {
  if (!iso) return ""
  try {
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return iso
    const seconds = Math.floor((Date.now() - ms) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return iso
  }
}

export function ContradictionItem({ finding }: ContradictionItemProps) {
  const { label, className } = SEVERITY_CONFIG[finding.severity]

  return (
    <Card>
      <CardContent className="space-y-2 p-4">
        {/* Header: severity + detected_at */}
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              className,
            )}
            aria-label={`Severity: ${label}`}
          >
            {label}
          </span>
          {finding.detected_at && (
            <span className="text-xs text-muted-foreground">
              Detected {formatDetectedAt(finding.detected_at)}
            </span>
          )}
        </div>

        {/* Claim A */}
        <blockquote
          className="border-l-2 border-muted-foreground/30 pl-3 text-sm text-foreground/80"
          aria-label="Claim A"
        >
          {finding.claim_a_text}
        </blockquote>

        {/* vs. separator */}
        <p className="text-center text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          contradicts
        </p>

        {/* Claim B */}
        <blockquote
          className="border-l-2 border-muted-foreground/30 pl-3 text-sm text-foreground/80"
          aria-label="Claim B"
        >
          {finding.claim_b_text}
        </blockquote>
      </CardContent>
    </Card>
  )
}
