// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * RecommendationBanner — adaptive feature recommendations, hosted by the
 * settings shell at the top of the detail area (above category content).
 *
 * Data source unchanged: polls GET /health every 60s for
 * `recommended_features` (ConfigRecommenderJob). Actions:
 *
 *   1. Enable now — PATCH /settings with enable_payload, then
 *      DELETE /settings/recommendations/{id}. Failures surface as an
 *      inline destructive Alert (never silent).
 *   2. Snooze     — explicitly labeled button; sessionStorage snooze
 *                   (per-tab, until window close).
 *   3. X icon     — permanent dismiss (server-side, per-tenant).
 *
 * When a recommendation's enable_payload maps to a registry-backed control
 * (a def with `writer: { kind: "settings-patch" }` whose key is in the
 * payload), "View setting" deep-links to the owning row via the `?setting=`
 * reveal path.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Lightbulb, X } from "lucide-react"
import { useEffect, useState } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import { useNavigation } from "@/contexts/navigation-context"
import { fetchHealth } from "@/lib/api/settings"
import { clearRecommendation, dismissRecommendation } from "@/lib/api/recommendations"
import { SETTINGS_REGISTRY, type SettingDef } from "@/lib/settings-registry"
import type { PatchResult } from "./categories/page-props"
import type { RecommendedFeature, SettingsUpdate } from "@/lib/types"
import { logSwallowedError } from "@/lib/log-swallowed"

const SNOOZE_PREFIX = "cerid:recommendation-snoozed:"
const COLLAPSE_KEY = "cerid-settings-recs-collapsed"

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

/** Resolve the registry def that owns a recommendation's primary setting. */
function owningDef(rec: RecommendedFeature): SettingDef | undefined {
  const payload = (rec.enable_payload ?? {}) as Record<string, unknown>
  const keys = Object.keys(payload)
  return SETTINGS_REGISTRY.find(
    (d) => d.writer.kind === "settings-patch" && keys.includes(d.writer.key),
  )
}

export interface RecommendationBannerProps {
  /** PATCH /settings — wired in from the shell so the banner shares one source of truth. */
  patch: (body: SettingsUpdate) => Promise<PatchResult>
}

export function RecommendationBanner({ patch }: RecommendationBannerProps) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ["health-recommendations"],
    queryFn: fetchHealth,
    refetchInterval: 60_000,
    staleTime: 60_000,
    retry: 1,
  })
  const [dismissedLocally, setDismissedLocally] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!data?.recommended_features) return
    const snoozed = new Set<string>()
    for (const rec of data.recommended_features) {
      if (isSnoozed(rec.id)) snoozed.add(rec.id)
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setDismissedLocally(snoozed)
  }, [data?.recommended_features])

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1"
    } catch {
      return false
    }
  })

  const recs = (data?.recommended_features ?? []).filter(
    (r) => !dismissedLocally.has(r.id),
  )
  if (recs.length === 0) return null

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, prev ? "0" : "1")
      } catch (err) {
        logSwallowedError(err, "recommendation-banner.collapse")
      }
      return !prev
    })
  }

  return (
    <Card data-testid="recommendation-banner">
      <CardContent className="grid gap-1 pt-4">
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md text-left"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
        >
          <Lightbulb className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
          <span className="text-sm font-medium">Recommendations</span>
          <Badge variant="secondary" className="text-label-xs">{recs.length}</Badge>
          <span className="flex-1" />
          <ChevronDown
            className={cn(
              "h-4 w-4 text-muted-foreground transition-transform duration-150",
              collapsed && "-rotate-90",
            )}
            aria-hidden="true"
          />
        </button>
        {!collapsed &&
          recs.map((rec, i) => (
            <div key={rec.id} className="grid gap-1">
              {i > 0 && <Separator className="my-1.5" />}
              <RecommendationRow
                rec={rec}
                patch={patch}
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
            </div>
          ))}
      </CardContent>
    </Card>
  )
}

interface RecommendationRowProps {
  rec: RecommendedFeature
  patch: (body: SettingsUpdate) => Promise<PatchResult>
  onSnooze: () => void
  onDismiss: () => void | Promise<void>
}

function RecommendationRow({ rec, patch, onSnooze, onDismiss }: RecommendationRowProps) {
  const qc = useQueryClient()
  const { goTo } = useNavigation()
  const [enableError, setEnableError] = useState("")
  const [enabling, setEnabling] = useState(false)
  const target = owningDef(rec)

  const enable = async () => {
    setEnabling(true)
    setEnableError("")
    try {
      const result = await patch(rec.enable_payload as SettingsUpdate)
      if (!result.ok) {
        setEnableError(result.error)
        return
      }
      await clearRecommendation(rec.id)
      await qc.invalidateQueries({ queryKey: ["health-recommendations"] })
    } catch (err) {
      setEnableError(err instanceof Error ? err.message : "Failed to enable")
    } finally {
      setEnabling(false)
    }
  }

  return (
    <div className="grid gap-1.5">
      <div className="flex items-start justify-between gap-3">
        <div className="grid min-w-0 gap-0.5">
          <span className="min-w-0 text-sm font-medium">{rec.label}</span>
          <p className="text-sm leading-relaxed text-muted-foreground">{rec.reason}</p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 shrink-0 p-0"
          onClick={onDismiss}
          aria-label="Dismiss permanently"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </div>
      {enableError && (
        <Alert variant="destructive">
          <AlertDescription>Could not enable: {enableError}</AlertDescription>
        </Alert>
      )}
      <div className="flex items-center justify-end gap-2">
        {target && (
          <Button
            variant="ghost"
            size="sm"
            className="text-xs text-muted-foreground"
            onClick={() => goTo("settings", { category: target.category, setting: target.id })}
          >
            View setting
          </Button>
        )}
        <Button variant="ghost" size="sm" className="text-xs text-muted-foreground" onClick={onSnooze}>
          Snooze
        </Button>
        <Button size="sm" variant="outline" className="text-xs" onClick={enable} disabled={enabling}>
          Enable now
        </Button>
      </div>
    </div>
  )
}
