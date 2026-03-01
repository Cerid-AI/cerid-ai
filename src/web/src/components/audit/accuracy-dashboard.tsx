// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"
import { ModelAccuracyChart } from "./model-accuracy-chart"
import type { AuditVerification } from "@/lib/types"

interface AccuracyDashboardProps {
  verification: AuditVerification | undefined
}

function accuracyColor(accuracy: number): string {
  if (accuracy >= 0.8) return "text-green-400"
  if (accuracy >= 0.5) return "text-yellow-400"
  return "text-red-400"
}

function accuracyBarColor(accuracy: number): string {
  if (accuracy >= 0.8) return "bg-green-500"
  if (accuracy >= 0.5) return "bg-yellow-500"
  return "bg-red-500"
}

export function AccuracyDashboard({ verification }: AccuracyDashboardProps) {
  if (!verification || verification.total_checks === 0) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="No verification data"
        description="Stats appear after response verification runs"
      />
    )
  }

  const modelData = Object.entries(verification.by_model)
    .sort((a, b) => b[1].checks - a[1].checks)

  return (
    <>
    <Card>
      <CardHeader className="p-3 pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Verification Accuracy</CardTitle>
          <span className="text-xs text-muted-foreground">
            {verification.total_checks} checks
          </span>
        </div>
      </CardHeader>
      <CardContent className="p-3">
        {/* Overall accuracy */}
        <div className="mb-3 flex items-center gap-3">
          <span className="text-xs text-muted-foreground">Overall:</span>
          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-all",
                accuracyBarColor(verification.avg_accuracy),
              )}
              style={{ width: `${Math.round(verification.avg_accuracy * 100)}%` }}
            />
          </div>
          <span className={cn("text-xs font-medium tabular-nums", accuracyColor(verification.avg_accuracy))}>
            {Math.round(verification.avg_accuracy * 100)}%
          </span>
        </div>

        {/* Per-model breakdown */}
        {modelData.length > 0 ? (
          <div className="space-y-2">
            {modelData.map(([model, stats]) => (
              <div key={model} className="rounded-lg border p-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium">{model}</span>
                  <span className={cn("text-xs font-medium tabular-nums", accuracyColor(stats.accuracy))}>
                    {Math.round(stats.accuracy * 100)}%
                  </span>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        accuracyBarColor(stats.accuracy),
                      )}
                      style={{ width: `${Math.round(stats.accuracy * 100)}%` }}
                    />
                  </div>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                  <span>{stats.checks} checks</span>
                  <span className="text-green-400">{stats.verified} verified</span>
                  {stats.uncertain > 0 && (
                    <span className="text-yellow-400">{stats.uncertain} uncertain</span>
                  )}
                  {stats.unverified > 0 && (
                    <span className="text-red-400">{stats.unverified} unverified</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-4 text-center text-xs text-muted-foreground">No model data</p>
        )}
      </CardContent>
    </Card>

    {/* Model comparison chart — shown when 2+ models have data */}
    <ModelAccuracyChart verification={verification} />
    </>
  )
}
