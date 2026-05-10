// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { CheckCircle, AlertCircle, XCircle, MinusCircle, ExternalLink } from "lucide-react"
import { cn } from "@/lib/utils"
import type { TrustComponent, TrustScore, ComponentStatus } from "@/lib/types/trust-score"
import { getBandDisplay } from "@/lib/types/trust-score"

interface TrustScoreHoverProps {
  data: TrustScore
  onDetailsClick?: () => void
}

function StatusIcon({ status, className }: { status: ComponentStatus; className?: string }) {
  const cls = cn("h-3.5 w-3.5 shrink-0", className)
  switch (status) {
    case "ok":
      return <CheckCircle className={cn(cls, "text-green-600 dark:text-green-400")} aria-label="OK" />
    case "warn":
      return <AlertCircle className={cn(cls, "text-amber-600 dark:text-amber-400")} aria-label="Warning" />
    case "fail":
      return <XCircle className={cn(cls, "text-red-600 dark:text-red-400")} aria-label="Failing" />
    case "not_available":
      return <MinusCircle className={cn(cls, "text-muted-foreground")} aria-label="Not available" />
  }
}

function formatValue(component: TrustComponent): string {
  if (component.value === null) return "—"
  // Preservation health is a ratio 0–1 displayed as a fraction via note
  if (component.note && /^\d+\/\d+$/.test(component.note)) {
    return component.note
  }
  // Percentage-based components
  const pctIds = ["verification_coverage", "memory_recall", "faithfulness", "retrieval_ndcg10", "user_agreement"]
  if (pctIds.includes(component.id)) {
    return `${Math.round(component.value * 100)}%`
  }
  return component.value.toFixed(2)
}

function formatTarget(component: TrustComponent): string {
  if (component.target === null) return ""
  const pctIds = ["verification_coverage", "memory_recall", "faithfulness", "retrieval_ndcg10", "user_agreement"]
  if (pctIds.includes(component.id)) {
    return `/ ${Math.round(component.target * 100)}%`
  }
  if (component.id === "preservation_health") return "/ 100%"
  return `/ ${component.target.toFixed(2)}`
}

export function TrustScoreHover({ data, onDetailsClick }: TrustScoreHoverProps) {
  const display = getBandDisplay(data.band)
  const updatedAt = data.updated_at
    ? new Date(data.updated_at).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—"

  return (
    <div className="w-80 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground">
          Cerid Trust Score:{" "}
          <span className={display.textClass}>
            {data.score !== null ? data.score : "—"}
          </span>
        </p>
        <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider", display.bgClass, display.textClass)}>
          {display.label}
        </span>
      </div>

      <p className="text-[10px] text-muted-foreground">Updated {updatedAt}</p>

      {/* Component list */}
      <ul className="space-y-1.5" aria-label="Trust score components">
        {(data.components ?? []).map((comp) => (
          <li key={comp.id} className="flex items-center gap-2 text-xs">
            <StatusIcon status={comp.status} />
            <span className="min-w-0 flex-1 truncate text-foreground/80">{comp.label}</span>
            <span className={cn("tabular-nums font-medium", comp.status === "ok" && "text-green-700 dark:text-green-400", comp.status === "warn" && "text-amber-700 dark:text-amber-400", comp.status === "fail" && "text-red-700 dark:text-red-400", comp.status === "not_available" && "text-muted-foreground")}>
              {formatValue(comp)}
            </span>
            {comp.target !== null && (
              <span className="text-muted-foreground">{formatTarget(comp)}</span>
            )}
          </li>
        ))}
      </ul>

      {/* Footer */}
      {onDetailsClick && (
        <button
          type="button"
          onClick={onDetailsClick}
          className="flex w-full items-center justify-end gap-1 text-[10px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Click for details
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
