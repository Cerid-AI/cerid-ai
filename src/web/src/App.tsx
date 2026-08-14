// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { AppErrorBoundary } from "@/components/layout/app-error-boundary"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { AppLayout } from "@/components/layout/app-layout"
import type { Pane } from "@/components/layout/sidebar"
import { ChatPanel } from "@/components/chat/chat-panel"
// Eager (not lazy): the bottom-tab-bar Capture button opens this via a
// fire-and-forget `cerid:quick-capture` window event, so the FAB's listener
// must be attached at first mount — a lazy chunk that loads a beat later would
// miss an early tap and silently no-op (caught by the mobile e2e).
import { QuickCaptureFab } from "@/components/quick-capture/quick-capture-fab"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import { ConversationsProvider } from "@/contexts/conversations-context"
import { AuthProvider } from "@/contexts/auth-context"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ProtectedRoute } from "@/components/auth/protected-route"
import { fetchSettings, fetchSetupStatus, setTierOverride } from "@/lib/api"
import { paneFromLocation } from "@/lib/url-state"
import { SetupWizard } from "@/components/setup/setup-wizard"
import {
  DesktopSetup,
  needsDesktopSetup,
  readDesktopSetupFlag,
} from "@/components/setup/desktop-setup"
import { useTheme } from "@/hooks/use-theme"
import { LiquidGlassDefs } from "@/components/ui/liquid-glass-defs"
import { OpeningSequence } from "@/components/ui/opening-sequence"

// Phase A/B/C consolidation aftermath — legacy panes (knowledge /
// monitoring / audit / agents / wiki / communities) live on as sub-views
// inside SourcesPane, SettingsPane → DiagnosticsSection, and SubjectsPane;
// any `goTo("monitoring")` etc. is rewritten by NavigationProvider's
// `applyRedirect` map before `setActivePane` fires, so this switch never
// sees those values. See `contexts/navigation-context.tsx::LEGACY_PANE_REDIRECTS`.
// Memories (RA-08) and Automations (RA-09) are first-class panes mounted here.
const SettingsPane = lazy(() => import("@/components/settings/settings-pane"))
const SubjectsPane = lazy(() => import("@/components/subjects/subjects-pane"))
const SourcesPane = lazy(() => import("@/components/sources/sources-pane"))
const BriefsPane = lazy(() => import("@/components/briefs/briefs-pane"))
const WorkflowsPane = lazy(() => import("@/components/workflows/workflows-pane"))
const MemoriesPane = lazy(() => import("@/components/memories/memories-pane"))
const AutomationsPane = lazy(() => import("@/components/automations/automations-pane"))
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

/**
 * SF-7 — the pane a cold load of the current URL names. Resolved once at
 * module scope: the pathname at first paint is the deep link; later
 * pathname writes come from syncPanePath and must not re-run this.
 */
function coldLoadPane(): Pane {
  if (typeof window === "undefined") return "chat"
  return paneFromLocation(window.location.pathname) ?? "chat"
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
  const [currentPane, setCurrentPane] = useState<Pane>(coldLoadPane)
  // Pane AppLayout mounts on. Two writers: a cold load of a deep URL
  // (/subjects?mode=timeline must land on Subjects, not Chat — SF-7) and
  // DesktopSetup's "Take me to Connectors" exit — AppLayout owns pane state
  // after mount, so both must ride the initial value (GUI spec MUST 1).
  const [initialPane, setInitialPane] = useState<Pane>(coldLoadPane)
  const tierCycling = useRef(false)
  // Per-MACHINE setup, independent of the server's onboarding flag. A desktop
  // client connecting to a server configured months ago from a browser sees
  // setup_required=false and onboarding_complete=true, and would otherwise
  // land in the main app having never been asked for a TCC grant or told where
  // the Apple connectors are. The server's flag is not a statement about this
  // Mac.
  const [showDesktopSetup, setShowDesktopSetup] = useState(() =>
    needsDesktopSetup({
      hasDesktopBridge:
        typeof window !== "undefined" &&
        !!(window as unknown as { cerid?: unknown }).cerid,
      completedFlag: readDesktopSetupFlag(),
    }),
  )

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
        // RA-12: the wizard is gated on setup_required alone. The old
        // onboarding_complete flag existed for the deleted OnboardingDialog
        // tour; routing it to SetupWizard forced a full (env-clobbering)
        // wizard onto configured instances. DesktopSetup covers per-machine
        // first-run independently.
        setSetupRequired(!!status.setup_required)
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

  // Show setup wizard if backend reports no API keys configured
  if (setupRequired) {
    // Wrapped in the SAME window chrome AppLayout uses — full-height circuit
    // background, vignette, safe-area insets. The wizard replaces AppLayout
    // rather than rendering inside it, so without this the dialog floated on a
    // bare page: different background, no vignette, and no safe-area padding
    // under the frameless (hiddenInset) title bar. The first screen of the
    // product looked like a different product.
    return (
      <div className="cerid-content-rise flex h-screen flex-col bg-background text-foreground bg-circuit safe-area-top safe-area-bottom safe-area-left safe-area-right">
        <div className="vignette" aria-hidden="true" />
        <div className="app-drag-region" aria-hidden="true" />
        <SetupWizard
          open
          canSkip={false}
          onComplete={() => setSetupRequired(false)}
        />
      </div>
    )
  }

  // Per-machine setup, AFTER the server wizard (which may not run at all).
  // Rendered inside the app chrome so the window looks like the product.
  if (showDesktopSetup) {
    return (
      <div className="cerid-content-rise flex h-screen flex-col bg-background text-foreground bg-circuit safe-area-top safe-area-bottom safe-area-left safe-area-right">
        <div className="vignette" aria-hidden="true" />
        <div className="app-drag-region" aria-hidden="true" />
        <DesktopSetup
          open
          onDone={() => setShowDesktopSetup(false)}
          onOpenConnectors={() => {
            // Land directly on the Apple rows — "Sources → Connectors" is not
            // a path anyone guesses.
            try {
              const url = new URL(window.location.href)
              url.searchParams.set("sources_mode", "connectors")
              window.history.replaceState({}, "", url.toString())
            } catch {
              // URL unavailable — the pane still opens on its default mode.
            }
            // AppLayout hasn't mounted yet — seed its initial pane; the
            // ?sources_mode= write above selects the Connectors sub-tab.
            setInitialPane("sources")
            setCurrentPane("sources")
            setShowDesktopSetup(false)
          }}
        />
      </div>
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
    <ConversationsProvider>
    <KBInjectionProvider>
    <AppLayout featureTier={featureTier} onCycleTier={cycleTier} onActivePaneChange={setCurrentPane} initialPane={initialPane}>
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
          case "briefs":
          case "workflows":
          case "memories":
          case "automations":
            return (
              <PaneErrorBoundary label={activePane} queryClient={queryClient}>
                <Suspense fallback={<PaneLoader />}>
                  {activePane === "settings" && <SettingsPane />}
                  {activePane === "subjects" && <SubjectsPane />}
                  {activePane === "sources" && <SourcesPane />}
                  {activePane === "briefs" && <BriefsPane />}
                  {activePane === "workflows" && <WorkflowsPane />}
                  {activePane === "memories" && <MemoriesPane />}
                  {activePane === "automations" && <AutomationsPane />}
                </Suspense>
              </PaneErrorBoundary>
            )
          // Legacy panes (knowledge / monitoring / audit / agents / wiki /
          // communities) are not handled here — NavigationProvider's
          // redirect map rewrites them to subjects/sources/settings before
          // they reach this switch. Keeping them in the Pane type union
          // preserves the goTo() contract for programmatic callers.
        }
      }}
    </AppLayout>
    {/* Quick-capture FAB — visible from every pane except Sources (own
        "Add a new source" FAB, BETA-001), Subjects (Constellation
        anchors its view-mode toggle + map settings in the same corner;
        the fixed z-40 FAB sat on top and swallowed their clicks), and
        Briefs (a reading surface — no capture affordance needed there).
        ⌘⇧N still opens quick capture from the excluded panes. */}
    {currentPane !== "sources" && currentPane !== "subjects" && currentPane !== "briefs" && (
      <QuickCaptureFab />
    )}
    </KBInjectionProvider>
    </ConversationsProvider>
    </ProtectedRoute>
    </AuthProvider>
    </TooltipProvider>
    </AppErrorBoundary>
  )
}

