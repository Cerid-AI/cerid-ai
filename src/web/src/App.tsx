import { lazy, Suspense } from "react"
import { Loader2 } from "lucide-react"
import { AppLayout } from "@/components/layout/app-layout"
import { ChatPanel } from "@/components/chat/chat-panel"
import { KBInjectionProvider } from "@/contexts/kb-injection-context"
import type { Pane } from "@/components/layout/sidebar"

const KnowledgePane = lazy(() => import("@/components/kb/knowledge-pane"))
const MonitoringPane = lazy(() => import("@/components/monitoring/monitoring-pane"))
const AuditPane = lazy(() => import("@/components/audit/audit-pane"))

function PaneLoader() {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      Loading...
    </div>
  )
}

function PanePlaceholder({ pane }: { pane: Pane }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <p className="text-lg">{pane.charAt(0).toUpperCase() + pane.slice(1)} pane — coming soon</p>
    </div>
  )
}

export default function App() {
  return (
    <KBInjectionProvider>
    <AppLayout>
      {(activePane) => {
        switch (activePane) {
          case "chat":
            return <ChatPanel />
          case "knowledge":
          case "monitoring":
          case "audit":
            return (
              <Suspense fallback={<PaneLoader />}>
                {activePane === "knowledge" && <KnowledgePane />}
                {activePane === "monitoring" && <MonitoringPane />}
                {activePane === "audit" && <AuditPane />}
              </Suspense>
            )
          default:
            return <PanePlaceholder pane={activePane} />
        }
      }}
    </AppLayout>
    </KBInjectionProvider>
  )
}
