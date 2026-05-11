// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Surfaces the v0.92 ``/health.invariants`` block — operational invariants
 * that the backend monitors but that don't have a dedicated pane.
 *
 * Fields shown here are intentionally the ones NOT already surfaced in
 * neighbouring cards:
 *   - Processor metrics live in `<ProcessorPane>`.
 *   - TrustScore lives in the header `<TrustScoreChip>` + observability dashboard.
 *
 * What's left for this card:
 *   - Overall ``healthy_invariants`` rollup.
 *   - NLI model load state (critical for verification path).
 *   - Memory consolidation failures (24h) — O.2 invariant.
 *   - Verification report orphans count — W/V hygiene.
 *   - Swallowed-error summary (last hour) — observability invariant.
 */

import { useQuery } from "@tanstack/react-query"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { ShieldCheck, ShieldAlert, AlertTriangle, CheckCircle2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { fetchHealth } from "@/lib/api"
import type { HealthInvariants } from "@/lib/types"

interface RowProps {
  label: string
  tooltip: string
  value: React.ReactNode
  status: "ok" | "warn" | "error" | "unknown"
}

const STATUS_DOT: Record<RowProps["status"], string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  error: "bg-red-500",
  unknown: "bg-muted-foreground/40",
}

function InvariantRow({ label, tooltip, value, status }: RowProps) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={cn("h-1.5 w-1.5 shrink-0 rounded-full", STATUS_DOT[status])}
              aria-hidden="true"
            />
            <span className="truncate text-sm text-foreground">{label}</span>
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          {tooltip}
        </TooltipContent>
      </Tooltip>
      <span className="shrink-0 text-sm tabular-nums text-muted-foreground">{value}</span>
    </div>
  )
}

function totalSwallowed(by_module?: Record<string, number>): number {
  if (!by_module) return 0
  return Object.values(by_module).reduce((a, b) => a + b, 0)
}

function summarize(inv: HealthInvariants | undefined): {
  rollupOk: boolean
  rollupLabel: string
  problemCount: number
} {
  if (!inv) return { rollupOk: false, rollupLabel: "Unknown", problemCount: 0 }
  let problems = 0
  if (inv.healthy_invariants === false) problems += 1
  if (inv.nli_model_loaded === false) problems += 1
  if ((inv.memory_consolidation_failures_last_24h ?? 0) > 0) problems += 1
  if ((inv.verification_report_orphans ?? 0) > 0) problems += 1
  if (totalSwallowed(inv.swallowed_errors_last_hour) > 0) problems += 1
  if (problems === 0) return { rollupOk: true, rollupLabel: "All healthy", problemCount: 0 }
  return {
    rollupOk: false,
    rollupLabel: `${problems} ${problems === 1 ? "issue" : "issues"}`,
    problemCount: problems,
  }
}

export function InvariantsCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
  })

  if (isLoading) {
    return <Skeleton className="h-44 w-full rounded-lg" />
  }
  if (isError || !data) {
    return null
  }
  const inv = data.invariants
  const summary = summarize(inv)

  const swallowedTotal = totalSwallowed(inv?.swallowed_errors_last_hour)
  const memFails = inv?.memory_consolidation_failures_last_24h ?? 0
  const orphans = inv?.verification_report_orphans ?? 0
  const nli = inv?.nli_model_loaded

  return (
    <TooltipProvider>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-2">
          <div className="flex items-center gap-2">
            {summary.rollupOk ? (
              <ShieldCheck className="h-4 w-4 text-emerald-500" aria-hidden="true" />
            ) : (
              <ShieldAlert className="h-4 w-4 text-amber-500" aria-hidden="true" />
            )}
            <CardTitle className="text-sm">Operational invariants</CardTitle>
          </div>
          <Badge variant={summary.rollupOk ? "secondary" : "destructive"}>
            {summary.rollupLabel}
          </Badge>
        </CardHeader>
        <CardContent className="p-3 pt-0">
          <div className="divide-y divide-border/40">
            <InvariantRow
              label="NLI verification model"
              tooltip="Loaded at startup; verification falls back to lexical-only when this is false."
              status={nli === true ? "ok" : nli === false ? "error" : "unknown"}
              value={
                nli === true ? (
                  <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                    <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                    loaded
                  </span>
                ) : nli === false ? (
                  <span className="text-red-600 dark:text-red-400">unloaded</span>
                ) : (
                  <span>—</span>
                )
              }
            />
            <InvariantRow
              label="Memory consolidations failed (24h)"
              tooltip="O.2 invariant: tracks NLI / timeout / circuit-open failures in the memory consolidation path."
              status={memFails === 0 ? "ok" : memFails < 5 ? "warn" : "error"}
              value={
                <span
                  className={cn(
                    memFails > 0 && "text-amber-600 dark:text-yellow-400",
                    memFails >= 5 && "text-red-600 dark:text-red-400",
                  )}
                >
                  {memFails}
                </span>
              }
            />
            <InvariantRow
              label="Verification report orphans"
              tooltip="Verification reports missing provenance edges. m0001 backfilled history; new orphans indicate a writer regression."
              status={orphans === 0 ? "ok" : "warn"}
              value={
                <span className={cn(orphans > 0 && "text-amber-600 dark:text-yellow-400")}>
                  {orphans}
                </span>
              }
            />
            <InvariantRow
              label="Swallowed errors (last hour)"
              tooltip="Errors caught via log_swallowed_error across all modules. Spikes are usually transient infra blips; sustained nonzero values are worth investigating."
              status={swallowedTotal === 0 ? "ok" : swallowedTotal < 10 ? "warn" : "error"}
              value={
                <span className="inline-flex items-center gap-1">
                  {swallowedTotal > 0 && (
                    <AlertTriangle
                      className={cn(
                        "h-3 w-3",
                        swallowedTotal < 10 ? "text-amber-500" : "text-red-500",
                      )}
                      aria-hidden="true"
                    />
                  )}
                  {swallowedTotal}
                </span>
              }
            />
          </div>
        </CardContent>
      </Card>
    </TooltipProvider>
  )
}
