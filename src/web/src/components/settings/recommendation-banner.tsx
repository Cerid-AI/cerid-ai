// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RecommendationBanner — surfaces adaptive feature recommendations at the
 * top of the Settings pane (Cycle 3.2 / v0.93.3).
 *
 * Polls GET /health every 60s for the `recommended_features` array
 * populated by the ConfigRecommenderJob. Each entry renders as a
 * dismissable card with three actions:
 *
 *   1. Enable now              — PATCH /settings with enable_payload,
 *                                then DELETE /settings/recommendations/{id}
 *                                so the banner closes immediately.
 *   2. Maybe later             — sessionStorage snooze (per-tab, until
 *                                window close).
 *   3. Dismiss permanently     — POST /settings/recommendations/{id}/dismiss
 *                                (server-side, per-tenant).
 *
 * Amber border keeps the visual vocabulary aligned with the "Custom"
 * preset badge elsewhere in the Settings pane.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Sparkles, X } from "lucide-react"
import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { fetchHealth } from "@/lib/api/settings"
import { clearRecommendation, dismissRecommendation } from "@/lib/api/recommendations"
import type { RecommendedFeature, SettingsUpdate } from "@/lib/types"
import { logSwallowedError } from "@/lib/log-swallowed"

const SNOOZE_PREFIX = "cerid:recommendation-snoozed:"

function isSnoozed(id: string): boolean {
  try {
    return sessionStorage.getItem(SNOOZE_PREFIX + id) === "1"
  } catch {
    return false
  }
}

function snooze(id: string): void {
  try {
    sessionStorage.setItem(SNOOZE_PREFIX + id, "1")
  } catch (err) {
    logSwallowedError(err, "recommendation-banner.snooze")
  }
}

export interface RecommendationBannerProps {
  /** PATCH /settings — wired in from the parent so the banner shares one source of truth. */
  patch: (body: SettingsUpdate) => Promise<unknown>
}

export function RecommendationBanner({ patch }: RecommendationBannerProps) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ["health-recommendations"],
    queryFn: fetchHealth,
    // 60s poll matches GitHub's notification cadence — fresh enough to
    // matter, slow enough to be invisible.
    refetchInterval: 60_000,
    staleTime: 60_000,
    retry: 1,
  })
  const [dismissedLocally, setDismissedLocally] = useState<Set<string>>(new Set())

  // Refresh dismissedLocally when sessionStorage state changes — e.g.
  // user clicked "Maybe later" then the banner re-renders and should
  // suppress that entry until the tab is closed.
  useEffect(() => {
    if (!data?.recommended_features) return
    const snoozed = new Set<string>()
    for (const rec of data.recommended_features) {
      if (isSnoozed(rec.id)) snoozed.add(rec.id)
    }
    setDismissedLocally(snoozed)
  }, [data?.recommended_features])

  const recs = (data?.recommended_features ?? []).filter(
    (r) => !dismissedLocally.has(r.id),
  )
  if (recs.length === 0) return null

  return (
    <div className="mb-4 grid gap-2">
      {recs.map((rec) => (
        <RecommendationCard
          key={rec.id}
          rec={rec}
          onEnable={async () => {
            try {
              await patch(rec.enable_payload as SettingsUpdate)
              await clearRecommendation(rec.id)
              await qc.invalidateQueries({ queryKey: ["health-recommendations"] })
            } catch (err) {
              logSwallowedError(err, "recommendation-banner.enable")
            }
          }}
          onSnooze={() => {
            snooze(rec.id)
            setDismissedLocally((prev) => new Set(prev).add(rec.id))
          }}
          onDismiss={async () => {
            try {
              await dismissRecommendation(rec.id)
              await qc.invalidateQueries({ queryKey: ["health-recommendations"] })
            } catch (err) {
              logSwallowedError(err, "recommendation-banner.dismiss")
            }
          }}
        />
      ))}
    </div>
  )
}

interface RecommendationCardProps {
  rec: RecommendedFeature
  onEnable: () => void | Promise<void>
  onSnooze: () => void
  onDismiss: () => void | Promise<void>
}

function RecommendationCard({ rec, onEnable, onSnooze, onDismiss }: RecommendationCardProps) {
  return (
    <Card className="border-amber-500/40 bg-amber-50/30 dark:bg-amber-950/20">
      <CardContent className="grid gap-3 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <Sparkles className="mt-0.5 h-4 w-4 text-amber-600 dark:text-amber-400" />
            <div className="grid gap-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">{rec.label}</span>
                <Badge
                  variant="outline"
                  className="border-amber-500/40 bg-amber-100/50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
                >
                  Recommended
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">{rec.reason}</p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={onSnooze}
            aria-label="Snooze for this session"
            title="Maybe later (snoozed for this tab)"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onDismiss}
            className="text-xs text-muted-foreground"
          >
            Dismiss permanently
          </Button>
          <Button size="sm" onClick={onEnable} className="text-xs">
            Enable now
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
