// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { lazy, Suspense, useCallback, useEffect, useState } from "react"
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
import { fetchSettings, fetchSetupStatus } from "@/lib/api"
import { OnboardingDialog } from "@/components/onboarding/onboarding-dialog"
import { SetupWizard } from "@/components/setup/setup-wizard"

const KnowledgePane = lazy(() => import("@/components/kb/knowledge-pane"))
const MonitoringPane = lazy(() => import("@/components/monitoring/monitoring-pane"))
const AuditPane = lazy(() => import("@/components/audit/audit-pane"))
const MemoriesPane = lazy(() => import("@/components/memories/memories-pane"))
const SettingsPane = lazy(() => import("@/components/settings/settings-pane"))
const TradingPane = lazy(() => import("@/components/trading/trading-pane"))

function PaneLoader() {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      Loading...
    </div>
  )
}

export default function App() {
  const [multiUser, setMultiUser] = useState(false)
  const [tradingEnabled, setTradingEnabled] = useState(false)
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null)
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try { return !localStorage.getItem("cerid-onboarding-complete") } catch { return false }
  })

  const handleOnboardingComplete = useCallback(() => setShowOnboarding(false), [])
  const handleSetupComplete = useCallback(() => setSetupRequired(false), [])

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
        setTradingEnabled(!!s.trading_enabled)
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

  // Show setup wizard if backend reports no API keys configured
  if (setupRequired) {
    return <SetupWizard open onComplete={handleSetupComplete} />
  }

  return (
    <AppErrorBoundary>
    <AuthProvider>
    <ProtectedRoute multiUser={multiUser}>
    <UIModeProvider>
    {showOnboarding && <OnboardingDialog open={showOnboarding} onComplete={handleOnboardingComplete} />}
    <ConversationsProvider>
    <KBInjectionProvider>
    <AppLayout tradingEnabled={tradingEnabled}>
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
          case "trading":
          case "settings":
            return (
              <Suspense fallback={<PaneLoader />}>
                {activePane === "knowledge" && <KnowledgePane />}
                {activePane === "monitoring" && <MonitoringPane />}
                {activePane === "audit" && <AuditPane />}
                {activePane === "memories" && <MemoriesPane />}
                {activePane === "trading" && <TradingPane />}
                {activePane === "settings" && <SettingsPane />}
              </Suspense>
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
