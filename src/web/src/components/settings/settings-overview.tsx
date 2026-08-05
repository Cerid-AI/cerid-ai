// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Settings Overview (ST1) — the settings landing surface. Three blocks:
 *
 *   1. Recommendations — the adaptive banner, hoisted to the top so it reads
 *      as the differentiated, act-on-me element (Lightbulb + count badge).
 *   2. Active configuration — every setting whose live value differs from its
 *      recommended default (derived from the registry, `modifiedSettings`),
 *      grouped by category. Each row deep-links to the owning control. When
 *      nothing is changed, an at-defaults state.
 *   3. Explore settings — the category map, organized into themed group
 *      cards. Every row carries a one-line explanation of what lives there,
 *      denser groups carry an InfoTip, and rows surface cheap status hints
 *      (domain count, provider state, tier) plus per-category modified /
 *      tier-lock badges. Clicking a row navigates via the pane's existing
 *      category-switch mechanism — includes the Analytics and Diagnostics
 *      console entries.
 *
 * State matrix: the pane gates on settings load (loading skeleton /
 * destructive alert) before this renders, so the overview itself is the
 * success path. Data-driven fragments degrade individually: status hints
 * render only when their backing field is present, and the active-config
 * block has an explicit at-defaults state. Layout-only composition over
 * existing primitives; no new data sources.
 */

import { useMemo } from "react"
import { Activity, BarChart3, CheckCircle2, ChevronRight, Settings, type LucideIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import {
  CATEGORY_META,
  categoryLabel,
  modifiedSettings,
  SETTINGS_REGISTRY,
  type CategoryId,
  type ModifiedSetting,
  type SettingDef,
} from "@/lib/settings-registry"
import { useEntitlements } from "@/hooks/use-entitlements"
import type { FeatureTier } from "@/lib/api/billing"
import type { ProviderCredits, ServerSettings, SettingsUpdate } from "@/lib/types"
import { FOCUS_RING, InfoTip, TierLockBadge } from "./settings-primitives"
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

/** Every destination the map can navigate to — the 8 registry categories
    plus the two console entries the sidebar also offers. */
export type OverviewTarget = CategoryId | "analytics" | "diagnostics"

/** One-line, plain-language answer to "what lives in this category?". */
const CATEGORY_BLURBS: Record<CategoryId, string> = {
  models: "Choose the chat, embedding, and reranking models Cerid runs, and optionally connect an API provider.",
  knowledge: "Ingest documents, watch folders, and organize the domains Cerid answers from.",
  retrieval: "Tune how answers are searched, ranked, and verified against your sources.",
  privacy: "Set the boundaries for encryption, retention, and what leaves this machine.",
  extensions: "Govern MCP servers, agents, and external API access.",
  appearance: "Theme, density, and motion preferences for this device.",
  plan: "See your current tier and the features it unlocks.",
  system: "Storage, sync, backup, and server maintenance operations.",
}

const CONSOLE_META: Record<"analytics" | "diagnostics", { label: string; icon: LucideIcon; blurb: string }> = {
  analytics: {
    label: "Analytics",
    icon: BarChart3,
    blurb: "Usage, cost, and answer-quality reporting.",
  },
  diagnostics: {
    label: "Diagnostics",
    icon: Activity,
    blurb: "Live health checks and agent activity consoles.",
  },
}

interface OverviewGroup {
  id: string
  label: string
  /** One-line group summary, rendered as the card description. */
  blurb: string
  /** 1–2 sentence explanation for denser concepts (InfoTip). */
  info?: string
  targets: OverviewTarget[]
}

const OVERVIEW_GROUPS: OverviewGroup[] = [
  {
    id: "models-providers",
    label: "Models & Providers",
    blurb: "The engines behind every answer.",
    info:
      "Cerid runs on local models out of the box. Connecting an API provider is optional — only query context is ever sent, never your knowledge base.",
    targets: ["models"],
  },
  {
    id: "knowledge-retrieval",
    label: "Knowledge & Retrieval",
    blurb: "What Cerid knows, and how it finds and verifies answers.",
    info:
      "Knowledge is the content Cerid draws on, organized into domains. Retrieval is the pipeline that searches it, reranks results, and verifies answers against sources.",
    targets: ["knowledge", "retrieval"],
  },
  {
    id: "privacy-data",
    label: "Privacy & Data",
    blurb: "Your data stays local by default — these controls keep it that way.",
    info:
      "Cerid is self-hosted and local-first. These settings govern encryption at rest, retention, and exactly what context is shared with any model provider you enable.",
    targets: ["privacy"],
  },
  {
    id: "connections",
    label: "Connections & Extensions",
    blurb: "Tools and services that extend what Cerid can do.",
    info:
      "MCP (Model Context Protocol) servers add tools Cerid's agents can call. Governance modes control which servers and agents are allowed to run.",
    targets: ["extensions"],
  },
  {
    id: "preferences-plan",
    label: "Preferences & Plan",
    blurb: "How Cerid looks on this device, and what your plan unlocks.",
    targets: ["appearance", "plan"],
  },
  {
    id: "system-monitoring",
    label: "System & Monitoring",
    blurb: "Operate the server and watch how it's performing.",
    info:
      "Diagnostics shows live service health; Analytics reports usage, cost, and answer quality over time.",
    targets: ["system", "analytics", "diagnostics"],
  },
]

function rowMeta(target: OverviewTarget): { label: string; icon: LucideIcon; blurb: string } {
  if (target === "analytics" || target === "diagnostics") return CONSOLE_META[target]
  const meta = CATEGORY_META.find((c) => c.id === target)
  return {
    label: meta?.label ?? target,
    icon: meta?.icon ?? Settings,
    blurb: CATEGORY_BLURBS[target],
  }
}

interface HintCtx {
  settings: ServerSettings
  credits?: ProviderCredits
  tier: FeatureTier
}

/** Status glance, derived exclusively from data the pane already loaded —
    no new API calls. Returns null when the backing field is absent. */
function rowHint(target: OverviewTarget, { settings, credits, tier }: HintCtx): string | null {
  switch (target) {
    case "models":
      if (!credits) return null
      return credits.configured ? "API provider connected" : "No API provider configured"
    case "knowledge": {
      const n = Array.isArray(settings.domains) ? settings.domains.length : 0
      return n > 0 ? `${n} domain${n === 1 ? "" : "s"}` : null
    }
    case "retrieval":
      if (typeof settings.enable_hallucination_check !== "boolean") return null
      return settings.enable_hallucination_check ? "Verification on" : "Verification off"
    case "privacy":
      if (typeof settings.enable_encryption !== "boolean") return null
      return settings.enable_encryption ? "Encryption on" : "Encryption off"
    case "extensions":
      return settings.mcp_client_mode ? `MCP ${settings.mcp_client_mode}` : null
    case "plan":
      return `${TIER_LABEL[tier] ?? tier} tier`
    case "system":
      return settings.version ? `v${settings.version}` : null
    default:
      return null
  }
}

export interface SettingsOverviewProps {
  settings: ServerSettings
  patch: (update: SettingsUpdate) => Promise<PatchResult>
  tier: FeatureTier
  credits?: ProviderCredits
  onRevealSetting: (def: SettingDef) => void
  onSelectCategory: (id: OverviewTarget) => void
}

export function SettingsOverview({
  settings,
  patch,
  tier,
  credits,
  onRevealSetting,
  onSelectCategory,
}: SettingsOverviewProps) {
  const { forDef } = useEntitlements()

  const modified = modifiedSettings(settings as unknown as Record<string, unknown>, { tier })
  const byCategory = new Map<CategoryId, ModifiedSetting[]>()
  for (const m of modified) {
    const list = byCategory.get(m.def.category) ?? []
    list.push(m)
    byCategory.set(m.def.category, list)
  }

  // Per-category tier locks (lowest required tier + count), mirroring the
  // per-row treatment in SettingRow / search results at category altitude.
  const lockedByCategory = useMemo(() => {
    const rank: Record<string, number> = { pro: 1, enterprise: 2 }
    const map = new Map<CategoryId, { requiredTier: FeatureTier; count: number }>()
    for (const def of SETTINGS_REGISTRY) {
      if (!def.entitlement) continue
      if (def.visibleWhen?.({ tier, serverSettings: settings as unknown as Record<string, unknown> }) === false) continue
      const entitlement = forDef(def)
      if (entitlement.state !== "locked" || !entitlement.requiredTier) continue
      const prev = map.get(def.category)
      if (!prev) {
        map.set(def.category, { requiredTier: entitlement.requiredTier, count: 1 })
      } else {
        map.set(def.category, {
          requiredTier:
            (rank[entitlement.requiredTier] ?? 0) < (rank[prev.requiredTier] ?? 0)
              ? entitlement.requiredTier
              : prev.requiredTier,
          count: prev.count + 1,
        })
      }
    }
    return map
  }, [forDef, settings, tier])

  const hintCtx: HintCtx = { settings, credits, tier }

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
            <InfoTip text="Settings whose value differs from the recommended default. Click a row to jump straight to the control; each row offers a reset there." />
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
                        className={cn(
                          "flex w-full items-center justify-between gap-3 rounded-md border-l-2 border-brand/60 bg-muted/30 px-2.5 py-1.5 text-left transition-colors hover:bg-muted/60",
                          FOCUS_RING,
                        )}
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

      <nav aria-label="Explore settings" className="grid gap-3 md:grid-cols-2">
        {OVERVIEW_GROUPS.map((group) => (
          <Card key={group.id} className={cn(group.targets.length > 1 && "md:col-span-2")}>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-1.5 text-sm font-medium">
                {group.label}
                {group.info && <InfoTip text={group.info} />}
              </CardTitle>
              <CardDescription className="text-label-sm">{group.blurb}</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className={cn("grid list-none gap-1 p-0", group.targets.length > 1 && "sm:grid-cols-2")}>
                {group.targets.map((target) => {
                  const { label, icon: Icon, blurb } = rowMeta(target)
                  const hint = rowHint(target, hintCtx)
                  const locked =
                    target === "analytics" || target === "diagnostics"
                      ? undefined
                      : lockedByCategory.get(target)
                  const modCount =
                    target === "analytics" || target === "diagnostics"
                      ? 0
                      : (byCategory.get(target)?.length ?? 0)
                  return (
                    <li key={target}>
                      <button
                        type="button"
                        data-testid={`settings-overview-${target}`}
                        onClick={() => onSelectCategory(target)}
                        className={cn(
                          "flex w-full items-start gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-muted/60",
                          FOCUS_RING,
                        )}
                      >
                        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="text-sm font-medium">{label}</span>
                            {locked && (
                              <TierLockBadge requiredTier={locked.requiredTier} count={locked.count} />
                            )}
                            {modCount > 0 && (
                              <Badge variant="outline" className="gap-1 border-brand/50 text-label-xs text-brand">
                                {modCount} modified
                              </Badge>
                            )}
                          </span>
                          <span className="block text-label-sm leading-snug text-muted-foreground">
                            {blurb}
                          </span>
                        </span>
                        {hint && (
                          <span className="mt-0.5 shrink-0 text-label-xs text-muted-foreground tabular-nums">
                            {hint}
                          </span>
                        )}
                        <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/60" aria-hidden="true" />
                      </button>
                    </li>
                  )
                })}
              </ul>
            </CardContent>
          </Card>
        ))}
      </nav>
    </div>
  )
}
