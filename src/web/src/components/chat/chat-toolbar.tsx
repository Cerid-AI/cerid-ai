// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Top toolbar for the chat panel — new-chat button, feature toggles (KB, verification,
 * feedback, dashboard, routing), overflow menu on narrow viewports, and model selector.
 *
 * Each feature toggle uses two interactions:
 * - **Click** the icon to toggle the feature on/off
 * - **Click the chevron** (▾) to open a settings popover with detailed options
 *
 * The chevron provides a clear affordance that more options exist, and the popover
 * stays open until explicitly dismissed (click outside or select an option).
 */

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { useState, useCallback } from "react"
import { Plus, Database, Rss, LayoutDashboard, Zap, Shield, ShieldCheck, ShieldOff, MoreVertical, Brain, Check, Layers, ChevronDown, Lock, LockOpen } from "lucide-react"
import type { RagMode } from "@/lib/types"
import { ModelSelect } from "./model-select"
import { cn } from "@/lib/utils"

/* ── Reusable menu primitives (replaces ContextMenu items) ── */

function MenuItem({ children, onClick, className }: { children: React.ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button
      className={cn(
        "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none select-none",
        "hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent",
        "transition-colors",
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
  return <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">{children}</div>
}

function MenuSeparator() {
  return <Separator className="-mx-1 my-1.5" />
}

/**
 * Toolbar button with a companion chevron that opens a settings popover.
 *
 * - Click the **icon** to toggle the feature on/off
 * - Click the **chevron** (▾) to open the settings popover
 * - Popover stays open until dismissed (click outside or pick an option)
 *
 * The title prop shows as a header inside the popover for context.
 */
function ToolbarButtonWithMenu({
  icon,
  active,
  onClick,
  tooltip,
  ariaLabel,
  title,
  menuContent,
  className,
}: {
  icon: React.ReactNode
  active: boolean
  onClick: () => void
  tooltip: string
  ariaLabel: string
  title?: string
  menuContent: React.ReactNode
  className?: string
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative flex items-center">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={cn("h-8 w-8 rounded-r-none", active && "text-brand hover:text-brand bg-brand/10", className)}
            onClick={onClick}
            aria-label={ariaLabel}
          >
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-8 w-4 min-w-0 rounded-l-none border-l border-border/40 px-0",
              active && "text-brand hover:text-brand bg-brand/10",
            )}
            aria-label={`${ariaLabel} options`}
          >
            <ChevronDown className="h-3 w-3" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-56 p-2" align="start">
          {title && (
            <>
              <p className="px-2 pb-1.5 text-xs font-semibold">{title}</p>
              <MenuSeparator />
            </>
          )}
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
  verificationDegraded?: boolean
  verificationUnavailable?: boolean
  // Feedback + Memory
  feedbackLoop: boolean
  toggleFeedbackLoop: () => void
  memoryExtraction: boolean
  toggleMemoryExtraction: () => void
  // Dashboard
  showDashboard: boolean
  toggleDashboard: () => void
  // RAG mode
  ragMode: RagMode
  setRagMode: (mode: RagMode) => void
  // Routing
  routingMode: string
  setRoutingMode: (mode: "manual" | "recommend" | "auto") => void
  cycleRoutingMode: () => void
  // Model
  selectedModel: string
  onModelChange: (model: string) => void
  // Private Mode
  privateModeEnabled: boolean
  privateModeLevel: number
  togglePrivateMode: () => void
  changePrivateModeLevel: (level: number) => void
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
  verificationDegraded, verificationUnavailable,
  feedbackLoop, toggleFeedbackLoop,
  memoryExtraction, toggleMemoryExtraction,
  showDashboard, toggleDashboard,
  ragMode, setRagMode,
  routingMode, setRoutingMode, cycleRoutingMode,
  selectedModel, onModelChange,
  privateModeEnabled, privateModeLevel, togglePrivateMode, changePrivateModeLevel,
  onNewChat,
}: ChatToolbarProps) {
  const cycleRagMode = useCallback(() => {
    const next: RagMode = ragMode === "manual" ? "smart" : ragMode === "smart" ? "custom_smart" : "manual"
    setRagMode(next)
  }, [ragMode, setRagMode])
  return (
    <div className="flex items-center gap-2 border-b px-4 py-2">
      <Button variant="ghost" size="sm" onClick={onNewChat}>
        <Plus className="mr-1 h-4 w-4" />
        {!isNarrow && "New chat"}
      </Button>

      {/* Private Mode toggle */}
      <TooltipProvider delayDuration={0}>
        <ToolbarButtonWithMenu
          icon={privateModeEnabled ? <Lock className="h-4 w-4" /> : <LockOpen className="h-4 w-4" />}
          active={privateModeEnabled}
          onClick={togglePrivateMode}
          ariaLabel={privateModeEnabled ? "Disable private mode" : "Enable private mode"}
          title="Private Mode"
          tooltip={
            privateModeEnabled
              ? `Private mode: Level ${privateModeLevel} — ${["Off", "Skip saves & sync", "Also skip KB injection", "Also no logging", "Full ephemeral — nothing persisted"][privateModeLevel]}`
              : "Private mode: OFF — normal operation"
          }
          className={cn(
            privateModeEnabled && privateModeLevel === 1 && "text-green-500 hover:text-green-500 bg-green-500/10",
            privateModeEnabled && privateModeLevel === 2 && "text-yellow-500 hover:text-yellow-500 bg-yellow-500/10",
            privateModeEnabled && privateModeLevel === 3 && "text-orange-500 hover:text-orange-500 bg-orange-500/10 animate-pulse",
            privateModeEnabled && privateModeLevel === 4 && "text-red-500 hover:text-red-500 bg-red-500/10 animate-pulse",
          )}
          menuContent={
            <>
              <MenuLabel>Privacy Level</MenuLabel>
              <MenuRadioItem checked={privateModeLevel === 0} onClick={() => changePrivateModeLevel(0)}>
                Off — normal operation
              </MenuRadioItem>
              <MenuRadioItem checked={privateModeLevel === 1} onClick={() => changePrivateModeLevel(1)}>
                L1 — skip saves &amp; sync
              </MenuRadioItem>
              <MenuRadioItem checked={privateModeLevel === 2} onClick={() => changePrivateModeLevel(2)}>
                L2 — also skip KB injection
              </MenuRadioItem>
              <MenuRadioItem checked={privateModeLevel === 3} onClick={() => changePrivateModeLevel(3)}>
                L3 — also no logging
              </MenuRadioItem>
              <MenuRadioItem checked={privateModeLevel === 4} onClick={() => changePrivateModeLevel(4)}>
                L4 — full ephemeral, nothing persisted
              </MenuRadioItem>
            </>
          }
        />
      </TooltipProvider>
      {privateModeEnabled && !isNarrow && (
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-amber-500/40 text-amber-500">
          Private
        </Badge>
      )}

      <div className="flex-1" />
      <TooltipProvider delayDuration={0}>
        {/* Advanced-only toggles */}
        {!isSimple && (
        <>
        {/* RAG mode toggle */}
        <ToolbarButtonWithMenu
          icon={<Layers className="h-4 w-4" />}
          active={ragMode !== "manual"}
          onClick={cycleRagMode}
          ariaLabel={`RAG mode: ${ragMode}`}
          title="RAG Mode"
          tooltip={
            ragMode === "manual" ? "Manual: you control which docs are included"
              : ragMode === "smart" ? "Smart: automatically finds relevant docs + memories + external sources"
              : "Custom: fine-tune retrieval weights (Pro)"
          }
          menuContent={
            <>
              <MenuLabel>RAG Mode</MenuLabel>
              <MenuRadioItem checked={ragMode === "manual"} onClick={() => setRagMode("manual")}>Manual — you pick docs</MenuRadioItem>
              <MenuRadioItem checked={ragMode === "smart"} onClick={() => setRagMode("smart")}>Smart — auto-retrieval</MenuRadioItem>
              <MenuRadioItem checked={ragMode === "custom_smart"} onClick={() => setRagMode("custom_smart")}>
                <span className="flex items-center gap-1">
                  Custom
                  <Badge variant="outline" className="text-[9px] ml-1 px-1 py-0 text-gold">Pro</Badge>
                </span>
              </MenuRadioItem>
            </>
          }
        />

        {/* KB toggle + settings menu */}
        <ToolbarButtonWithMenu
          icon={<Database className="h-4 w-4" />}
          active={showKB}
          onClick={onToggleKB}
          ariaLabel={showKB ? "Hide knowledge context" : "Show knowledge context"}
          title="Knowledge Base"
          tooltip={showKB ? "Include relevant documents from your knowledge base in AI responses" : "Knowledge base context disabled — AI responds without your documents"}
          menuContent={
            <>
              <MenuCheckboxItem checked={autoInject} onCheckedChange={toggleAutoInject}>
                Auto-inject KB context
              </MenuCheckboxItem>
              <MenuSeparator />
              <MenuLabel>Injection threshold</MenuLabel>
              {[
                { value: 0.10, label: "Broad — include loosely related docs" },
                { value: 0.15, label: "Standard — balanced relevance" },
                { value: 0.25, label: "Focused — only highly relevant" },
                { value: 0.40, label: "Strict — exact matches only" },
              ].map((t) => (
                <MenuRadioItem key={t.value} checked={autoInjectThreshold === t.value} onClick={() => setAutoInjectThreshold(t.value)}>
                  {t.label}
                </MenuRadioItem>
              ))}
            </>
          }
        />

        {/* Verification toggle + settings menu */}
        <ToolbarButtonWithMenu
          icon={
            <>
              {hallucinationEnabled && verificationUnavailable && (
                <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-destructive" />
              )}
              {hallucinationEnabled && verificationDegraded && !verificationUnavailable && (
                <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-yellow-500" />
              )}
              {expertVerification && hallucinationEnabled && !verificationDegraded && !verificationUnavailable && (
                <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-amber-500" />
              )}
              {verificationUnavailable ? <ShieldOff className="h-4 w-4" /> : expertVerification && hallucinationEnabled ? <ShieldCheck className="h-4 w-4" /> : <Shield className="h-4 w-4" />}
            </>
          }
          active={hallucinationEnabled}
          onClick={toggleHallucinationEnabled}
          ariaLabel={
            verificationUnavailable
              ? "Verification enabled but temporarily unavailable"
              : hallucinationEnabled
                ? "Disable response verification"
                : "Enable response verification"
          }
          title="Verification"
          tooltip={
            verificationUnavailable
              ? "Fact-checking unavailable — verification services degraded"
              : verificationDegraded
                ? "Fact-checking active (single-model fallback)"
                : hallucinationEnabled
                  ? expertVerification
                    ? "Expert verification: claims verified against KB at no cost, then externally with advanced models"
                    : "Fact-check AI responses against your KB and external sources"
                  : "Fact-checking disabled — toggle to verify AI claims"
          }
          className="relative"
          menuContent={
            <>
              <MenuItem onClick={onVerifyMessage}>
                <span className="flex flex-col gap-0.5">
                  <span>Verify last response</span>
                  <span className="text-[9px] text-muted-foreground font-normal">Check facts in the most recent AI response</span>
                </span>
              </MenuItem>
              <MenuSeparator />
              <MenuCheckboxItem checked={inlineMarkups} onCheckedChange={toggleInlineMarkups}>
                <span className="flex flex-col gap-0.5">
                  <span>Inline claim markups</span>
                  <span className="text-[9px] text-muted-foreground font-normal">Highlight verified/unverified claims in message text</span>
                </span>
              </MenuCheckboxItem>
              <MenuSeparator />
              <MenuCheckboxItem checked={expertVerification} onCheckedChange={toggleExpertVerification}>
                <span className="flex flex-col gap-0.5">
                  <span className="flex items-center gap-1">
                    Expert verification
                  </span>
                  <span className="text-[9px] text-muted-foreground font-normal">
                    Uses advanced models for more thorough fact-checking
                  </span>
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
              title="Learning"
              tooltip={feedbackLoop ? "Learning: AI responses are saved back to your KB, improving future answers" : "Learning disabled — AI responses are not saved to your KB"}
              menuContent={
                <>
                  <MenuCheckboxItem checked={feedbackLoop} onCheckedChange={toggleFeedbackLoop}>
                    <span className="flex flex-col gap-0.5">
                      <span>Feedback loop</span>
                      <span className="text-[9px] text-muted-foreground font-normal">Save AI responses to your KB for future retrieval</span>
                    </span>
                  </MenuCheckboxItem>
                  <MenuCheckboxItem checked={memoryExtraction} onCheckedChange={toggleMemoryExtraction}>
                    <span className="flex flex-col gap-0.5">
                      <span>Memory extraction</span>
                      <span className="text-[9px] text-muted-foreground font-normal">Extract and remember key facts from conversations</span>
                    </span>
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
                {showDashboard ? "Hide token usage, response timing, and retrieval metrics" : "Show token usage, response timing, and retrieval metrics"}
              </TooltipContent>
            </Tooltip>

            {/* Routing */}
            <ToolbarButtonWithMenu
              icon={<Zap className="h-4 w-4" />}
              active={routingMode !== "manual"}
              onClick={cycleRoutingMode}
              ariaLabel={`Smart routing: ${routingMode}`}
              title="Model Routing"
              tooltip={routingMode === "manual" ? "Manual: you pick the model" : routingMode === "recommend" ? "Recommend: AI suggests optimal model" : "Auto: AI picks the best model for each query"}
              menuContent={
                <>
                  <MenuLabel>Routing mode</MenuLabel>
                  <MenuRadioItem checked={routingMode === "manual"} onClick={() => setRoutingMode("manual")}>Manual — you pick</MenuRadioItem>
                  <MenuRadioItem checked={routingMode === "recommend"} onClick={() => setRoutingMode("recommend")}>Recommend — AI suggests</MenuRadioItem>
                  <MenuRadioItem checked={routingMode === "auto"} onClick={() => setRoutingMode("auto")}>Auto — AI picks</MenuRadioItem>
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
            <PopoverContent className="w-48 p-2">
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", ragMode !== "manual" && "text-brand bg-brand/10")}
                onClick={cycleRagMode}
              >
                <Layers className="h-4 w-4" />
                {ragMode === "manual" ? "RAG: Manual" : ragMode === "smart" ? "RAG: Smart" : "RAG: Custom"}
              </button>
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
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", privateModeEnabled && "text-amber-500 bg-amber-500/10")}
                onClick={togglePrivateMode}
              >
                {privateModeEnabled ? <Lock className="h-4 w-4" /> : <LockOpen className="h-4 w-4" />}
                {privateModeEnabled ? `Private: L${privateModeLevel}` : "Private: Off"}
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
