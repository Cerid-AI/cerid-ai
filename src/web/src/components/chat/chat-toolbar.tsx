// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Top toolbar for the chat panel — new-chat button, feature toggles (KB, verification,
 * feedback, dashboard, routing), overflow menu on narrow viewports, and model selector.
 *
 * Each feature toggle uses a primary click action + right-click context menu for settings.
 */

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import {
  ContextMenu, ContextMenuTrigger, ContextMenuContent,
  ContextMenuItem, ContextMenuCheckboxItem, ContextMenuRadioGroup,
  ContextMenuRadioItem, ContextMenuSeparator, ContextMenuLabel,
} from "@/components/ui/context-menu"
import { Plus, Database, Rss, LayoutDashboard, Zap, Shield, MoreVertical, Brain } from "lucide-react"
import { ModelSelect } from "./model-select"
import { cn } from "@/lib/utils"

interface ChatToolbarProps {
  isNarrow: boolean
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
  showKB, onToggleKB,
  autoInject, toggleAutoInject, autoInjectThreshold, setAutoInjectThreshold,
  hallucinationEnabled, toggleHallucinationEnabled,
  inlineMarkups, toggleInlineMarkups, onVerifyMessage,
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
        {/* KB toggle (right-click: auto-inject settings) */}
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-8 w-8", showKB && "text-green-500")}
                  onClick={onToggleKB}
                  aria-label={showKB ? "Hide knowledge context" : "Show knowledge context"}
                >
                  <Database className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {showKB ? "Hide knowledge context" : "Show knowledge context"}
              </TooltipContent>
            </Tooltip>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuCheckboxItem checked={autoInject} onCheckedChange={toggleAutoInject}>
              Auto-inject KB context
            </ContextMenuCheckboxItem>
            <ContextMenuSeparator />
            <ContextMenuLabel>Injection threshold</ContextMenuLabel>
            <ContextMenuRadioGroup
              value={String(autoInjectThreshold)}
              onValueChange={(v) => setAutoInjectThreshold(parseFloat(v))}
            >
              {[0.70, 0.80, 0.85, 0.90].map((t) => (
                <ContextMenuRadioItem key={t} value={String(t)}>
                  {Math.round(t * 100)}% relevance
                </ContextMenuRadioItem>
              ))}
            </ContextMenuRadioGroup>
          </ContextMenuContent>
        </ContextMenu>

        {/* Verification toggle (right-click: verify + inline markups) */}
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-8 w-8", hallucinationEnabled && "text-green-500")}
                  onClick={toggleHallucinationEnabled}
                  aria-label={hallucinationEnabled ? "Disable response verification" : "Enable response verification"}
                >
                  <Shield className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {hallucinationEnabled ? "Response verification: ON" : "Response verification: OFF"}
              </TooltipContent>
            </Tooltip>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuItem onClick={onVerifyMessage}>
              Verify last response
            </ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuCheckboxItem checked={inlineMarkups} onCheckedChange={toggleInlineMarkups}>
              Inline claim markups
            </ContextMenuCheckboxItem>
          </ContextMenuContent>
        </ContextMenu>

        {/* Wide viewport: inline buttons */}
        {!isNarrow && (
          <>
            {/* Feedback + Memory */}
            <ContextMenu>
              <ContextMenuTrigger asChild>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className={cn("h-8 w-8", feedbackLoop && "text-green-500")}
                      onClick={toggleFeedbackLoop}
                      aria-label={feedbackLoop ? "Disable feedback loop" : "Enable feedback loop"}
                    >
                      <Rss className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {feedbackLoop ? "Feedback loop: ON (responses saved to KB)" : "Feedback loop: OFF"}
                  </TooltipContent>
                </Tooltip>
              </ContextMenuTrigger>
              <ContextMenuContent>
                <ContextMenuCheckboxItem checked={feedbackLoop} onCheckedChange={toggleFeedbackLoop}>
                  Feedback loop
                </ContextMenuCheckboxItem>
                <ContextMenuCheckboxItem checked={memoryExtraction} onCheckedChange={toggleMemoryExtraction}>
                  Memory extraction
                </ContextMenuCheckboxItem>
              </ContextMenuContent>
            </ContextMenu>

            {/* Dashboard */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn("h-8 w-8", showDashboard && "text-green-500")}
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
            <ContextMenu>
              <ContextMenuTrigger asChild>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className={cn("h-8 w-8", routingMode !== "manual" && "text-green-500")}
                      onClick={cycleRoutingMode}
                      aria-label={`Smart routing: ${routingMode}`}
                    >
                      <Zap className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {routingMode === "manual" ? "Smart routing: OFF" : routingMode === "recommend" ? "Smart routing: Recommend" : "Smart routing: Auto"}
                  </TooltipContent>
                </Tooltip>
              </ContextMenuTrigger>
              <ContextMenuContent>
                <ContextMenuLabel>Routing mode</ContextMenuLabel>
                <ContextMenuRadioGroup value={routingMode} onValueChange={(v) => setRoutingMode(v as "manual" | "recommend" | "auto")}>
                  <ContextMenuRadioItem value="manual">Manual</ContextMenuRadioItem>
                  <ContextMenuRadioItem value="recommend">Recommend</ContextMenuRadioItem>
                  <ContextMenuRadioItem value="auto">Auto</ContextMenuRadioItem>
                </ContextMenuRadioGroup>
              </ContextMenuContent>
            </ContextMenu>
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
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", feedbackLoop && "text-green-500")}
                onClick={toggleFeedbackLoop}
              >
                <Rss className="h-4 w-4" />
                {feedbackLoop ? "Feedback: ON" : "Feedback: OFF"}
              </button>
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", memoryExtraction && "text-green-500")}
                onClick={toggleMemoryExtraction}
              >
                <Brain className="h-4 w-4" />
                {memoryExtraction ? "Memory: ON" : "Memory: OFF"}
              </button>
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", showDashboard && "text-green-500")}
                onClick={toggleDashboard}
              >
                <LayoutDashboard className="h-4 w-4" />
                {showDashboard ? "Dashboard: ON" : "Dashboard: OFF"}
              </button>
              <button
                className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent", routingMode !== "manual" && "text-green-500")}
                onClick={cycleRoutingMode}
              >
                <Zap className="h-4 w-4" />
                {routingMode === "manual" ? "Routing: Off" : routingMode === "recommend" ? "Routing: Suggest" : "Routing: Auto"}
              </button>
            </PopoverContent>
          </Popover>
        )}
      </TooltipProvider>
      <ModelSelect value={selectedModel} onChange={onModelChange} />
    </div>
  )
}
