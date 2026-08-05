// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useRef, useState, useCallback } from "react"
import {
  X,
  Trash2,
  ChevronDown,
  ChevronUp,
  Filter,
  Loader2,
  Radio,
  Search,
  Split,
  Combine,
  Inbox,
  Palette,
  SearchCheck,
  BookOpen,
  Receipt,
  Brush,
  Wrench,
  Cog,
  type LucideIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { AgentEvent } from "@/hooks/use-agent-console"

// ---------------------------------------------------------------------------
// Agent styling config
// ---------------------------------------------------------------------------

interface AgentStyle {
  icon: LucideIcon
  color: string     // Tailwind text color
  bgColor: string   // Tailwind bg color (for filter buttons)
  label: string
}

const AGENT_STYLES: Record<string, AgentStyle> = {
  query:        { icon: Search,      color: "text-teal-400",    bgColor: "bg-teal-500/20",    label: "Query" },
  decomposer:   { icon: Split,       color: "text-blue-400",    bgColor: "bg-blue-500/20",    label: "Decomposer" },
  assembler:    { icon: Combine,     color: "text-emerald-400", bgColor: "bg-emerald-500/20", label: "Assembler" },
  triage:       { icon: Inbox,       color: "text-amber-400",   bgColor: "bg-amber-500/20",   label: "Triage" },
  curator:      { icon: Palette,     color: "text-purple-400",  bgColor: "bg-purple-500/20",  label: "Curator" },
  verification: { icon: SearchCheck, color: "text-rose-400",    bgColor: "bg-rose-500/20",    label: "Verification" },
  memory:       { icon: BookOpen,    color: "text-cyan-400",    bgColor: "bg-cyan-500/20",    label: "Memory" },
  audit:        { icon: Receipt,     color: "text-yellow-400",  bgColor: "bg-yellow-500/20",  label: "Audit" },
  maintenance:  { icon: Brush,       color: "text-zinc-400",    bgColor: "bg-zinc-500/20",    label: "Maintenance" },
  rectify:      { icon: Wrench,      color: "text-orange-400",  bgColor: "bg-orange-500/20",  label: "Rectify" },
}

const DEFAULT_STYLE: AgentStyle = {
  icon: Cog,
  color: "text-zinc-400",
  bgColor: "bg-zinc-500/20",
  label: "Agent",
}

function getAgentStyle(agent: string): AgentStyle {
  return AGENT_STYLES[agent.toLowerCase()] ?? DEFAULT_STYLE
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

// ---------------------------------------------------------------------------
// Level indicator
// ---------------------------------------------------------------------------

function LevelDot({ level }: { level: string }) {
  return (
    <span
      role="img"
      aria-label={level}
      className={cn(
        "inline-block h-1.5 w-1.5 rounded-full flex-shrink-0",
        level === "success" && "bg-green-500",
        level === "warning" && "bg-yellow-500",
        level === "error" && "bg-red-500",
        level === "info" && "bg-zinc-500",
      )}
    />
  )
}

// ---------------------------------------------------------------------------
// Single event row
// ---------------------------------------------------------------------------

function EventRow({ event }: { event: AgentEvent }) {
  const style = getAgentStyle(event.agent)
  const Icon = style.icon
  return (
    <div className="flex items-start gap-2 px-3 py-1 hover:bg-zinc-800/50 transition-colors text-xs leading-relaxed">
      <span className="text-zinc-600 font-mono tabular-nums flex-shrink-0 select-none">
        {formatTime(event.timestamp)}
      </span>
      <LevelDot level={event.level} />
      <Icon className={cn("h-3 w-3 shrink-0", style.color)} aria-hidden="true" />
      <span className={cn("font-semibold flex-shrink-0", style.color)}>
        {style.label}
      </span>
      <span className="text-zinc-300 break-words min-w-0">
        {event.message}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface AgentConsoleProps {
  events: AgentEvent[]
  connected: boolean
  onClear: () => void
  onClose: () => void
}

export function AgentConsole({ events, connected, onClear, onClose }: AgentConsoleProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [hiddenAgents, setHiddenAgents] = useState<Set<string>>(new Set())
  const [showFilters, setShowFilters] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events, autoScroll])

  // Detect manual scroll (disable auto-scroll when user scrolls up)
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setAutoScroll(atBottom)
  }, [])

  const toggleAgent = useCallback((agent: string) => {
    setHiddenAgents((prev) => {
      const next = new Set(prev)
      if (next.has(agent)) {
        next.delete(agent)
      } else {
        next.add(agent)
      }
      return next
    })
  }, [])

  const filteredEvents = hiddenAgents.size > 0
    ? events.filter((e) => !hiddenAgents.has(e.agent.toLowerCase()))
    : events

  // Collect visible agents for filter buttons
  const seenAgents = new Set(events.map((e) => e.agent.toLowerCase()))

  return (
    <div className="border-t border-zinc-800 bg-zinc-950 flex flex-col transition-all duration-200">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900/80 border-b border-zinc-800 select-none">
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 transition-colors"
          aria-label={collapsed ? "Expand console" : "Collapse console"}
        >
          {collapsed ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          <span className="font-mono tracking-wide uppercase text-label-xs">Agent Console</span>
        </button>

        <span
          role="status"
          aria-label={connected ? "Live — connected to the agent stream" : "Disconnected — reconnecting"}
          className="flex items-center gap-1"
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              connected ? "bg-green-500" : "bg-red-500",
            )}
          />
          <span className="text-label-xs text-zinc-500">
            {connected ? "Live" : "Reconnecting…"}
          </span>
        </span>

        <span className="text-label-xs text-zinc-600 tabular-nums">
          {filteredEvents.length} events
        </span>

        <div className="ml-auto flex items-center gap-1">
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-zinc-500 hover:text-zinc-300"
                  aria-label="Filter agents"
                  onClick={() => setShowFilters((f) => !f)}
                >
                  <Filter className="h-3 w-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Filter agents</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-zinc-500 hover:text-zinc-300"
                  aria-label="Clear console"
                  onClick={onClear}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Clear console</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-zinc-500 hover:text-zinc-300"
                  aria-label="Close console"
                  onClick={onClose}
                >
                  <X className="h-3 w-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Close console</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </div>

      {/* Filter bar */}
      {showFilters && !collapsed && (
        <div className="flex flex-wrap gap-1 px-3 py-1.5 bg-zinc-900/50 border-b border-zinc-800">
          {Array.from(seenAgents).sort().map((agent) => {
            const style = getAgentStyle(agent)
            const Icon = style.icon
            const isHidden = hiddenAgents.has(agent)
            return (
              <button
                key={agent}
                onClick={() => toggleAgent(agent)}
                className={cn(
                  "flex items-center gap-1 rounded px-2 py-0.5 text-label-xs font-medium transition-colors",
                  isHidden
                    ? "bg-zinc-800 text-zinc-600 line-through"
                    : `${style.bgColor} ${style.color}`,
                )}
              >
                <Icon className="h-3 w-3 shrink-0" aria-hidden="true" /> {style.label}
              </button>
            )
          })}
        </div>
      )}

      {/* Event list */}
      {!collapsed && (
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="overflow-y-auto font-mono"
          style={{ maxHeight: 200, minHeight: 80 }}
        >
          {!connected && events.length === 0 ? (
            <div className="flex items-center justify-center gap-1.5 h-20 text-xs text-zinc-600">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              Connecting to agent stream…
            </div>
          ) : filteredEvents.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-0.5 h-20 text-xs text-zinc-600">
              <Radio className="h-3.5 w-3.5 mb-1" aria-hidden="true" />
              <span>No agent activity yet</span>
              <span className="text-zinc-700">Agent events stream here as queries are processed.</span>
            </div>
          ) : (
            filteredEvents.map((event) => (
              <EventRow key={event.id} event={event} />
            ))
          )}
        </div>
      )}
    </div>
  )
}
