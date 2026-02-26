import { useMemo } from "react"
import { Cpu, Coins, Binary, Database } from "lucide-react"
import { MODELS } from "@/lib/types"
import type { ChatMessage } from "@/lib/types"
import { estimateTokens } from "@/lib/utils"

interface ChatDashboardProps {
  model: string
  messages: ChatMessage[]
  injectedCount: number
}

export function ChatDashboard({ model, messages, injectedCount }: ChatDashboardProps) {
  const modelInfo = useMemo(() => MODELS.find((m) => m.id === model), [model])
  const { input, output } = useMemo(() => estimateTokens(messages), [messages])
  const totalTokens = input + output
  const contextWindow = modelInfo?.contextWindow ?? 128_000
  const usagePct = contextWindow > 0 ? (totalTokens / contextWindow) * 100 : 0

  const sessionCost = useMemo(() => {
    if (!modelInfo) return 0
    return (input * modelInfo.inputCostPer1M + output * modelInfo.outputCostPer1M) / 1_000_000
  }, [input, output, modelInfo])

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
        <span className="text-muted-foreground">tokens ({usagePct.toFixed(1)}%)</span>
        <div className="h-1 w-12 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.min(usagePct, 100)}%` }}
          />
        </div>
      </div>

      <Separator />

      {/* Cost */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Coins className="h-3 w-3 text-muted-foreground" />
        <span className="tabular-nums">~${sessionCost.toFixed(4)}</span>
        <span className="text-muted-foreground">session</span>
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
