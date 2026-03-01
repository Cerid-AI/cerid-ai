// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Cpu, Coins, Binary, Database } from "lucide-react"
import type { ChatMessage } from "@/lib/types"
import { useLiveMetrics } from "@/hooks/use-live-metrics"
import { cn } from "@/lib/utils"

interface ChatDashboardProps {
  model: string
  messages: ChatMessage[]
  injectedCount: number
}

export function ChatDashboard({ model, messages, injectedCount }: ChatDashboardProps) {
  const { metrics, modelInfo } = useLiveMetrics(model, messages)
  const contextWindow = modelInfo?.contextWindow ?? 128_000
  const totalTokens = metrics.inputTokens + metrics.outputTokens

  return (
    <div className="border-b bg-muted/30 px-4 py-1.5 text-xs">
      {/* Row 1: Model + Context */}
      <div className="flex items-center gap-4">
        <div className="flex shrink-0 items-center gap-1.5">
          <Cpu className="h-3 w-3 text-muted-foreground" />
          <span className="font-medium">{modelInfo?.label ?? "Unknown"}</span>
          <span className="text-muted-foreground">({modelInfo?.provider ?? "?"})</span>
        </div>

        <Separator />

        <div className="flex shrink-0 items-center gap-1.5">
          <Binary className="h-3 w-3 text-muted-foreground" />
          <span className="tabular-nums">~{totalTokens.toLocaleString()}</span>
          <span className="text-muted-foreground">/ {formatContextWindow(contextWindow)}</span>
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
      </div>

      {/* Row 2: Cost + KB */}
      <div className="mt-1 flex items-center gap-4">
        <div className="flex shrink-0 items-center gap-1.5">
          <Coins className="h-3 w-3 text-muted-foreground" />
          <span className="tabular-nums">~${metrics.sessionCost.toFixed(4)}</span>
          <span className="text-muted-foreground">session</span>
          {metrics.messageCost > 0 && (
            <span className="text-muted-foreground">(last: ${metrics.messageCost.toFixed(4)})</span>
          )}
        </div>

        <Separator />

        <div className="flex shrink-0 items-center gap-1.5">
          <Database className="h-3 w-3 text-muted-foreground" />
          <span className="tabular-nums">{injectedCount}</span>
          <span className="text-muted-foreground">
            {injectedCount === 1 ? "source" : "sources"} injected
          </span>
        </div>
      </div>
    </div>
  )
}

function Separator() {
  return <div className="h-3 w-px shrink-0 bg-border" />
}

function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(0)}M`
  return `${(tokens / 1_000).toFixed(0)}K`
}