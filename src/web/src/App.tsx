import { AppLayout } from "@/components/layout/app-layout"
import { ChatPanel } from "@/components/chat/chat-panel"
import type { Pane } from "@/components/layout/sidebar"

function PanePlaceholder({ pane }: { pane: Pane }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <p className="text-lg">{pane.charAt(0).toUpperCase() + pane.slice(1)} pane — Phase 6B/6C</p>
    </div>
  )
}

export default function App() {
  return (
    <AppLayout>
      {(activePane) =>
        activePane === "chat" ? <ChatPanel /> : <PanePlaceholder pane={activePane} />
      }
    </AppLayout>
  )
}
