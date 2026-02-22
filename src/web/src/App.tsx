import { AppLayout } from "@/components/layout/app-layout"
import type { Pane } from "@/components/layout/sidebar"

function PanePlaceholder({ pane }: { pane: Pane }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <p className="text-lg">{pane.charAt(0).toUpperCase() + pane.slice(1)} — coming soon</p>
    </div>
  )
}

export default function App() {
  return (
    <AppLayout>
      {(activePane) => <PanePlaceholder pane={activePane} />}
    </AppLayout>
  )
}
