// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react"
import { Sidebar, type Pane } from "./sidebar"
import { StatusBar } from "./status-bar"
import { AgentConsole } from "@/components/console/AgentConsole"
import { useAgentConsole } from "@/hooks/use-agent-console"
import { useTheme } from "@/hooks/use-theme"

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? v === "true" : fallback
  } catch { return fallback }
}

interface AppLayoutProps {
  children: (activePane: Pane) => React.ReactNode
  featureTier?: string
  onCycleTier?: () => void
}

export function AppLayout({ children, featureTier, onCycleTier }: AppLayoutProps) {
  const [activePane, setActivePane] = useState<Pane>("chat")
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 1024)
  const { theme, toggleTheme } = useTheme()

  // Agent console state (persisted in localStorage)
  const [consoleOpen, setConsoleOpen] = useState(() => readBool("cerid-agent-console", false))
  const { events, connected, unreadCount, clearEvents, resetUnread } = useAgentConsole(consoleOpen)

  // Persist console open/closed state
  const toggleConsole = useCallback(() => {
    setConsoleOpen((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-agent-console", String(next)) } catch { /* noop */ }
      return next
    })
  }, [])

  // Reset unread when console opens
  useEffect(() => {
    if (consoleOpen) resetUnread()
  }, [consoleOpen, resetUnread])

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1024px)")
    const handler = (e: MediaQueryListEvent) => setSidebarCollapsed(e.matches)
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [])

  return (
    <div className="flex h-screen flex-col bg-background text-foreground safe-area-top safe-area-bottom safe-area-left safe-area-right">
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          activePane={activePane}
          onPaneChange={setActivePane}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
          theme={theme}
          onToggleTheme={toggleTheme}
          featureTier={featureTier}
          onCycleTier={onCycleTier}
        />
        <main key={activePane} className="flex-1 animate-in fade-in duration-200 overflow-hidden">{children(activePane)}</main>
      </div>
      {consoleOpen && (
        <AgentConsole
          events={events}
          connected={connected}
          onClear={clearEvents}
          onClose={toggleConsole}
        />
      )}
      <StatusBar
        consoleOpen={consoleOpen}
        onToggleConsole={toggleConsole}
        consoleUnreadCount={unreadCount}
      />
    </div>
  )
}
