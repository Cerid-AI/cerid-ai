// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings Overview (ST1) — the settings landing surface. Three blocks:
 *
 *   1. Recommendations — the adaptive banner, hoisted to the top so it reads
 *      as the differentiated, act-on-me element (Lightbulb + count badge).
 *   2. Active configuration — every setting whose live value differs from its
 *      recommended default (derived from the registry, `modifiedSettings`),
 *      grouped by category. Each row deep-links to the owning control. When
 *      nothing is changed, an at-defaults empty state.
 *   3. Jump to a category — the 8 category cards, for fast navigation.
 *
 * Layout-only composition over existing primitives; no new data sources.
 */

import { CheckCircle2, ChevronRight } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import {
  CATEGORY_META,
  categoryLabel,
  modifiedSettings,
  type CategoryId,
  type ModifiedSetting,
  type SettingDef,
} from "@/lib/settings-registry"
import type { FeatureTier } from "@/lib/api/billing"
import type { ServerSettings, SettingsUpdate } from "@/lib/types"
import { RecommendationBanner } from "./recommendation-banner"
import type { PatchResult } from "./categories/page-props"

function formatValue(def: SettingDef, value: unknown): string {
  if (value === undefined || value === null) return "—"
  if (def.type === "boolean") return value ? "On" : "Off"
  const opt = def.options?.find((o) => o.value === value)
  if (opt) return opt.label
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2)
  return String(value)
}

const TIER_LABEL: Record<string, string> = {
  community: "Community",
  pro: "Pro",
  enterprise: "Enterprise",
}

export interface SettingsOverviewProps {
  settings: ServerSettings
  patch: (update: SettingsUpdate) => Promise<PatchResult>
  tier: FeatureTier
  onRevealSetting: (def: SettingDef) => void
  onSelectCategory: (id: CategoryId) => void
}

export function SettingsOverview({
  settings,
  patch,
  tier,
  onRevealSetting,
  onSelectCategory,
}: SettingsOverviewProps) {
  const modified = modifiedSettings(settings as unknown as Record<string, unknown>, { tier })
  const byCategory = new Map<CategoryId, ModifiedSetting[]>()
  for (const m of modified) {
    const list = byCategory.get(m.def.category) ?? []
    list.push(m)
    byCategory.set(m.def.category, list)
  }

  return (
    <div className="density-stack">
      <RecommendationBanner patch={patch} />

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            Active configuration
            {modified.length > 0 && (
              <Badge variant="secondary" className="text-label-xs">{modified.length}</Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <section aria-label="Active configuration">
            {modified.length === 0 ? (
              <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
                Everything is at its recommended defaults.
              </div>
            ) : (
              <div className="space-y-3">
                {[...byCategory.entries()].map(([category, items]) => (
                  <div key={category} className="grid gap-1">
                    <p className="text-label-xs font-medium uppercase tracking-wide text-muted-foreground/80">
                      {categoryLabel(category)}
                    </p>
                    {items.map(({ def, current, default: dflt }) => (
                      <button
                        key={def.id}
                        type="button"
                        onClick={() => onRevealSetting(def)}
                        className="flex w-full items-center justify-between gap-3 rounded-md border-l-2 border-brand/60 bg-muted/30 px-2.5 py-1.5 text-left transition-colors hover:bg-muted/60"
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">{def.label}</span>
                          <span className="block text-label-xs text-muted-foreground">
                            {formatValue(def, current)}
                            <span className="text-muted-foreground/60"> · default {formatValue(def, dflt)}</span>
                          </span>
                        </span>
                        <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/60" aria-hidden="true" />
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </section>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex items-center justify-between gap-3 py-3">
          <div className="min-w-0">
            <p className="text-sm font-medium">Plan</p>
            <p className="text-label-xs text-muted-foreground">{TIER_LABEL[tier] ?? tier} tier</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => onSelectCategory("plan")}>
            Manage plan
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Jump to a category</CardTitle>
        </CardHeader>
        <CardContent>
          <nav aria-label="Jump to a category" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {CATEGORY_META.map(({ id, label, description, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => onSelectCategory(id)}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors hover:border-primary/40 hover:bg-muted/50",
                )}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="text-sm font-medium">{label}</span>
                </span>
                <span className="text-label-xs text-muted-foreground">{description}</span>
              </button>
            ))}
          </nav>
        </CardContent>
      </Card>
    </div>
  )
}
