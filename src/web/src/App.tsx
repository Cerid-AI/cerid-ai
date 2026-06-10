// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { AppErrorBoundary } from "@/components/layout/app-error-boundary"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { AppLayout } from "@/components/layout/app-layout"
import type { Pane } from "@/components/layout/sidebar"
import { ChatPanel } from "@/components/chat/chat-panel"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import { ConversationsProvider } from "@/contexts/conversations-context"
import { AuthProvider } from "@/contexts/auth-context"
import { UIModeProvider } from "@/contexts/ui-mode-context"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ProtectedRoute } from "@/components/auth/protected-route"
import { fetchSettings, fetchSetupStatus, setTierOverride } from "@/lib/api"
import { SetupWizard } from "@/components/setup/setup-wizard"
import { useTheme } from "@/hooks/use-theme"
import { LiquidGlassDefs } from "@/components/ui/liquid-glass-defs"
import { OpeningSequence } from "@/components/ui/opening-sequence"

// Phase A/B/C consolidation aftermath — only the 4 user-facing panes are
// mounted here. Legacy panes (knowledge / monitoring / audit / memories /
// agents / wiki / communities) live on as sub-views inside SourcesPane,
// SettingsPane → DiagnosticsSection, and SubjectsPane; any `goTo("monitoring")`
// etc. is rewritten by NavigationProvider's `applyRedirect` map before
// `setActivePane` fires, so this switch never sees the legacy values.
// See `contexts/navigation-context.tsx::LEGACY_PANE_REDIRECTS`.
const SettingsPane = lazy(() => import("@/components/settings/settings-pane"))
const SubjectsPane = lazy(() => import("@/components/subjects/subjects-pane"))
const SourcesPane = lazy(() => import("@/components/sources/sources-pane"))
const QuickCaptureFab = lazy(() =>
  import("@/components/quick-capture/quick-capture-fab").then((m) => ({ default: m.QuickCaptureFab })),
)
const AtlasPerfHarness = lazy(() => import("@/components/dev/atlas-perf-harness"))

/**
 * Dev-mode escape hatch: `?dev=atlas-perf` bypasses the full app shell
 * and mounts just the perf harness. Driven by Playwright spec
 * `tests/perf/atlas-perf.spec.ts` for budget assertions. Production
 * builds also expose this — the harness is a synthetic-fixture page;
 * no live data leaks — so QA can run the same check against a deployed
 * preview build without a special build flag.
 */
function isDevRoute(): string | null {
  if (typeof window === "undefined") return null
  return new URLSearchParams(window.location.search).get("dev")
}

function PaneLoader() {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      Loading...
    </div>
  )
}

export default function App() {
  // Initialize theme globally so dark-mode effects (bg-circuit, glow-teal, etc.)
  // work in the setup wizard path where AppLayout is not mounted.
  useTheme()

  const queryClient = useQueryClient()
  const [multiUser, setMultiUser] = useState(false)
  const [featureTier, setFeatureTier] = useState("community")
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)
  // Tracks the active pane so global chrome can be gated per-pane. The
  // Sources pane has its own "Add a new source" FAB, so the global Quick
  // Capture FAB is hidden there to avoid the two overlapping (BETA-001).
  const [currentPane, setCurrentPane] = useState<Pane>("chat")
  const tierCycling = useRef(false)
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try { return !localStorage.getItem("cerid-onboarding-complete") } catch { return false }
  })

  const cycleTier = useCallback(async () => {
    if (tierCycling.current) return
    tierCycling.current = true
    const order = ["community", "pro", "enterprise"] as const
    const next = order[(order.indexOf(featureTier as typeof order[number]) + 1) % order.length]
    try {
      const res = await setTierOverride(next)
      setFeatureTier(res.tier)
    } catch (err) {
      if (import.meta.env.DEV) console.warn("Tier override failed:", err)
    } finally {
      tierCycling.current = false
    }
  }, [featureTier])

  // Update favicon + document title based on tier
  useEffect(() => {
    const icons: Record<string, string> = { community: "/cerid-core.svg", pro: "/cerid-pro.svg", enterprise: "/cerid-vault.svg" }
    const titles: Record<string, string> = { community: "Cerid AI", pro: "Cerid Pro", enterprise: "Cerid Vault" }
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (link) link.href = icons[featureTier] ?? icons.community
    document.title = titles[featureTier] ?? titles.community
  }, [featureTier])

  useEffect(() => {
    // Check setup status first, then load settings
    fetchSetupStatus()
      .then((status) => {
        if (status.setup_required) {
          setSetupRequired(true)
        } else {
          setSetupRequired(false)
        }
      })
      .catch(() => {
        // Backend unreachable — skip setup check, show main app
        setSetupRequired(false)
      })

    fetchSettings()
      .then((s) => {
        setMultiUser(!!s.multi_user)
        setFeatureTier(s.feature_tier ?? "community")
      })
      .catch((err) => { if (import.meta.env.DEV) console.warn("Settings fetch failed:", err) })
  }, [])

  // Dev-mode perf harness — bypasses the full app shell entirely so
  // measurements aren't contaminated by sidebar/chat/settings work.
  const devRoute = isDevRoute()
  if (devRoute === "atlas-perf") {
    return (
      <AppErrorBoundary>
        <Suspense fallback={<PaneLoader />}>
          <div className="h-screen w-screen">
            <AtlasPerfHarness />
          </div>
        </Suspense>
      </AppErrorBoundary>
    )
  }

  // Show nothing while checking setup status
  if (setupRequired === null) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading...
      </div>
    )
  }

  // Show setup wizard if backend reports no API keys configured OR first-run onboarding
  if (setupRequired || showOnboarding) {
    return (
      <UIModeProvider>
        <SetupWizard
          open
          canSkip={!setupRequired && showOnboarding}
          onComplete={() => {
            setSetupRequired(false)
            setShowOnboarding(false)
          }}
        />
      </UIModeProvider>
    )
  }

  return (
    <AppErrorBoundary>
    {/* SVG filter defs for .liquid-glass; mounted once. Zero render cost
        until a `.liquid-glass` surface references `filter: url(#cerid-liquid-glass)`. */}
    <LiquidGlassDefs />
    {/* First-paint reveal — auto-skipped on revisit via sessionStorage flag,
        and on prefers-reduced-motion. */}
    <OpeningSequence />
    {/* Mount TooltipProvider once at app root per shadcn convention (ui-ux-pro-max
        shadcn rule #39). Without this, panes that use `<Tooltip>` without their
        own local provider crash on mount — Memories pane was the symptom. */}
    <TooltipProvider delayDuration={0}>
    <AuthProvider>
    <ProtectedRoute multiUser={multiUser}>
    <UIModeProvider>
    <ConversationsProvider>
    <KBInjectionProvider>
    <AppLayout featureTier={featureTier} onCycleTier={cycleTier} onActivePaneChange={setCurrentPane}>
      {(activePane, openSidebar) => {
        switch (activePane) {
          case "chat":
            return (
              <PaneErrorBoundary label="Chat" queryClient={queryClient}>
                <ChatPanel onOpenSidebar={openSidebar} />
              </PaneErrorBoundary>
            )
          case "settings":
          case "subjects":
          case "sources":
            return (
              <PaneErrorBoundary label={activePane} queryClient={queryClient}>
                <Suspense fallback={<PaneLoader />}>
                  {activePane === "settings" && <SettingsPane />}
                  {activePane === "subjects" && <SubjectsPane />}
                  {activePane === "sources" && <SourcesPane />}
                </Suspense>
              </PaneErrorBoundary>
            )
          // Legacy panes (knowledge / monitoring / audit / memories / agents /
          // wiki / communities) are not handled here — NavigationProvider's
          // redirect map rewrites them to subjects/sources/settings before
          // they reach this switch. Keeping them in the Pane type union
          // preserves the goTo() contract for programmatic callers.
        }
      }}
    </AppLayout>
    {/* Quick-capture FAB — visible from every pane except Sources (own
        "Add a new source" FAB, BETA-001) and Subjects (Constellation
        anchors its view-mode toggle + map settings in the same corner;
        the fixed z-40 FAB sat on top and swallowed their clicks).
        ⌘⇧N still opens quick capture from both panes. */}
    {currentPane !== "sources" && currentPane !== "subjects" && (
      <Suspense fallback={null}>
        <QuickCaptureFab />
      </Suspense>
    )}
    </KBInjectionProvider>
    </ConversationsProvider>
    </UIModeProvider>
    </ProtectedRoute>
    </AuthProvider>
    </TooltipProvider>
    </AppErrorBoundary>
  )
}

