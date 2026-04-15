// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react"
import { Loader2 } from "lucide-react"
import { AppErrorBoundary } from "@/components/layout/app-error-boundary"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { AppLayout } from "@/components/layout/app-layout"
import { ChatPanel } from "@/components/chat/chat-panel"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import { ConversationsProvider } from "@/contexts/conversations-context"
import { AuthProvider } from "@/contexts/auth-context"
import { UIModeProvider } from "@/contexts/ui-mode-context"
import { ProtectedRoute } from "@/components/auth/protected-route"
import { fetchSettings, fetchSetupStatus, setTierOverride } from "@/lib/api"
import { SetupWizard } from "@/components/setup/setup-wizard"
import { useTheme } from "@/hooks/use-theme"

const KnowledgePane = lazy(() => import("@/components/kb/knowledge-pane"))
const MonitoringPane = lazy(() => import("@/components/monitoring/monitoring-pane"))
const AuditPane = lazy(() => import("@/components/audit/audit-pane"))
const MemoriesPane = lazy(() => import("@/components/memories/memories-pane"))
const SettingsPane = lazy(() => import("@/components/settings/settings-pane"))
const AgentConsole = lazy(() => import("@/components/agents/agent-console"))

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

  const [multiUser, setMultiUser] = useState(false)
  const [featureTier, setFeatureTier] = useState("community")
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)
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
    <AuthProvider>
    <ProtectedRoute multiUser={multiUser}>
    <UIModeProvider>
    <ConversationsProvider>
    <KBInjectionProvider>
    <AppLayout featureTier={featureTier} onCycleTier={cycleTier}>
      {(activePane) => {
        switch (activePane) {
          case "chat":
            return (
              <PaneErrorBoundary label="Chat">
                <ChatPanel />
              </PaneErrorBoundary>
            )
          case "knowledge":
          case "monitoring":
          case "audit":
          case "memories":
          case "agents":
          case "settings":
            return (
              <PaneErrorBoundary label={activePane}>
                <Suspense fallback={<PaneLoader />}>
                  {activePane === "knowledge" && <KnowledgePane />}
                  {activePane === "monitoring" && <MonitoringPane />}
                  {activePane === "audit" && <AuditPane />}
                  {activePane === "memories" && <MemoriesPane />}
                  {activePane === "agents" && <AgentConsole />}
                  {activePane === "settings" && <SettingsPane />}
                </Suspense>
              </PaneErrorBoundary>
            )
        }
      }}
    </AppLayout>
    </KBInjectionProvider>
    </ConversationsProvider>
    </UIModeProvider>
    </ProtectedRoute>
    </AuthProvider>
    </AppErrorBoundary>
  )
}
