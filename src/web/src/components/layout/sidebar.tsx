// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import {
  MessageSquare, Database, Activity, FileBarChart, Brain, Settings,
  Sun, Moon, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Plus, History,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Separator } from "@/components/ui/separator"
import { ConversationList } from "@/components/chat/conversation-list"
import { useConversationsContext } from "@/contexts/conversations-context"
import { MODELS } from "@/lib/types"
import { cn } from "@/lib/utils"

export type Pane = "chat" | "knowledge" | "monitoring" | "audit" | "memories" | "settings"

interface SidebarProps {
  activePane: Pane
  onPaneChange: (pane: Pane) => void
  collapsed: boolean
  onToggleCollapse: () => void
  theme: "dark" | "light"
  onToggleTheme: () => void
}

const NAV_ITEMS: { pane: Pane; icon: typeof MessageSquare; label: string }[] = [
  { pane: "chat", icon: MessageSquare, label: "Chat" },
  { pane: "knowledge", icon: Database, label: "Knowledge" },
  { pane: "monitoring", icon: Activity, label: "Monitoring" },
  { pane: "audit", icon: FileBarChart, label: "Audit" },
  { pane: "memories", icon: Brain, label: "Memories" },
  { pane: "settings", icon: Settings, label: "Settings" },
]

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? v === "true" : fallback
  } catch { return fallback }
}

export function Sidebar({ activePane, onPaneChange, collapsed, onToggleCollapse, theme, onToggleTheme }: SidebarProps) {
  const { conversations, activeId, setActiveId, create, remove } = useConversationsContext()
  const [historyExpanded, setHistoryExpanded] = useState(() => readBool("cerid-sidebar-history", true))

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
          {!collapsed && <span className="text-lg font-semibold tracking-tight">Cerid AI</span>}
          <Button variant="ghost" size="icon" className={cn("ml-auto h-8 w-8")} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggleCollapse}>
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>

        {/* Nav items */}
        <nav className="space-y-1 p-2">
          {NAV_ITEMS.map(({ pane, icon: Icon, label }) => (
            <Tooltip key={pane}>
              <TooltipTrigger asChild>
                <Button
                  variant={activePane === pane ? "secondary" : "ghost"}
                  className={cn("w-full justify-start gap-3", collapsed && "justify-center px-0")}
                  onClick={() => onPaneChange(pane)}
                >
                  <Icon className="h-4 w-4 shrink-0" />
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
                {historyExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />}
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
        <div className="border-t p-2">
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
