// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Cpu, Coins, Binary, Database } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import type { ChatMessage } from "@/lib/types"
import { useLiveMetrics } from "@/hooks/use-live-metrics"
import { cn, tokenCost } from "@/lib/utils"

interface ChatDashboardProps {
  model: string
  messages: ChatMessage[]
  injectedCount: number
}

export function ChatDashboard({ model, messages, injectedCount }: ChatDashboardProps) {
  const { metrics, modelInfo } = useLiveMetrics(model, messages)
  const contextWindow = modelInfo?.contextWindow ?? 128_000
  const totalTokens = metrics.inputTokens + metrics.outputTokens
  const remaining = Math.max(contextWindow - totalTokens, 0)

  const inputCost = modelInfo ? tokenCost(metrics.inputTokens, modelInfo.inputCostPer1M) : 0
  const outputCost = modelInfo ? tokenCost(metrics.outputTokens, modelInfo.outputCostPer1M) : 0

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex flex-nowrap items-center gap-4 overflow-hidden border-b bg-muted/30 px-4 py-1.5 text-xs">
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex shrink-0 cursor-default items-center gap-1.5">
              <Cpu className="h-3 w-3 text-muted-foreground" />
              <span className="font-medium">{modelInfo?.label ?? "Unknown"}</span>
              <span className="hidden text-muted-foreground xl:inline">({modelInfo?.provider ?? "?"})</span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="space-y-1">
            <p className="font-medium">{modelInfo?.label ?? "Unknown"}</p>
            <p className="font-mono text-xs text-muted-foreground">{model}</p>
            <p className="text-muted-foreground">Provider: {modelInfo?.provider ?? "?"}</p>
            <p className="text-muted-foreground">Context: {formatContextWindow(contextWindow)} tokens</p>
            {modelInfo && (
              <>
                <p className="text-muted-foreground">Input: ${modelInfo.inputCostPer1M.toFixed(2)}/1M tokens</p>
                <p className="text-muted-foreground">Output: ${modelInfo.outputCostPer1M.toFixed(2)}/1M tokens</p>
              </>
            )}
          </TooltipContent>
        </Tooltip>

        <DashSeparator />

        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex shrink-0 cursor-default items-center gap-1.5">
              <Binary className="h-3 w-3 text-muted-foreground" />
              <span className="hidden tabular-nums xl:inline">~{totalTokens.toLocaleString()}</span>
              <span className="hidden text-muted-foreground xl:inline">/ {formatContextWindow(contextWindow)}</span>
              <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    metrics.contextPct < 50
                      ? "bg-green-500"
                      : metrics.contextPct < 80
                        ? "bg-yellow-500"
                        : "bg-red-500",
                  )}
                  style={{ width: `${Math.min(metrics.contextPct, 100)}%` }}
                  role="progressbar"
                  aria-valuenow={Math.round(metrics.contextPct)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Context window ${metrics.contextPct.toFixed(1)}% used`}
                />
              </div>
              <span
                className={cn(
                  "tabular-nums",
                  metrics.contextPct < 50
                    ? "text-green-600 dark:text-green-400"
                    : metrics.contextPct < 80
                      ? "text-yellow-600 dark:text-yellow-400"
                      : "text-red-600 dark:text-red-400",
                )}
              >
                {metrics.contextPct.toFixed(1)}%
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="space-y-1">
            <p className="font-medium">Token Usage</p>
            <p className="text-muted-foreground">Input: ~{metrics.inputTokens.toLocaleString()} tokens</p>
            <p className="text-muted-foreground">Output: ~{metrics.outputTokens.toLocaleString()} tokens</p>
            <p className="text-muted-foreground">Total: ~{totalTokens.toLocaleString()} tokens</p>
            <p className="text-muted-foreground">Remaining: ~{remaining.toLocaleString()} tokens</p>
            <p className="text-[10px] text-muted-foreground/80">When this reaches zero, older messages are summarized to free space. Your knowledge base is not affected.</p>
          </TooltipContent>
        </Tooltip>

        <DashSeparator />

        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex shrink-0 cursor-default items-center gap-1.5">
              <Coins className="h-3 w-3 text-muted-foreground" />
              <span className="tabular-nums">~${metrics.sessionCost.toFixed(4)}</span>
              <span className="hidden text-muted-foreground xl:inline">session</span>
              {metrics.messageCost > 0 && (
                <span className="hidden text-muted-foreground xl:inline">(last: ${metrics.messageCost.toFixed(4)})</span>
              )}
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="space-y-1">
            <p className="font-medium">Cost Breakdown</p>
            <p className="text-muted-foreground">Input cost: ${inputCost.toFixed(6)}</p>
            <p className="text-muted-foreground">Output cost: ${outputCost.toFixed(6)}</p>
            <p className="text-muted-foreground">Session total: ${metrics.sessionCost.toFixed(6)}</p>
            {metrics.messageCost > 0 && (
              <p className="text-muted-foreground">Last message: ${metrics.messageCost.toFixed(6)}</p>
            )}
          </TooltipContent>
        </Tooltip>

        <DashSeparator />

        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex shrink-0 cursor-default items-center gap-1.5">
              <Database className="h-3 w-3 text-muted-foreground" />
              <span className="tabular-nums">{injectedCount}</span>
              <span className="text-muted-foreground">
                {injectedCount === 1 ? "source" : "sources"} injected
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p className="font-medium">KB Sources</p>
            <p className="text-muted-foreground">
              {injectedCount} knowledge base {injectedCount === 1 ? "artifact" : "artifacts"} injected as context
            </p>
          </TooltipContent>
        </Tooltip>
      </div>
    </TooltipProvider>
  )
}

function DashSeparator() {
  return <div className="h-3 w-px shrink-0 bg-border" />
}

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(0)}M`
  return `${(tokens / 1_000).toFixed(0)}K`
}
