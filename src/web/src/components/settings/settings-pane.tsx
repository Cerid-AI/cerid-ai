// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings shell (SEXTANT) — master–detail inside the settings pane.
 *
 * Left sidebar: pinned search, 8 intent categories, separator, Diagnostics
 * console entry (preserves the `?diagnostics_tab=` contract). Right detail:
 * ONE scroll container per category page (Diagnostics is the lone documented
 * full-height exception). The U-1 Simple | Advanced header toggle is
 * consumed ONLY by `AdvancedDisclosure` default state.
 *
 * Deep links: `?category=`, `?setting=` (reveal + force-open + scroll),
 * `?settings_q=` (search), `?diagnostics_tab=`. Old `cerid-settings-tab`
 * values are read once through a one-release redirect map (J-4).
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchSettings, updateSettings, fetchProviderCredits } from "@/lib/api"
import type { ServerSettings, SettingsUpdate, ProviderCredits } from "@/lib/types"
import type { FeatureTier } from "@/lib/api/billing"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { SegmentedControl } from "@/components/ui/segmented-control"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Activity, BarChart3, ChevronLeft, LayoutDashboard, RefreshCw, Settings, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavigation } from "@/contexts/navigation-context"
import {
  CATEGORY_META,
  getDef,
  modifiedSettingIds,
  searchSettings,
  SETTINGS_REGISTRY,
  type CategoryId,
  type SettingDef,
} from "@/lib/settings-registry"
import { setSettingsMode, useSettingsMode, type SettingsMode } from "@/lib/settings-mode"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { logSwallowedError } from "@/lib/log-swallowed"
import { DiagnosticsSection, type DiagnosticsSubTab } from "./diagnostics-section"
import { AnalyticsSection } from "./analytics-section"
import { SettingsOverview } from "./settings-overview"
import { RecommendationBanner } from "./recommendation-banner"
import { SettingsRevealProvider, type RevealTarget } from "./reveal-context"
import { ModifiedSettingsProvider } from "./modified-context"
import { SettingsSearchInput, SettingsSearchResults } from "./settings-search"
import type { PatchResult, SettingsCategoryPageProps } from "./categories/page-props"
import AppearanceCategory from "./categories/appearance"
import ModelsCategory from "./categories/models"
import KnowledgeCategory from "./categories/knowledge"
import RetrievalAnswersCategory from "./categories/retrieval-answers"
import PrivacyCategory from "./categories/privacy"
import ExtensionsCategory from "./categories/extensions"
import PlanBillingCategory from "./categories/plan-billing"
import SystemCategory from "./categories/system"

type LoadState = "loading" | "error" | "ready"
type Selected = CategoryId | "diagnostics" | "overview" | "analytics"

const CATEGORY_KEY = "cerid-settings-category"
const LEGACY_TAB_KEY = "cerid-settings-tab"

/** One-release read-only redirect map from the old tab values (J-4). */
const LEGACY_TAB_REDIRECT: Record<string, Selected> = {
  essentials: "models",
  pipeline: "retrieval",
  system: "system",
  governance: "extensions",
  plugins: "extensions",
  diagnostics: "diagnostics",
  pro: "plan",
}

const CATEGORY_PAGES: Record<CategoryId, ComponentType<SettingsCategoryPageProps>> = {
  models: ModelsCategory,
  knowledge: KnowledgeCategory,
  retrieval: RetrievalAnswersCategory,
  privacy: PrivacyCategory,
  extensions: ExtensionsCategory,
  appearance: AppearanceCategory,
  plan: PlanBillingCategory,
  system: SystemCategory,
}

function isSelected(value: string | null): value is Selected {
  return (
    value === "diagnostics" ||
    value === "overview" ||
    value === "analytics" ||
    CATEGORY_META.some((c) => c.id === value)
  )
}

function readUrlParam(key: string): string | null {
  if (typeof window === "undefined") return null
  return new URLSearchParams(window.location.search).get(key)
}

function writeUrlParam(key: string, value: string | null) {
  if (typeof window === "undefined") return
  const params = new URLSearchParams(window.location.search)
  if (value) params.set(key, value)
  else params.delete(key)
  const next = params.toString()
  window.history.replaceState(
    {},
    "",
    `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`,
  )
}

function initialCategory(): Selected {
  // Legacy cross-pane contract: a diagnostics deep link lands on the
  // Diagnostics console entry — except `analytics`, which is now its own
  // top-level section (ST9). Old `?diagnostics_tab=analytics` bookmarks route
  // there too.
  const diagnosticsTab = readUrlParam("diagnostics_tab")
  if (diagnosticsTab === "analytics") return "analytics"
  if (diagnosticsTab) return "diagnostics"
  const fromUrl = readUrlParam("category")
  if (isSelected(fromUrl)) return fromUrl
  const fromSetting = readUrlParam("setting")
  if (fromSetting) {
    const def = getDef(fromSetting)
    if (def) return def.category
  }
  try {
    const stored = localStorage.getItem(CATEGORY_KEY)
    if (isSelected(stored)) return stored
    const legacy = localStorage.getItem(LEGACY_TAB_KEY)
    if (legacy && LEGACY_TAB_REDIRECT[legacy]) return LEGACY_TAB_REDIRECT[legacy]
  } catch (err) {
    logSwallowedError(err, "localStorage.getItem", { key: CATEGORY_KEY })
  }
  return "overview"
}

export default function SettingsPane() {
  const queryClient = useQueryClient()
  const navigation = useNavigation()
  const mode = useSettingsMode()
  const [settings, setSettings] = useState<ServerSettings | null>(null)
  const [loadState, setLoadState] = useState<LoadState>("loading")
  const [error, setError] = useState("")
  const [patchError, setPatchError] = useState("")
  const [selected, setSelected] = useState<Selected>(initialCategory)
  const [mobileDetail, setMobileDetail] = useState(() => readUrlParam("setting") !== null)
  const [searchInput, setSearchInput] = useState(() => readUrlParam("settings_q") ?? "")
  const [query, setQuery] = useState(searchInput)
  const [reveal, setReveal] = useState<RevealTarget | null>(null)
  const nonceRef = useRef(0)
  const searchInputRef = useRef<HTMLInputElement>(null)

  const { data: credits } = useQuery<ProviderCredits>({
    queryKey: ["provider-credits"],
    queryFn: fetchProviderCredits,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const load = useCallback(async () => {
    setLoadState("loading")
    setError("")
    try {
      const data = await fetchSettings()
      setSettings(data)
      setLoadState("ready")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch settings")
      setLoadState("error")
    }
  }, [])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
  useEffect(() => { load() }, [load])

  const patch = useCallback(async (update: SettingsUpdate): Promise<PatchResult> => {
    setSettings((prev) => {
      if (!prev) return prev
      return { ...prev, ...update } as ServerSettings
    })
    setPatchError("")
    try {
      await updateSettings(update)
      return { ok: true }
    } catch (e) {
      await load()
      const message = e instanceof Error ? e.message : "Failed to save"
      setPatchError(message)
      return { ok: false, error: message }
    }
  }, [load])

  const resetSetting = useCallback(
    (def: SettingDef) => {
      if (def.writer.kind !== "settings-patch" || def.default === undefined) return
      void patch({ [def.writer.key]: def.default } as SettingsUpdate)
    },
    [patch],
  )

  const modifiedValue = useMemo(() => {
    const tierForCtx = (settings?.feature_tier as FeatureTier) ?? "community"
    return {
      ids: modifiedSettingIds(settings as unknown as Record<string, unknown> | null, { tier: tierForCtx }),
      reset: resetSetting,
    }
  }, [settings, resetSetting])

  const selectCategory = useCallback((next: Selected) => {
    setSelected(next)
    setMobileDetail(true)
    try {
      localStorage.setItem(CATEGORY_KEY, next)
    } catch (err) {
      logSwallowedError(err, "localStorage.setItem", { key: CATEGORY_KEY })
    }
  }, [])

  const revealSetting = useCallback(
    (def: SettingDef) => {
      selectCategory(def.category)
      setSearchInput("")
      setQuery("")
      writeUrlParam("settings_q", null)
      writeUrlParam("setting", def.id)
      nonceRef.current += 1
      setReveal({ id: def.id, nonce: nonceRef.current })
    },
    [selectCategory],
  )

  // Debounced search (200ms) + ?settings_q= reflection.
  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(searchInput)
      writeUrlParam("settings_q", searchInput || null)
    }, 200)
    return () => clearTimeout(t)
  }, [searchInput])

  // "/" focuses search (ignored while typing in an input/textarea/editable).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return
      e.preventDefault()
      searchInputRef.current?.focus()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  // Initial ?setting= reveal once settings are ready (rows must be mounted).
  useEffect(() => {
    if (loadState !== "ready") return
    const id = readUrlParam("setting")
    if (!id) return
    const def = getDef(id)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (URL / navigation / reveal subscription); behavior validated in tests
    if (def) revealSetting(def)
  }, [loadState, revealSetting])

  // Same-pane goTo("settings", { category, setting }) consumption — mirrors
  // the subjects-pane navVersion pattern.
  useEffect(() => {
    if (navigation.navVersion === 0) return
    const settingId = readUrlParam("setting")
    if (settingId) {
      const def = getDef(settingId)
      if (def) {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (URL / navigation / reveal subscription); behavior validated in tests
        revealSetting(def)
        return
      }
    }
    const category = readUrlParam("category")
     
    if (isSelected(category)) selectCategory(category)
  }, [navigation.navVersion, revealSetting, selectCategory])

  const tier: FeatureTier = (settings?.feature_tier as FeatureTier) ?? "community"
  const searching = query.trim().length > 0
  const matches = searching
    ? searchSettings(SETTINGS_REGISTRY, query, {
        tier,
        serverSettings: settings as Record<string, unknown> | null,
      })
    : []

  if (loadState === "loading") {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <Header mode={mode} />
        <div className="density-stack flex-1 p-4" aria-busy="true" data-testid="settings-loading">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <CardContent className="space-y-3 pt-4">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  if (loadState === "error" || !settings) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <Header mode={mode} />
        <div className="p-4">
          <Alert variant="destructive">
            <AlertDescription className="flex items-center justify-between gap-3">
              <span>{error || "Failed to load settings"}</span>
              <Button variant="outline" size="sm" onClick={load}>
                <RefreshCw className="mr-2 h-3 w-3" aria-hidden="true" />
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      </div>
    )
  }

  const isConsole =
    selected === "diagnostics" || selected === "overview" || selected === "analytics"
  const meta = isConsole ? null : CATEGORY_META.find((c) => c.id === selected)
  const Page =
    selected === "diagnostics" || selected === "overview" || selected === "analytics"
      ? null
      : CATEGORY_PAGES[selected]

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header mode={mode} />
      {patchError && (
        <Alert variant="destructive" className="rounded-none border-x-0">
          <AlertDescription className="flex items-center justify-between gap-3">
            <span>Save failed: {patchError}</span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0"
              onClick={() => setPatchError("")}
              aria-label="Dismiss save error"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <TooltipProvider delayDuration={300}>
        <div className="flex min-h-0 flex-1">
          {/* Sidebar — full-width category list on mobile (two-step list-push) */}
          <nav
            aria-label="Settings categories"
            className={cn(
              "w-full shrink-0 flex-col gap-1 overflow-y-auto border-r p-3 lg:flex lg:w-60",
              mobileDetail ? "hidden" : "flex",
            )}
          >
            <div className="pb-2">
              <SettingsSearchInput value={searchInput} onChange={setSearchInput} inputRef={searchInputRef} />
            </div>
            <button
              type="button"
              aria-current={!searching && selected === "overview" ? "page" : undefined}
              onClick={() => selectCategory("overview")}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/60",
                !searching && selected === "overview"
                  ? "bg-muted font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <LayoutDashboard className="h-4 w-4 shrink-0" aria-hidden="true" />
              Overview
            </button>
            <Separator className="my-2" />
            {CATEGORY_META.map(({ id, label, description, icon: Icon }) => (
              <button
                key={id}
                type="button"
                aria-current={!searching && selected === id ? "page" : undefined}
                onClick={() => selectCategory(id)}
                className={cn(
                  "flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/60",
                  !searching && selected === id
                    ? "bg-muted font-medium"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="min-w-0">
                  <span className="block">{label}</span>
                  <span className="block text-label-xs text-muted-foreground lg:hidden">
                    {description}
                  </span>
                </span>
              </button>
            ))}
            <Separator className="my-2" />
            <button
              type="button"
              aria-current={!searching && selected === "analytics" ? "page" : undefined}
              onClick={() => selectCategory("analytics")}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/60",
                !searching && selected === "analytics"
                  ? "bg-muted font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <BarChart3 className="h-4 w-4 shrink-0" aria-hidden="true" />
              Analytics
            </button>
            <button
              type="button"
              aria-current={!searching && selected === "diagnostics" ? "page" : undefined}
              onClick={() => selectCategory("diagnostics")}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/60",
                !searching && selected === "diagnostics"
                  ? "bg-muted font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Activity className="h-4 w-4 shrink-0" aria-hidden="true" />
              Diagnostics
            </button>
          </nav>

          {/* Detail — ONE scroll container; Diagnostics is the documented
              full-height exception (it manages its own internal scroll). */}
          <div
            className={cn(
              "min-w-0 flex-1 flex-col lg:flex",
              mobileDetail ? "flex" : "hidden",
            )}
          >
            <SettingsRevealProvider target={reveal}>
             <ModifiedSettingsProvider value={modifiedValue}>
              {searching ? (
                <div className="min-h-0 flex-1 overflow-y-auto p-4">
                  <SettingsSearchResults
                    query={query}
                    matches={matches}
                    onSelect={revealSetting}
                    onClear={() => {
                      setSearchInput("")
                      setQuery("")
                      writeUrlParam("settings_q", null)
                    }}
                  />
                </div>
              ) : selected === "overview" ? (
                <div className="min-h-0 flex-1 overflow-y-auto p-4" data-density-scope="settings">
                  <DetailHeading
                    title="Overview"
                    description="A snapshot of your configuration"
                    onBack={() => setMobileDetail(false)}
                  />
                  <PaneErrorBoundary label="Overview" queryClient={queryClient}>
                    <SettingsOverview
                      settings={settings}
                      patch={patch}
                      tier={tier}
                      onRevealSetting={revealSetting}
                      onSelectCategory={selectCategory}
                    />
                  </PaneErrorBoundary>
                </div>
              ) : selected === "analytics" ? (
                <div className="min-h-0 flex-1 overflow-y-auto p-4">
                  <DetailHeading
                    title="Analytics"
                    description="Usage, cost, and answer-quality reporting"
                    onBack={() => setMobileDetail(false)}
                  />
                  <PaneErrorBoundary label="Analytics" queryClient={queryClient}>
                    <AnalyticsSection tier={tier} />
                  </PaneErrorBoundary>
                </div>
              ) : selected === "diagnostics" ? (
                <div className="flex min-h-0 flex-1 flex-col p-4">
                  <DetailHeading
                    title="Diagnostics"
                    description="Health status and agent activity consoles"
                    onBack={() => setMobileDetail(false)}
                  />
                  <div className="min-h-0 flex-1">
                    <PaneErrorBoundary label="Diagnostics" queryClient={queryClient}>
                      <DiagnosticsSection
                        tier={tier}
                        initialTab={(readUrlParam("diagnostics_tab") as DiagnosticsSubTab | null) ?? "status"}
                        onTabChange={(sub) => {
                          writeUrlParam("diagnostics_tab", sub === "status" ? null : sub)
                        }}
                      />
                    </PaneErrorBoundary>
                  </div>
                </div>
              ) : (
                <div className="min-h-0 flex-1 overflow-y-auto p-4" data-density-scope="settings">
                  {meta && (
                    <DetailHeading
                      title={meta.label}
                      description={meta.description}
                      onBack={() => setMobileDetail(false)}
                    />
                  )}
                  <div className="density-stack">
                    <PaneErrorBoundary label="Recommendations" queryClient={queryClient}>
                      <RecommendationBanner patch={patch} />
                    </PaneErrorBoundary>
                    {Page && meta && (
                      <PaneErrorBoundary label={meta.label} queryClient={queryClient}>
                        <Page settings={settings} patch={patch} credits={credits} onRefresh={load} />
                      </PaneErrorBoundary>
                    )}
                  </div>
                </div>
              )}
             </ModifiedSettingsProvider>
            </SettingsRevealProvider>
          </div>
        </div>
      </TooltipProvider>
    </div>
  )
}

function Header({ mode }: { mode: SettingsMode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
      <div>
        <div className="flex items-center gap-2">
          <Settings className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-lg font-semibold">Settings</h2>
        </div>
        <p className="text-xs text-muted-foreground">
          Server configuration, features, and retrieval pipeline
        </p>
      </div>
      <SegmentedControl<SettingsMode>
        value={mode}
        onChange={setSettingsMode}
        options={[
          { value: "simple", label: "Simple" },
          { value: "advanced", label: "Advanced" },
        ]}
        size="sm"
        ariaLabel="Settings detail level"
      />
    </div>
  )
}

function DetailHeading({
  title,
  description,
  onBack,
}: {
  title: string
  description: string
  onBack: () => void
}) {
  return (
    <div className="mb-3 flex items-start gap-2">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7 shrink-0 p-0 lg:hidden"
        onClick={onBack}
        aria-label="Back to categories"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
      </Button>
      <div>
        <h3 className="text-base font-semibold">{title}</h3>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}
