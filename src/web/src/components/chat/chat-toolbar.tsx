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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useEffect, useRef, useState, useCallback } from "react"
import { Plus, Database, Rss, LayoutDashboard, Zap, Shield, ShieldCheck, ShieldOff, MoreVertical, Brain, Check, Layers, ChevronDown, Lock, LockOpen, Menu } from "lucide-react"
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

function MenuRadioItem({
  children,
  checked,
  onClick,
  description,
  destructive = false,
}: {
  children: React.ReactNode
  checked: boolean
  onClick: () => void
  description?: string
  destructive?: boolean
}) {
  return (
    <button
      className={cn(
        "flex w-full items-start gap-2 rounded-sm py-1.5 pr-2 pl-7 text-sm outline-none select-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent relative",
        destructive && "text-red-500 hover:text-red-500 dark:text-red-400 dark:hover:text-red-400",
      )}
      onClick={onClick}
    >
      {checked && (
        <span className="absolute left-2 top-2 flex h-3.5 w-3.5 items-center justify-center">
          <span className="h-2 w-2 rounded-full bg-current" />
        </span>
      )}
      <span className="flex flex-col items-start text-left">
        <span className="leading-tight">{children}</span>
        {description && (
          <span className="text-label-xs text-muted-foreground">{description}</span>
        )}
      </span>
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
 * Small italic footnote at the base of a menu — used to clarify
 * data-route semantics under the privacy radio group without
 * cluttering each individual option's one-line description.
 */
function MenuFootnote({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-t mt-1 px-2 pt-2 text-label-xs italic text-muted-foreground leading-snug">
      {children}
    </div>
  )
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
  /** Provider IDs (lowercase) the user has configured. Passed through to
   *  <ModelSelect> so models from unconfigured providers render disabled
   *  with a "Not configured" hint (C-P1.5). Optional — when omitted,
   *  every model renders enabled (legacy behaviour). */
  configuredProviders?: string[]
  // Private Mode
  privateModeEnabled: boolean
  privateModeLevel: number
  togglePrivateMode: () => void
  changePrivateModeLevel: (level: number) => void
  // Actions
  onNewChat: () => void
  // Mobile navigation
  onOpenSidebar?: () => void
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
  configuredProviders,
  privateModeEnabled, privateModeLevel, togglePrivateMode, changePrivateModeLevel,
  onNewChat,
  onOpenSidebar,
}: ChatToolbarProps) {
  const cycleRagMode = useCallback(() => {
    const next: RagMode = ragMode === "manual" ? "smart" : ragMode === "smart" ? "custom_smart" : "manual"
    setRagMode(next)
  }, [ragMode, setRagMode])
  // L4 confirmation gate: full-ephemeral wipes the session on close, so
  // we intercept the level change and require an explicit confirm.
  const [pendingL4, setPendingL4] = useState(false)
  const requestPrivateLevel = useCallback(
    (level: 0 | 1 | 2 | 3 | 4) => {
      if (level === 4 && privateModeLevel !== 4) {
        setPendingL4(true)
        return
      }
      changePrivateModeLevel(level)
    },
    [changePrivateModeLevel, privateModeLevel],
  )
  // C-P2.6: persistent `animate-pulse` on L3/L4 private-mode is visually
  // exhausting. Fire a one-shot 3s pulse the moment the user activates the
  // higher tier, then revert to a static icon.
  const [privatePulse, setPrivatePulse] = useState(false)
  const prevPrivateLevelRef = useRef(privateModeLevel)
  useEffect(() => {
    const prev = prevPrivateLevelRef.current
    prevPrivateLevelRef.current = privateModeLevel
    if (privateModeLevel >= 3 && prev < 3) {
      setPrivatePulse(true)
      const id = setTimeout(() => setPrivatePulse(false), 3000)
      return () => clearTimeout(id)
    }
    if (privateModeLevel < 3 && privatePulse) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setPrivatePulse(false)
    }
  }, [privateModeLevel, privatePulse])

  const badgeBorderClass =
    privateModeLevel === 1
      ? "border-green-500/40 text-green-500"
      : privateModeLevel === 2
        ? "border-yellow-500/40 text-yellow-500"
        : privateModeLevel === 3
          ? "border-orange-500/40 text-orange-500"
          : privateModeLevel === 4
            ? "border-red-500/40 text-red-500"
            : "border-amber-500/40 text-amber-500"
  return (
    <div className="flex items-center gap-2 border-b px-4 py-2">
      {isNarrow && onOpenSidebar && (
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label="Open navigation"
                onClick={onOpenSidebar}
              >
                <Menu className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Navigation</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
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
            privateModeEnabled && privateModeLevel === 3 && cn("text-orange-500 hover:text-orange-500 bg-orange-500/10", privatePulse && "animate-pulse"),
            privateModeEnabled && privateModeLevel === 4 && cn("text-red-500 hover:text-red-500 bg-red-500/10", privatePulse && "animate-pulse"),
          )}
          menuContent={
            <>
              <MenuLabel>Privacy Level</MenuLabel>
              <MenuRadioItem
                checked={privateModeLevel === 0}
                onClick={() => requestPrivateLevel(0)}
                description="Standard behaviour. Conversations saved, KB used, audit logged."
              >
                Off
              </MenuRadioItem>
              <MenuRadioItem
                checked={privateModeLevel === 1}
                onClick={() => requestPrivateLevel(1)}
                description="Don't save this conversation; don't sync to other devices."
              >
                L1 — Skip saves &amp; sync
              </MenuRadioItem>
              <MenuRadioItem
                checked={privateModeLevel === 2}
                onClick={() => requestPrivateLevel(2)}
                description="Also bypass KB injection — model sees only what you type."
              >
                L2 — Also skip KB injection
              </MenuRadioItem>
              <MenuRadioItem
                checked={privateModeLevel === 3}
                onClick={() => requestPrivateLevel(3)}
                description="Also skip audit log entries. Nothing reaches Redis."
              >
                L3 — Also no logging
              </MenuRadioItem>
              <MenuRadioItem
                checked={privateModeLevel === 4}
                onClick={() => requestPrivateLevel(4)}
                description="One-shot per tab. Session is erased automatically on tab close — even the audit log is bypassed."
                destructive
              >
                L4 — Full ephemeral
              </MenuRadioItem>
              <MenuFootnote>
                Data routes: L0 persists to server + local cache. L1 keeps the
                local cache only. L2 also bypasses KB injection. L3 also skips
                Redis audit logs. L4 is in-memory only and disappears with the tab.
              </MenuFootnote>
            </>
          }
        />
      </TooltipProvider>
      {privateModeEnabled && !isNarrow && (
        privateModeLevel === 4 ? (
          // L4 needs a persistent reassurance that the wipe-on-close
          // contract is still in force — without it the badge looks
          // identical to L1-L3 once the AlertDialog has been dismissed,
          // and users have flagged uncertainty about whether the
          // ephemeral lifecycle actually triggers.
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="outline" className={cn("text-label-xs px-1.5 py-0 cursor-help", badgeBorderClass)}>
                Private · L4
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              This tab is in full-ephemeral mode. Closing the tab will erase the
              conversation, memory state, and any cached query results. No
              recovery path.
            </TooltipContent>
          </Tooltip>
        ) : (
          <Badge variant="outline" className={cn("text-label-xs px-1.5 py-0", badgeBorderClass)}>
            Private
          </Badge>
        )
      )}
      <AlertDialog open={pendingL4} onOpenChange={setPendingL4}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Switch to Level 4 (Full ephemeral)?</AlertDialogTitle>
            <AlertDialogDescription>
              Closing the tab will wipe this conversation, its memory state, and any
              cached query results from Redis. There is no recovery path — even the
              audit log is bypassed.
              <br />
              <br />
              Use this for highly sensitive one-off questions. For everyday privacy,
              L1–L3 cover most needs.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                changePrivateModeLevel(4)
                setPendingL4(false)
              }}
              className="bg-red-500 text-white hover:bg-red-600"
            >
              Enable L4
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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
              <MenuRadioItem checked={ragMode === "manual"} onClick={() => setRagMode("manual")}>
                <div className="flex flex-col gap-0.5">
                  <span>Manual — you pick docs</span>
                  <span className="text-label-xs font-normal text-muted-foreground">
                    Only documents you @mention or drag in are included.
                  </span>
                </div>
              </MenuRadioItem>
              <MenuRadioItem checked={ragMode === "smart"} onClick={() => setRagMode("smart")}>
                <div className="flex flex-col gap-0.5">
                  <span>Smart — auto-retrieval</span>
                  <span className="text-label-xs font-normal text-muted-foreground">
                    Searches your KB on every message and injects matches.
                  </span>
                </div>
              </MenuRadioItem>
              <MenuRadioItem checked={ragMode === "custom_smart"} onClick={() => setRagMode("custom_smart")}>
                <div className="flex flex-col gap-0.5">
                  <span className="flex items-center gap-1">
                    Custom
                    <Badge variant="outline" className="text-label-xxs ml-1 px-1 py-0">Advanced</Badge>
                  </span>
                  <span className="text-label-xs font-normal text-muted-foreground">
                    Tune source weights + thresholds manually.
                  </span>
                </div>
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
                  <span className="text-label-xxs text-muted-foreground font-normal">Check facts in the most recent AI response</span>
                </span>
              </MenuItem>
              <MenuSeparator />
              <MenuCheckboxItem checked={inlineMarkups} onCheckedChange={toggleInlineMarkups}>
                <span className="flex flex-col gap-0.5">
                  <span>Inline claim markups</span>
                  <span className="text-label-xxs text-muted-foreground font-normal">Highlight verified/unverified claims in message text</span>
                </span>
              </MenuCheckboxItem>
              <MenuSeparator />
              <MenuCheckboxItem checked={expertVerification} onCheckedChange={toggleExpertVerification}>
                <span className="flex flex-col gap-0.5">
                  <span className="flex items-center gap-1">
                    Expert verification
                  </span>
                  <span className="text-label-xxs text-muted-foreground font-normal">
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
                      <span className="text-label-xxs text-muted-foreground font-normal">Save AI responses to your KB for future retrieval</span>
                    </span>
                  </MenuCheckboxItem>
                  <MenuCheckboxItem checked={memoryExtraction} onCheckedChange={toggleMemoryExtraction}>
                    <span className="flex flex-col gap-0.5">
                      <span>Memory extraction</span>
                      <span className="text-label-xxs text-muted-foreground font-normal">Extract and remember key facts from conversations</span>
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
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent",
                  privateModeEnabled && privateModeLevel === 1 && "text-green-500 bg-green-500/10",
                  privateModeEnabled && privateModeLevel === 2 && "text-yellow-500 bg-yellow-500/10",
                  privateModeEnabled && privateModeLevel === 3 && "text-orange-500 bg-orange-500/10",
                  privateModeEnabled && privateModeLevel === 4 && "text-red-500 bg-red-500/10",
                )}
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
      <ModelSelect value={selectedModel} onChange={onModelChange} configuredProviders={configuredProviders} />
    </div>
  )
}
