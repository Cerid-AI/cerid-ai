// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  MessageSquare, Database, HeartPulse, BarChart3, Brain, Settings,
  Sun, Moon, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Plus, History,
  Shield, Activity,
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
import { fetchModelUpdatesFull } from "@/lib/api"

export type Pane = "chat" | "knowledge" | "monitoring" | "audit" | "memories" | "agents" | "settings"

interface SidebarProps {
  activePane: Pane
  onPaneChange: (pane: Pane) => void
  collapsed: boolean
  onToggleCollapse: () => void
  theme: "dark" | "light"
  onToggleTheme: () => void
  featureTier?: string
  onCycleTier?: () => void
}

const NAV_ITEMS: { pane: Pane; icon: typeof MessageSquare; label: string }[] = [
  { pane: "chat", icon: MessageSquare, label: "Chat" },
  { pane: "knowledge", icon: Database, label: "Knowledge" },
  { pane: "monitoring", icon: HeartPulse, label: "Health" },
  { pane: "audit", icon: BarChart3, label: "Analytics" },
  { pane: "memories", icon: Brain, label: "Memories" },
  { pane: "agents", icon: Activity, label: "Agents" },
  { pane: "settings", icon: Settings, label: "Settings" },
]

function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? v === "true" : fallback
  } catch { return fallback }
}

const SIMPLE_PANES = new Set<Pane>(["chat", "memories", "settings"])

const TIER_CONFIG: Record<string, { label: string; wordmark: string; tierWord: string; tierClass: string; iconColor: string; icon: string }> = {
  community: { label: "Core", wordmark: "CERID", tierWord: "CORE", tierClass: "text-muted-foreground", iconColor: "text-brand", icon: "/cerid-core.svg" },
  pro:       { label: "Pro",  wordmark: "CERID", tierWord: "PRO",  tierClass: "text-muted-foreground", iconColor: "text-brand", icon: "/cerid-pro.svg" },
  enterprise:{ label: "Vault",wordmark: "CERID", tierWord: "VAULT",tierClass: "text-gold",            iconColor: "text-gold",  icon: "/cerid-vault.svg" },
}
const TIER_LABELS: Record<string, string> = { community: "Core", pro: "Pro", enterprise: "Vault" }
const TIER_COLORS: Record<string, string> = { community: "text-muted-foreground", pro: "text-brand", enterprise: "text-gold" }

export function Sidebar({ activePane, onPaneChange, collapsed, onToggleCollapse, theme, onToggleTheme, featureTier, onCycleTier }: SidebarProps) {
  const {
    visibleConversations, activeId, setActiveId, create, remove, rename,
    archive, unarchive, showArchived, toggleShowArchived, archivedCount,
    bulkDelete, bulkArchive,
  } = useConversationsContext()
  const { toggle: toggleMode, isSimple } = useUIMode()
  const [historyExpanded, setHistoryExpanded] = useState(() => readBool("cerid-sidebar-history", true))
  const { data: modelUpdates } = useQuery({
    queryKey: ["model-updates"],
    queryFn: fetchModelUpdatesFull,
    refetchInterval: 300_000,
    staleTime: 120_000,
  })
  const updateCount = modelUpdates?.updates?.length ?? 0

  const visibleNav = isSimple ? NAV_ITEMS.filter((n) => SIMPLE_PANES.has(n.pane)) : NAV_ITEMS

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
        {/* Logo area — tier-reactive */}
        <div className="flex h-[4.75rem] items-center border-b px-3">
          {(() => {
            const tier = TIER_CONFIG[featureTier ?? "community"] ?? TIER_CONFIG.community
            return collapsed ? (
              <img src={tier.icon} alt={`Cerid ${tier.label}`} className="h-10 w-10 shrink-0" />
            ) : (
              <div className="flex items-center gap-2.5">
                <img src={tier.icon} alt={`Cerid ${tier.label}`} className="h-10 w-10 shrink-0" />
                <span className="text-[21px] font-bold tracking-tight leading-none">
                  <span className="bg-gradient-to-r from-brand to-[oklch(0.90_0.14_178)] bg-clip-text text-transparent">{tier.wordmark}</span>
                  {" "}
                  <span className={cn("font-semibold text-[20px]", tier.tierClass)}>{tier.tierWord}</span>
                </span>
              </div>
            )
          })()}
          <Button variant="ghost" size="icon" className={cn("ml-auto h-8 w-8")} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggleCollapse}>
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </Button>
        </div>

        {/* Nav items */}
        <nav className="space-y-1 p-2">
          {visibleNav.map(({ pane, icon: Icon, label }) => {
            const showBadge = pane === "settings" && updateCount > 0
            const navButton = (
              <Tooltip key={pane}>
                <TooltipTrigger asChild>
                  <Button
                    variant={activePane === pane ? "secondary" : "ghost"}
                    className={cn(
                      "w-full justify-start gap-3",
                      collapsed && "justify-center px-0",
                      activePane === pane && "border-l-2 border-brand bg-brand/5",
                      pane === "chat" && !collapsed && "flex-1",
                    )}
                    onClick={() => onPaneChange(pane)}
                  >
                    <span className="relative shrink-0">
                      <Icon className={cn("h-4 w-4", activePane === pane && "text-brand")} />
                      {showBadge && (
                        <span className="absolute -right-1 -top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-teal-500 text-[8px] font-bold text-white">
                          {updateCount > 9 ? "9+" : updateCount}
                        </span>
                      )}
                    </span>
                    {!collapsed && (
                      <span className="flex items-center gap-1.5">
                        {label}
                        {showBadge && !collapsed && (
                          <span className="rounded-full bg-teal-500/10 px-1.5 py-0 text-[9px] font-medium text-teal-600 dark:text-teal-400">
                            {updateCount}
                          </span>
                        )}
                      </span>
                    )}
                  </Button>
                </TooltipTrigger>
                {collapsed && <TooltipContent side="right">{label}</TooltipContent>}
              </Tooltip>
            )
            if (pane === "chat") {
              return (
                <div key={pane} className="flex items-center gap-1">
                  {navButton}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 shrink-0 text-muted-foreground hover:text-foreground"
                        onClick={(e) => { e.stopPropagation(); handleNewChat() }}
                        aria-label="New conversation"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="right">New conversation</TooltipContent>
                  </Tooltip>
                </div>
              )
            }
            return navButton
          })}

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
            </div>
            {historyExpanded && (
              <div className="min-h-0 flex-1">
                <ConversationList
                  conversations={visibleConversations}
                  activeId={activeId}
                  onSelect={handleSelectConversation}
                  onDelete={remove}
                  onArchive={archive}
                  onUnarchive={unarchive}
                  onRename={rename}
                  showArchived={showArchived}
                  archivedCount={archivedCount}
                  onToggleShowArchived={toggleShowArchived}
                  onBulkDelete={bulkDelete}
                  onBulkArchive={bulkArchive}
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

          {/* Tier toggle (dev/demo) — hidden in production builds */}
          {import.meta.env.DEV && onCycleTier && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  className={cn(
                    "flex w-full items-center rounded-md px-3 py-1.5 text-sm hover:bg-accent",
                    collapsed && "justify-center px-0"
                  )}
                  onClick={onCycleTier}
                  aria-label="Cycle feature tier"
                >
                  {!collapsed ? (
                    <>
                      <Shield className={cn("mr-2 h-3.5 w-3.5 shrink-0", TIER_COLORS[featureTier ?? "community"])} />
                      <span className={cn("flex-1 text-left text-xs font-medium", TIER_COLORS[featureTier ?? "community"])}>
                        {TIER_LABELS[featureTier ?? "community"] ?? "Core"}
                      </span>
                    </>
                  ) : (
                    <span className={cn("text-[10px] font-bold", TIER_COLORS[featureTier ?? "community"])}>
                      {(TIER_LABELS[featureTier ?? "community"] ?? "C")[0]}
                    </span>
                  )}
                </button>
              </TooltipTrigger>
              {collapsed && (
                <TooltipContent side="right">
                  Tier: {TIER_LABELS[featureTier ?? "community"]} — click to cycle
                </TooltipContent>
              )}
            </Tooltip>
          )}
        </div>
      </div>
    </TooltipProvider>
  )
}
