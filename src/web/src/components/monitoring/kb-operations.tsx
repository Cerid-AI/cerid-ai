// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Loader2, ShieldCheck, Wrench, Award, AlertTriangle, CheckCircle2 } from "lucide-react"
import { fetchRectify, fetchMaintenance, fetchCurate } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { RectifyResponse, MaintenanceResponse, CurateResponse } from "@/lib/types"

export function KBOperations() {
  const [rectifyResult, setRectifyResult] = useState<RectifyResponse | null>(null)
  const [maintenanceResult, setMaintenanceResult] = useState<MaintenanceResponse | null>(null)
  const [curateResult, setCurateResult] = useState<CurateResponse | null>(null)
  const [generateSynopses, setGenerateSynopses] = useState(false)

  const rectify = useMutation({
    mutationFn: () => fetchRectify(["duplicates", "stale", "orphans", "distribution"]),
    onSuccess: setRectifyResult,
  })

  const maintain = useMutation({
    mutationFn: (autoPurge: boolean) =>
      fetchMaintenance(["health", "collections", "stale", "orphans"], {
        auto_purge: autoPurge,
      }),
    onSuccess: setMaintenanceResult,
  })

  const curate = useMutation({
    mutationFn: () => fetchCurate(undefined, 200, generateSynopses),
    onSuccess: setCurateResult,
  })

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-muted-foreground">KB Operations</h3>

      {/* Rectification Agent */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-2">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm">Health Check</CardTitle>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => rectify.mutate()}
            disabled={rectify.isPending}
          >
            {rectify.isPending ? (
              <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Running...</>
            ) : (
              "Run Check"
            )}
          </Button>
        </CardHeader>
        <CardContent className="p-3 pt-0">
          {rectify.isError && (
            <p className="text-xs text-destructive">
              {rectify.error instanceof Error ? rectify.error.message : "Check failed"}
            </p>
          )}
          {rectifyResult && <RectifyResults result={rectifyResult} />}
          {!rectifyResult && !rectify.isPending && !rectify.isError && (
            <p className="text-xs text-muted-foreground">
              Checks for duplicates, stale artifacts, orphaned chunks, and domain distribution.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Maintenance Agent */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-2">
          <div className="flex items-center gap-2">
            <Wrench className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm">Maintenance</CardTitle>
          </div>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => maintain.mutate(false)}
              disabled={maintain.isPending}
            >
              {maintain.isPending ? (
                <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Running...</>
              ) : (
                "Scan"
              )}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs text-destructive hover:text-destructive"
              onClick={() => maintain.mutate(true)}
              disabled={maintain.isPending}
            >
              Scan & Clean
            </Button>
          </div>
        </CardHeader>
        <CardContent className="p-3 pt-0">
          {maintain.isError && (
            <p className="text-xs text-destructive">
              {maintain.error instanceof Error ? maintain.error.message : "Maintenance failed"}
            </p>
          )}
          {maintenanceResult && <MaintenanceResults result={maintenanceResult} />}
          {!maintenanceResult && !maintain.isPending && !maintain.isError && (
            <p className="text-xs text-muted-foreground">
              Scan detects stale artifacts and orphaned chunks. Clean removes them automatically.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Quality Audit */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 p-3 pb-2">
          <div className="flex items-center gap-2">
            <Award className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm">Quality Audit</CardTitle>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => curate.mutate()}
            disabled={curate.isPending}
          >
            {curate.isPending ? (
              <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Scoring...</>
            ) : (
              "Run Audit"
            )}
          </Button>
        </CardHeader>
        <CardContent className="p-3 pt-0">
          <label className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={generateSynopses}
              onChange={(e) => setGenerateSynopses(e.target.checked)}
              className="h-3 w-3 rounded border-muted-foreground/40"
            />
            Generate AI synopses for artifacts with raw summaries
          </label>
          {curate.isError && (
            <p className="text-xs text-destructive">
              {curate.error instanceof Error ? curate.error.message : "Quality audit failed"}
            </p>
          )}
          {curateResult && <CurationResults result={curateResult} />}
          {!curateResult && !curate.isPending && !curate.isError && (
            <p className="text-xs text-muted-foreground">
              Score all artifacts on quality dimensions: summary, keywords, freshness, completeness.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RectifyResults({ result }: { result: RectifyResponse }) {
  const { findings, actions } = result
  const hasIssues =
    (findings.duplicates?.count ?? 0) > 0 ||
    (findings.stale?.count ?? 0) > 0 ||
    (findings.orphans?.count ?? 0) > 0

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <FindingBadge
          label="Duplicates"
          count={findings.duplicates?.count ?? 0}
        />
        <FindingBadge
          label="Stale"
          count={findings.stale?.count ?? 0}
          suffix={findings.stale ? `>${findings.stale.threshold_days}d` : undefined}
        />
        <FindingBadge
          label="Orphans"
          count={findings.orphans?.count ?? 0}
        />
      </div>
      {findings.distribution && (
        <p className="text-xs text-muted-foreground">
          {findings.distribution.total_artifacts} artifacts, {findings.distribution.total_chunks} chunks across {findings.distribution.domain_count} domains
        </p>
      )}
      {!hasIssues && (
        <div className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5" />
          Knowledge base is healthy
        </div>
      )}
      {actions.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {actions.length} auto-fix action{actions.length > 1 ? "s" : ""} applied
        </p>
      )}
    </div>
  )
}

function MaintenanceResults({ result }: { result: MaintenanceResponse }) {
  const staleCount = result.stale_artifacts?.length ?? 0
  const orphanCount = result.orphan_cleanup?.orphaned_chunks ?? 0
  const cleanedCount = result.orphan_cleanup?.cleaned ?? 0
  const hasIssues = staleCount > 0 || orphanCount > 0

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <FindingBadge label="Stale" count={staleCount} />
        <FindingBadge label="Orphans" count={orphanCount} />
      </div>
      {cleanedCount > 0 && (
        <p className="text-xs text-muted-foreground">
          Cleaned {cleanedCount} orphaned chunk{cleanedCount > 1 ? "s" : ""}
        </p>
      )}
      {!hasIssues && (
        <div className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5" />
          No maintenance issues found
        </div>
      )}
    </div>
  )
}

function FindingBadge({ label, count, suffix }: { label: string; count: number; suffix?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-xs",
        count > 0
          ? "border-yellow-500/50 text-yellow-700 dark:text-yellow-400"
          : "border-green-500/50 text-green-700 dark:text-green-400",
      )}
    >
      {count > 0 ? (
        <AlertTriangle className="mr-1 h-3 w-3" />
      ) : (
        <CheckCircle2 className="mr-1 h-3 w-3" />
      )}
      {count} {label}
      {suffix && <span className="ml-1 opacity-70">({suffix})</span>}
    </Badge>
  )
}

function CurationResults({ result }: { result: CurateResponse }) {
  const dist = result.score_distribution
  const avgPct = Math.round(result.avg_quality_score * 100)

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Scored {result.artifacts_scored} artifacts — avg quality:{" "}
        <span className="font-medium">{avgPct}%</span>
        {result.synopses_generated > 0 && (
          <> — {result.synopses_generated} synopsis{result.synopses_generated !== 1 ? "es" : ""} generated</>
        )}
      </p>
      <div className="grid grid-cols-4 gap-1 text-center">
        {([
          ["excellent", "text-green-600 dark:text-green-400"],
          ["good", "text-blue-600 dark:text-blue-400"],
          ["fair", "text-yellow-600 dark:text-yellow-400"],
          ["poor", "text-red-600 dark:text-red-400"],
        ] as const).map(([tier, color]) => (
          <div key={tier}>
            <div className={cn("text-sm font-medium tabular-nums", color)}>
              {dist[tier] ?? 0}
            </div>
            <div className="text-[10px] text-muted-foreground capitalize">{tier}</div>
          </div>
        ))}
      </div>
      {result.low_quality_artifacts.length > 0 && (
        <div className="border-t pt-2">
          <p className="text-[10px] font-medium text-muted-foreground mb-1">
            Low quality ({result.low_quality_artifacts.length}):
          </p>
          <div className="space-y-0.5">
            {result.low_quality_artifacts.slice(0, 5).map((art) => (
              <div key={art.artifact_id} className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <span className="min-w-0 truncate">{art.filename}</span>
                <span className="ml-auto shrink-0 tabular-nums text-red-600 dark:text-red-400">
                  {Math.round(art.quality_score * 100)}%
                </span>
              </div>
            ))}
            {result.low_quality_artifacts.length > 5 && (
              <p className="text-[9px] text-muted-foreground">
                +{result.low_quality_artifacts.length - 5} more
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
