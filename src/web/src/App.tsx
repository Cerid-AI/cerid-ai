// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { Loader2 } from "lucide-react"
import { AppLayout } from "@/components/layout/app-layout"
import { ChatPanel } from "@/components/chat/chat-panel"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import { ConversationsProvider } from "@/contexts/conversations-context"
import { AuthProvider } from "@/contexts/auth-context"
import { UIModeProvider } from "@/contexts/ui-mode-context"
import { ProtectedRoute } from "@/components/auth/protected-route"
import { fetchSettings } from "@/lib/api"
import { OnboardingDialog } from "@/components/onboarding/onboarding-dialog"

const KnowledgePane = lazy(() => import("@/components/kb/knowledge-pane"))
const MonitoringPane = lazy(() => import("@/components/monitoring/monitoring-pane"))
const AuditPane = lazy(() => import("@/components/audit/audit-pane"))
const MemoriesPane = lazy(() => import("@/components/memories/memories-pane"))
const SettingsPane = lazy(() => import("@/components/settings/settings-pane"))

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
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try { return !localStorage.getItem("cerid-onboarding-complete") } catch { return false }
  })

  const handleOnboardingComplete = useCallback(() => setShowOnboarding(false), [])

  useEffect(() => {
    fetchSettings()
      .then((s) => setMultiUser(!!s.multi_user))
      .catch(() => {})
  }, [])

  return (
    <AuthProvider>
    <ProtectedRoute multiUser={multiUser}>
    <UIModeProvider>
    {showOnboarding && <OnboardingDialog open={showOnboarding} onComplete={handleOnboardingComplete} />}
    <ConversationsProvider>
    <KBInjectionProvider>
    <AppLayout>
      {(activePane) => {
        switch (activePane) {
          case "chat":
            return <ChatPanel />
          case "knowledge":
          case "monitoring":
          case "audit":
          case "memories":
          case "settings":
            return (
              <Suspense fallback={<PaneLoader />}>
                {activePane === "knowledge" && <KnowledgePane />}
                {activePane === "monitoring" && <MonitoringPane />}
                {activePane === "audit" && <AuditPane />}
                {activePane === "memories" && <MemoriesPane />}
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
  )
}
