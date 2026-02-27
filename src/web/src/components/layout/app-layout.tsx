// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect } from "react"
import { Sidebar, type Pane } from "./sidebar"
import { StatusBar } from "./status-bar"
import { useTheme } from "@/hooks/use-theme"

interface AppLayoutProps {
  children: (activePane: Pane) => React.ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  const [activePane, setActivePane] = useState<Pane>("chat")
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 768)
  const { theme, toggleTheme } = useTheme()

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)")
    const handler = (e: MediaQueryListEvent) => setSidebarCollapsed(e.matches)
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [])

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          activePane={activePane}
          onPaneChange={setActivePane}
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
        <main className="flex-1 overflow-hidden">{children(activePane)}</main>
      </div>
      <StatusBar />
    </div>
  )
}