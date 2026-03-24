// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import {
  MessageSquare, Database, HeartPulse, BarChart3, Brain, Settings,
  Sun, Moon, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Plus, History,
  TrendingUp,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { ConversationList } from "@/components/chat/conversation-list"
import { useConversationsContext } from "@/contexts/conversations-context"
import { useUIMode } from "@/contexts/ui-mode-context"
import { MODELS } from "@/lib/types"
import { cn } from "@/lib/utils"

export type Pane = "chat" | "knowledge" | "monitoring" | "audit" | "memories" | "trading" | "settings"

interface SidebarProps {
  activePane: Pane
  onPaneChange: (pane: Pane) => void
  collapsed: boolean
  onToggleCollapse: () => void
  theme: "dark" | "light"
  onToggleTheme: () => void
  tradingEnabled?: boolean
}

const NAV_ITEMS: { pane: Pane; icon: typeof MessageSquare; label: string }[] = [
  { pane: "chat", icon: MessageSquare, label: "Chat" },
  { pane: "knowledge", icon: Database, label: "Knowledge" },
  { pane: "monitoring", icon: HeartPulse, label: "Health" },
  { pane: "audit", icon: BarChart3, label: "Analytics" },
  { pane: "memories", icon: Brain, label: "Memories" },
  { pane: "settings", icon: Settings, label: "Settings" },
]

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? v === "true" : fallback
  } catch { return fallback }
}

const SIMPLE_PANES = new Set<Pane>(["chat", "memories", "settings"])

export function Sidebar({ activePane, onPaneChange, collapsed, onToggleCollapse, theme, onToggleTheme, tradingEnabled }: SidebarProps) {
  const { conversations, activeId, setActiveId, create, remove } = useConversationsContext()
  const { toggle: toggleMode, isSimple } = useUIMode()
  const [historyExpanded, setHistoryExpanded] = useState(() => readBool("cerid-sidebar-history", true))

  const allNav = tradingEnabled
    ? [...NAV_ITEMS.slice(0, -1), { pane: "trading" as Pane, icon: TrendingUp, label: "Trading" }, NAV_ITEMS[NAV_ITEMS.length - 1]]
    : NAV_ITEMS
  const visibleNav = isSimple ? allNav.filter((n) => SIMPLE_PANES.has(n.pane)) : allNav

  const toggleHistory = () => {
    setHistoryExpanded((prev) => {
      const next = !prev
      try { localStorage.setItem("cerid-sidebar-history", String(next)) } catch { /* noop */ }
      return next
    })
  }

  const handleSelectConversation = (id: string) => {
    setActiveId(id)
    if (activePane !== "chat") onPaneChange("chat")
  }

  const handleNewChat = () => {
    create(MODELS[0].id)
    if (activePane !== "chat") onPaneChange("chat")
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div
        className={cn(
          "flex h-full flex-col border-r bg-muted/40 transition-all duration-200",
          collapsed ? "w-14" : "w-52"
        )}
      >
        {/* Logo area */}
        <div className="flex h-14 items-center border-b px-3">
          {!collapsed && <span className="text-lg font-semibold tracking-tight">Cerid <span className="text-brand">AI</span></span>}
          <Button variant="ghost" size="icon" className={cn("ml-auto h-8 w-8")} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggleCollapse}>
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>

        {/* Nav items */}
        <nav className="space-y-1 p-2">
          {visibleNav.map(({ pane, icon: Icon, label }) => (
            <Tooltip key={pane}>
              <TooltipTrigger asChild>
                <Button
                  variant={activePane === pane ? "secondary" : "ghost"}
                  className={cn(
                    "w-full justify-start gap-3",
                    collapsed && "justify-center px-0",
                    activePane === pane && "border-l-2 border-brand bg-brand/5"
                  )}
                  onClick={() => onPaneChange(pane)}
                >
                  <Icon className={cn("h-4 w-4 shrink-0", activePane === pane && "text-brand")} />
                  {!collapsed && <span>{label}</span>}
                </Button>
              </TooltipTrigger>
              {collapsed && <TooltipContent side="right">{label}</TooltipContent>}
            </Tooltip>
          ))}
        </nav>

        {/* Conversation history — only when sidebar expanded */}
        {!collapsed ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <Separator />
            <div className="flex items-center gap-1 px-3 py-1.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 flex-1 justify-start gap-1.5 px-1 text-xs text-muted-foreground"
                onClick={toggleHistory}
              >
                {historyExpanded ? <ChevronUp className="h-3 w-3 text-amber-600 dark:text-yellow-400" /> : <ChevronDown className="h-3 w-3 text-amber-600 dark:text-yellow-400" />}
                History
              </Button>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    onClick={handleNewChat}
                    aria-label="New conversation"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right">New chat</TooltipContent>
              </Tooltip>
            </div>
            {historyExpanded && (
              <div className="min-h-0 flex-1">
                <ConversationList
                  conversations={conversations}
                  activeId={activeId}
                  onSelect={handleSelectConversation}
                  onDelete={remove}
                />
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1">
            {/* Collapsed: just a history icon button */}
            <div className="p-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="w-full"
                    onClick={() => { onToggleCollapse(); setHistoryExpanded(true) }}
                    aria-label="Show conversation history"
                  >
                    <History className="h-4 w-4 shrink-0" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right">Conversation history</TooltipContent>
              </Tooltip>
            </div>
          </div>
        )}

        {/* Bottom controls */}
        <div className="space-y-1 border-t p-2">
          {/* Mode toggle */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                className={cn(
                  "flex w-full items-center rounded-md px-3 py-1.5 text-sm hover:bg-accent",
                  collapsed && "justify-center px-0"
                )}
                onClick={toggleMode}
                aria-label={isSimple ? "Switch to advanced mode" : "Switch to simple mode"}
              >
                {!collapsed ? (
                  <>
                    <span className="flex-1 text-left text-xs text-muted-foreground">
                      {isSimple ? "Simple" : "Advanced"}
                    </span>
                    <Switch checked={!isSimple} className="scale-75" />
                  </>
                ) : (
                  <span className="text-[10px] font-medium text-muted-foreground">
                    {isSimple ? "S" : "A"}
                  </span>
                )}
              </button>
            </TooltipTrigger>
            {collapsed && (
              <TooltipContent side="right">
                {isSimple ? "Simple mode — click for Advanced" : "Advanced mode — click for Simple"}
              </TooltipContent>
            )}
          </Tooltip>

          {/* Theme toggle */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn("w-full", !collapsed && "justify-start gap-3 px-3")}
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                onClick={onToggleTheme}
              >
                {theme === "dark" ? <Sun className="h-4 w-4 shrink-0" /> : <Moon className="h-4 w-4 shrink-0" />}
                {!collapsed && <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>}
              </Button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right">Toggle theme</TooltipContent>}
          </Tooltip>
        </div>
      </div>
    </TooltipProvider>
  )
}
