// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useEffect, useCallback, useMemo } from "react"
import { logSwallowedError } from "@/lib/log-swallowed"
import { clearForeignPaneParams, syncPanePath } from "@/lib/url-state"
import { Sidebar, type Pane } from "./sidebar"
import { NavigationProvider } from "@/contexts/navigation-context"
import { DeepLinkRouter } from "./deep-link-router"
import { StatusBar } from "./status-bar"
import { BottomTabBar } from "./bottom-tab-bar"
import { AgentConsole } from "@/components/console/AgentConsole"
import { ModelDownloadBanner } from "@/components/model-download-banner"
import { useAgentConsole } from "@/hooks/use-agent-console"
import { useTheme } from "@/hooks/use-theme"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"

const PHONE_MQ = "(max-width: 767px)"

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? v === "true" : fallback
  } catch { return fallback }
}

interface AppLayoutProps {
  children: (activePane: Pane, openSidebar: () => void) => React.ReactNode
  featureTier?: string
  onCycleTier?: () => void
  /** Notifies the parent of the active pane so it can gate global chrome
      (e.g. the Quick Capture FAB is hidden on Sources, which has its own). */
  onActivePaneChange?: (pane: Pane) => void
  /** Pane to mount on. Used only as the useState initial value — DesktopSetup's
      "Take me to Connectors" exit needs to land on Sources, and pane state
      lives here, not in App (GUI spec MUST 1). */
  initialPane?: Pane
}

export function AppLayout({ children, featureTier, onCycleTier, onActivePaneChange, initialPane }: AppLayoutProps) {
  const [activePane, setActivePane] = useState<Pane>(initialPane ?? "chat")

  // F-URL-01 — Switching primary nav must strip stale per-pane URL
  // params (e.g. `?mode=wiki` left over from Subjects when the user
  // navigates to Settings). Centralised here so every entry point —
  // sidebar click, mobile sheet, NavigationProvider.goTo — gets the
  // same hygiene without each call site having to remember.
  const handlePaneChange = useCallback((next: Pane) => {
    setActivePane((prev) => {
      if (next !== prev) {
        clearForeignPaneParams(next)
        // SF-7 — keep the pathname naming the active pane so reload and
        // copied URLs land back here (cold loads read it via paneFromLocation).
        syncPanePath(next)
      }
      return next
    })
    onActivePaneChange?.(next)
  }, [onActivePaneChange])
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.innerWidth < 1024)
  const [isPhone, setIsPhone] = useState(() => {
    if (typeof window === "undefined") return false
    return window.matchMedia(PHONE_MQ).matches
  })
  const [sidebarSheetOpen, setSidebarSheetOpen] = useState(false)
  const { theme, toggleTheme } = useTheme()

  // Agent console state (persisted in localStorage)
  const [consoleOpen, setConsoleOpen] = useState(() => readBool("cerid-agent-console", false))
  const { events, connected, unreadCount, clearEvents, resetUnread } = useAgentConsole(consoleOpen)

  // Persist console open/closed state
  const toggleConsole = useCallback(() => {
    setConsoleOpen((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-agent-console", String(next)) } catch (err) { logSwallowedError(err, "localStorage.setItem", { key: "cerid-agent-console" }) }
      return next
    })
  }, [])

  // Reset unread when console opens
  useEffect(() => {
    if (consoleOpen) resetUnread()
  }, [consoleOpen, resetUnread])

  // Activity LED: show pulsing dot on panes with background activity
  const activePanes = useMemo(() => {
    const s = new Set<Pane>()
    if (connected && unreadCount > 0) s.add("agents")
    return s
  }, [connected, unreadCount])

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1024px)")
    const handler = (e: MediaQueryListEvent) => setSidebarCollapsed(e.matches)
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [])

  useEffect(() => {
    const mq = window.matchMedia(PHONE_MQ)
    const handler = (e: MediaQueryListEvent) => setIsPhone(e.matches)
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [])

  return (
    <div className="cerid-content-rise flex h-screen flex-col bg-background text-foreground bg-circuit safe-area-top safe-area-bottom safe-area-left safe-area-right">
      <div className="vignette" aria-hidden="true" />
      {/* Lets the frameless window be dragged. See .app-drag-region — the only
          drag region in the repo was in a loading shell local mode never
          loads, so the window could not be moved at all. */}
      <div className="app-drag-region" aria-hidden="true" />
      {/* Phase E.6.6: first-query model-download notification —
          self-suppressing when both ONNX models are cached or the user
          has dismissed the banner. Sits above the main flex row so the
          layout shifts down when shown rather than overlapping. */}
      <ModelDownloadBanner />
      <NavigationProvider activePane={activePane} onPaneChange={handlePaneChange}>
        {/* Routes cerid:// links (Spotlight results) to the artifact they name.
            Inside the provider because it navigates; mounted once. */}
        <DeepLinkRouter />
        <div className="flex flex-1 overflow-hidden">
          {isPhone ? (
            <Sheet open={sidebarSheetOpen} onOpenChange={setSidebarSheetOpen}>
              <SheetContent side="left" className="w-52 p-0 flex flex-col">
                <SheetTitle className="sr-only">Navigation</SheetTitle>
                <Sidebar
                  activePane={activePane}
                  onPaneChange={(pane) => { handlePaneChange(pane); setSidebarSheetOpen(false) }}
                  collapsed={false}
                  onToggleCollapse={() => {}}
                  theme={theme}
                  onToggleTheme={toggleTheme}
                  featureTier={featureTier}
                  onCycleTier={onCycleTier}
                  activePanes={activePanes}
                />
              </SheetContent>
            </Sheet>
          ) : (
            <Sidebar
              activePane={activePane}
              onPaneChange={handlePaneChange}
              collapsed={sidebarCollapsed}
              onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
              theme={theme}
              onToggleTheme={toggleTheme}
              featureTier={featureTier}
              onCycleTier={onCycleTier}
              activePanes={activePanes}
            />
          )}
          <main key={activePane} className="flex-1 animate-in fade-in duration-200 overflow-hidden pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0"> {/* drift-allowed: safe-area-aware bottom-bar clearance (no static utility expresses env()) */}
            {children(activePane, () => setSidebarSheetOpen(true))}
          </main>
        </div>
        {/* Task 3.7 — persistent Chat/Capture/Menu tab bar, <md only. Sits
            below <main> (whose safe-area-aware bottom padding keeps content
            clear of it) and covers StatusBar's screen position on mobile,
            so StatusBar hides <md. */}
        <BottomTabBar onOpenMenu={() => setSidebarSheetOpen(true)} />
      </NavigationProvider>
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
        featureTier={featureTier}
      />
    </div>
  )
}
