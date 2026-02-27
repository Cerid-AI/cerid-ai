// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Cpu, Coins, Binary, Database } from "lucide-react"
import type { ChatMessage } from "@/lib/types"
import { useLiveMetrics } from "@/hooks/use-live-metrics"

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
    <div className="flex items-center gap-4 overflow-x-auto border-b bg-muted/30 px-4 py-1.5 text-xs">
      {/* Model */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Cpu className="h-3 w-3 text-muted-foreground" />
        <span className="font-medium">{modelInfo?.label ?? "Unknown"}</span>
        <span className="text-muted-foreground">
          ({modelInfo?.provider ?? "?"}) {formatContextWindow(contextWindow)} ctx
        </span>
      </div>

      <Separator />

      {/* Tokens */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Binary className="h-3 w-3 text-muted-foreground" />
        <span className="tabular-nums">~{totalTokens.toLocaleString()}</span>
        <span className="text-muted-foreground">tokens ({metrics.contextPct.toFixed(1)}%)</span>
        <div className="h-1 w-12 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.min(metrics.contextPct, 100)}%` }}
          />
        </div>
      </div>

      <Separator />

      {/* Cost */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Coins className="h-3 w-3 text-muted-foreground" />
        <span className="tabular-nums">~${metrics.sessionCost.toFixed(4)}</span>
        <span className="text-muted-foreground">session</span>
        {metrics.messageCost > 0 && (
          <span className="text-muted-foreground">(last: ${metrics.messageCost.toFixed(4)})</span>
        )}
      </div>

      <Separator />

      {/* KB Context */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Database className="h-3 w-3 text-muted-foreground" />
        <span className="tabular-nums">{injectedCount}</span>
        <span className="text-muted-foreground">
          {injectedCount === 1 ? "source" : "sources"} injected
        </span>
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