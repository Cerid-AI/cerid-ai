// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Top toolbar for the chat panel — new-chat button, feature toggles (KB, verification,
 * feedback, dashboard, routing), overflow menu on narrow viewports, and model selector.
 *
 * Each feature toggle uses a primary click action + a settings Popover that opens
 * automatically after a 2-second hover (desktop) or 500ms long-press (touch).
 * No separate trigger button — the main button itself controls both actions.
 */

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { useRef, useEffect, useState, useCallback } from "react"
import { Plus, Database, Rss, LayoutDashboard, Zap, Shield, ShieldCheck, MoreVertical, Brain, Check } from "lucide-react"
import { ModelSelect } from "./model-select"
import { cn } from "@/lib/utils"

/* ── Reusable menu primitives (replaces ContextMenu items) ── */

function MenuItem({ children, onClick, className }: { children: React.ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button
      className={cn(
        "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none select-none",
        "hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent",
        className,
      )}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

function MenuCheckboxItem({ children, checked, onCheckedChange }: { children: React.ReactNode; checked: boolean; onCheckedChange: () => void }) {
  return (
    <button
      className="flex w-full items-center gap-2 rounded-sm py-1.5 pr-2 pl-7 text-sm outline-none select-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent relative"
      onClick={onCheckedChange}
    >
      {checked && (
        <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
          <Check className="h-4 w-4" />
        </span>
      )}
      {children}
    </button>
  )
}

function MenuRadioItem({ children, checked, onClick }: { children: React.ReactNode; checked: boolean; onClick: () => void }) {
  return (
    <button
      className="flex w-full items-center gap-2 rounded-sm py-1.5 pr-2 pl-7 text-sm outline-none select-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent relative"
      onClick={onClick}
    >
      {checked && (
        <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
          <span className="h-2 w-2 rounded-full bg-current" />
        </span>
      )}
      {children}
    </button>
  )
}

function MenuLabel({ children }: { children: React.ReactNode }) {
  return <div className="px-2 py-1.5 text-xs text-muted-foreground">{children}</div>
}

function MenuSeparator() {
  return <Separator className="-mx-1 my-1" />
}

/** Toolbar button with a settings popover that opens on 2-second hover (desktop) or long-press (touch). */
function ToolbarButtonWithMenu({
  icon,
  active,
  onClick,
  tooltip,
  ariaLabel,
  menuContent,
  className,
}: {
  icon: React.ReactNode
  active: boolean
  onClick: () => void
  tooltip: string
  ariaLabel: string
  menuContent: React.ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const touchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const touchFiredRef = useRef(false)

  const clearHoverTimer = useCallback(() => {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current)
      hoverTimerRef.current = null
    }
  }, [])

  const clearTouchTimer = useCallback(() => {
    if (touchTimerRef.current) {
      clearTimeout(touchTimerRef.current)
      touchTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      clearHoverTimer()
      clearTouchTimer()
    }
  }, [clearHoverTimer, clearTouchTimer])

  const handleButtonMouseEnter = () => {
    clearHoverTimer()
    hoverTimerRef.current = setTimeout(() => {
      setOpen(true)
    }, 2000)
  }

  const handleWrapperMouseLeave = () => {
    clearHoverTimer()
    setOpen(false)
  }

  const handleClick = () => {
    clearHoverTimer()
    onClick()
  }

  const handleTouchStart = () => {
    touchFiredRef.current = false
    clearTouchTimer()
    touchTimerRef.current = setTimeout(() => {
      touchFiredRef.current = true
      setOpen(true)
    }, 500)
  }

  const handleTouchEnd = (e: React.TouchEvent) => {
    clearTouchTimer()
    if (touchFiredRef.current) {
      // Long-press opened the menu — prevent the click from also firing
      e.preventDefault()
    }
  }

  return (
    <div className="relative flex items-center" onMouseLeave={handleWrapperMouseLeave}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <span>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-8 w-8", active && "text-brand hover:text-brand bg-brand/10", className)}
                  onClick={handleClick}
                  onMouseEnter={handleButtonMouseEnter}
                  onTouchStart={handleTouchStart}
                  onTouchEnd={handleTouchEnd}
                  aria-label={ariaLabel}
                >
                  {icon}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{tooltip}</TooltipContent>
            </Tooltip>
          </span>
        </PopoverTrigger>
        <PopoverContent className="w-52">
          {menuContent}
        </PopoverContent>
      </Popover>
    </div>
  )
}

interface ChatToolbarProps {
  isNarrow: boolean
  isSimple?: boolean
  // KB
  showKB: boolean
  onToggleKB: () => void
  autoInject: boolean
  toggleAutoInject: () => void
  autoInjectThreshold: number
  setAutoInjectThreshold: (v: number) => void
  // Verification
  hallucinationEnabled: boolean
  toggleHallucinationEnabled: () => void
  inlineMarkups: boolean
  toggleInlineMarkups: () => void
  expertVerification: boolean
  toggleExpertVerification: () => void
  onVerifyMessage: () => void
  // Feedback + Memory
  feedbackLoop: boolean
  toggleFeedbackLoop: () => void
  memoryExtraction: boolean
  toggleMemoryExtraction: () => void
  // Dashboard
  showDashboard: boolean
  toggleDashboard: () => void
  // Routing
  routingMode: string
  setRoutingMode: (mode: "manual" | "recommend" | "auto") => void
  cycleRoutingMode: () => void
  // Model
  selectedModel: string
  onModelChange: (model: string) => void
  // Actions
  onNewChat: () => void
}

export function ChatToolbar({
  isNarrow,
  isSimple,
  showKB, onToggleKB,
  autoInject, toggleAutoInject, autoInjectThreshold, setAutoInjectThreshold,
  hallucinationEnabled, toggleHallucinationEnabled,
  inlineMarkups, toggleInlineMarkups, expertVerification, toggleExpertVerification, onVerifyMessage,
  feedbackLoop, toggleFeedbackLoop,
  memoryExtraction, toggleMemoryExtraction,
  showDashboard, toggleDashboard,
  routingMode, setRoutingMode, cycleRoutingMode,
  selectedModel, onModelChange,
  onNewChat,
}: ChatToolbarProps) {
  return (
    <div className="flex items-center gap-2 border-b px-4 py-2">
      <Button variant="ghost" size="sm" onClick={onNewChat}>
        <Plus className="mr-1 h-4 w-4" />
        {!isNarrow && "New chat"}
      </Button>
      <div className="flex-1" />
      <TooltipProvider delayDuration={0}>
        {/* Advanced-only toggles */}
        {!isSimple && (
        <>
        {/* KB toggle + settings menu */}
        <ToolbarButtonWithMenu
          icon={<Database className="h-4 w-4" />}
          active={showKB}
          onClick={onToggleKB}
          ariaLabel={showKB ? "Hide knowledge context" : "Show knowledge context"}
          tooltip={showKB ? "Hide knowledge context" : "Show knowledge context"}
          menuContent={
            <>
              <MenuCheckboxItem checked={autoInject} onCheckedChange={toggleAutoInject}>
                Auto-inject KB context
              </MenuCheckboxItem>
              <MenuSeparator />
              <MenuLabel>Injection threshold</MenuLabel>
              {[0.70, 0.80, 0.85, 0.90].map((t) => (
                <MenuRadioItem key={t} checked={autoInjectThreshold === t} onClick={() => setAutoInjectThreshold(t)}>
                  {Math.round(t * 100)}% relevance
                </MenuRadioItem>
              ))}
            </>
          }
        />

        {/* Verification toggle + settings menu */}
        <ToolbarButtonWithMenu
          icon={
            <>
              {expertVerification && hallucinationEnabled && (
                <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-amber-500" />
              )}
              {expertVerification && hallucinationEnabled ? <ShieldCheck className="h-4 w-4" /> : <Shield className="h-4 w-4" />}
            </>
          }
          active={hallucinationEnabled}
          onClick={toggleHallucinationEnabled}
          ariaLabel={hallucinationEnabled ? "Disable response verification" : "Enable response verification"}
          tooltip={
            hallucinationEnabled
              ? expertVerification
                ? "Expert verification: ON"
                : "Response verification: ON"
              : "Response verification: OFF"
          }
          className="relative"
          menuContent={
            <>
              <MenuItem onClick={onVerifyMessage}>
                Verify last response
              </MenuItem>
              <MenuSeparator />
              <MenuCheckboxItem checked={inlineMarkups} onCheckedChange={toggleInlineMarkups}>
                Inline claim markups
              </MenuCheckboxItem>
              <MenuSeparator />
              <MenuCheckboxItem checked={expertVerification} onCheckedChange={toggleExpertVerification}>
                <span className="flex items-center gap-1">
                  Expert verification (Grok 4)
                  <Badge variant="outline" className="text-[9px] ml-1 px-1 py-0 text-amber-500">~15x cost</Badge>
                </span>
              </MenuCheckboxItem>
            </>
          }
        />

        {/* Wide viewport: inline buttons */}
        {!isNarrow && (
          <>
            {/* Feedback + Memory */}
            <ToolbarButtonWithMenu
              icon={<Rss className="h-4 w-4" />}
              active={feedbackLoop}
              onClick={toggleFeedbackLoop}
              ariaLabel={feedbackLoop ? "Disable feedback loop" : "Enable feedback loop"}
              tooltip={feedbackLoop ? "Feedback loop: ON (responses saved to KB)" : "Feedback loop: OFF"}
              menuContent={
                <>
                  <MenuCheckboxItem checked={feedbackLoop} onCheckedChange={toggleFeedbackLoop}>
                    Feedback loop
                  </MenuCheckboxItem>
                  <MenuCheckboxItem checked={memoryExtraction} onCheckedChange={toggleMemoryExtraction}>
                    Memory extraction
                  </MenuCheckboxItem>
                </>
              }
            />

            {/* Dashboard */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-8 w-8", showDashboard && "text-brand hover:text-brand bg-brand/10")}
                  onClick={toggleDashboard}
                  aria-label={showDashboard ? "Hide metrics dashboard" : "Show metrics dashboard"}
                >
                  <LayoutDashboard className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {showDashboard ? "Hide metrics dashboard" : "Show metrics dashboard"}
              </TooltipContent>
            </Tooltip>

            {/* Routing */}
            <ToolbarButtonWithMenu
              icon={<Zap className="h-4 w-4" />}
              active={routingMode !== "manual"}
              onClick={cycleRoutingMode}
              ariaLabel={`Smart routing: ${routingMode}`}
              tooltip={routingMode === "manual" ? "Smart routing: OFF" : routingMode === "recommend" ? "Smart routing: Recommend" : "Smart routing: Auto"}
              menuContent={
                <>
                  <MenuLabel>Routing mode</MenuLabel>
                  <MenuRadioItem checked={routingMode === "manual"} onClick={() => setRoutingMode("manual")}>Manual</MenuRadioItem>
                  <MenuRadioItem checked={routingMode === "recommend"} onClick={() => setRoutingMode("recommend")}>Recommend</MenuRadioItem>
                  <MenuRadioItem checked={routingMode === "auto"} onClick={() => setRoutingMode("auto")}>Auto</MenuRadioItem>
                </>
              }
            />
          </>
        )}

        {/* Narrow viewport: overflow menu */}
        {isNarrow && (
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="More options">
                <MoreVertical className="h-4 w-4" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-48">
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", feedbackLoop && "text-brand bg-brand/10")}
                onClick={toggleFeedbackLoop}
              >
                <Rss className="h-4 w-4" />
                {feedbackLoop ? "Feedback: ON" : "Feedback: OFF"}
              </button>
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", memoryExtraction && "text-brand bg-brand/10")}
                onClick={toggleMemoryExtraction}
              >
                <Brain className="h-4 w-4" />
                {memoryExtraction ? "Memory: ON" : "Memory: OFF"}
              </button>
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", showDashboard && "text-brand bg-brand/10")}
                onClick={toggleDashboard}
              >
                <LayoutDashboard className="h-4 w-4" />
                {showDashboard ? "Dashboard: ON" : "Dashboard: OFF"}
              </button>
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", routingMode !== "manual" && "text-brand bg-brand/10")}
                onClick={cycleRoutingMode}
              >
                <Zap className="h-4 w-4" />
                {routingMode === "manual" ? "Routing: Off" : routingMode === "recommend" ? "Routing: Suggest" : "Routing: Auto"}
              </button>
            </PopoverContent>
          </Popover>
        )}
        </>
        )}
      </TooltipProvider>
      <ModelSelect value={selectedModel} onChange={onModelChange} />
    </div>
  )
}
