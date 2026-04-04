// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from "@tanstack/react-query"
import { Terminal } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchHealthStatus, fetchProviderCredits } from "@/lib/api"
import { cn } from "@/lib/utils"

const SERVICE_INFO: Record<string, { purpose: string; tech: string }> = {
  chromadb: { purpose: "Vector embeddings & semantic search", tech: "ChromaDB" },
  redis: { purpose: "Cache, session state, pub/sub", tech: "Redis" },
  neo4j: { purpose: "Knowledge graph & relationships", tech: "Neo4j" },
}

interface StatusBarProps {
  consoleOpen?: boolean
  onToggleConsole?: () => void
  consoleUnreadCount?: number
}

export function StatusBar({ consoleOpen, onToggleConsole, consoleUnreadCount = 0 }: StatusBarProps) {
  const { data: health, isError, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealthStatus,
    refetchInterval: 15_000,
    retry: 1,
  })

  const { data: credits } = useQuery({
    queryKey: ["provider-credits"],
    queryFn: fetchProviderCredits,
    refetchInterval: 60_000,
    retry: 1,
    staleTime: 30_000,
  })

  const status = isLoading ? "loading" : isError ? "error" : health?.status ?? "unknown"
  const lastChecked = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : "—"

  const services = health?.services
  const connectedCount = services
    ? Object.values(services).filter((s) => s === "connected").length
    : 0
  const totalCount = services ? Object.keys(services).length : 0

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-8 items-center gap-4 border-t bg-muted/40 px-4 text-xs text-muted-foreground">
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex cursor-default items-center gap-1.5">
              <div
                className={cn(
                  "h-2 w-2 rounded-full",
                  status === "healthy" && "bg-green-500",
                  status === "degraded" && "bg-yellow-500",
                  status === "loading" && "bg-muted-foreground/50",
                  (status === "error" || status === "unknown") && "bg-red-500"
                )}
                aria-hidden="true"
              />
              <span>
                {status === "healthy" && "All systems operational"}
                {status === "degraded" && "Some services degraded"}
                {status === "error" && "Connection error"}
                {status === "loading" && "Checking..."}
                {status === "unknown" && "Unknown status"}
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="top" className="space-y-1">
            <p className="font-medium">System Status: {status}</p>
            {services && (
              <p className="text-muted-foreground">
                {connectedCount}/{totalCount} services connected
              </p>
            )}
            <p className="text-muted-foreground">Last checked: {lastChecked}</p>
          </TooltipContent>
        </Tooltip>

        {health?.degradation_tier && health.degradation_tier !== "full" && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
                health.degradation_tier === "lite" && "bg-yellow-500/20 text-yellow-400",
                health.degradation_tier === "direct" && "bg-orange-500/20 text-orange-400",
                health.degradation_tier === "cached" && "bg-red-500/20 text-red-400",
                health.degradation_tier === "offline" && "bg-red-500/30 text-red-300 animate-pulse",
              )}>
                {health.degradation_tier}
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              <p className="font-medium">Degraded: {health.degradation_tier} tier</p>
              <div className="mt-1 space-y-0.5 text-xs">
                <p>Retrieve: {health.can_retrieve ? "\u2713" : "\u2717"}</p>
                <p>Verify: {health.can_verify ? "\u2713" : "\u2717"}</p>
                <p>Generate: {health.can_generate ? "\u2713" : "\u2717"}</p>
              </div>
              {health.pipeline_providers && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {Object.values(health.pipeline_providers).filter(p => p === "ollama").length}/
                  {Object.values(health.pipeline_providers).length} stages local
                </p>
              )}
            </TooltipContent>
          </Tooltip>
        )}

        {services && (
          <div className="flex items-center gap-3">
            {Object.entries(services).map(([name, state]) => {
              const info = SERVICE_INFO[name]
              const connected = state === "connected"
              return (
                <Tooltip key={name}>
                  <TooltipTrigger asChild>
                    <span className={cn("cursor-default", !connected && "text-destructive")}>
                      {name}: {state}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="space-y-1">
                    <p className="font-medium">{info?.tech ?? name}</p>
                    {info && <p className="text-muted-foreground">{info.purpose}</p>}
                    <p className={cn(connected ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400")}>
                      Status: {connected ? "Connected \u2713" : "Disconnected \u2717"}
                    </p>
                    <p className="text-muted-foreground">Last checked: {lastChecked}</p>
                  </TooltipContent>
                </Tooltip>
              )
            })}
          </div>
        )}

        {/* Ollama / pipeline indicator */}
        {health?.pipeline_providers && (() => {
          const localCount = Object.values(health.pipeline_providers).filter(p => p === "ollama").length
          const totalStages = Object.values(health.pipeline_providers).length
          if (localCount > 0) {
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="flex items-center gap-1 text-[10px] text-green-600 dark:text-green-400">
                    <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
                    Ollama: {health.internal_llm_model || "active"} ({localCount}/{totalStages} local)
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" className="space-y-1">
                  <p className="font-medium">Ollama — Local LLM</p>
                  <p className="text-muted-foreground">{localCount} of {totalStages} pipeline stages running locally ($0)</p>
                  <p className="text-muted-foreground">Model: {health.internal_llm_model || "configured"}</p>
                </TooltipContent>
              </Tooltip>
            )
          }
          return (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-[10px] text-yellow-500/70">&#x26A1; 0 local</span>
              </TooltipTrigger>
              <TooltipContent side="top">
                <p>All pipeline stages use cloud APIs. Enable Ollama for faster local processing.</p>
              </TooltipContent>
            </Tooltip>
          )
        })()}

        {/* Agent Console toggle */}
        {onToggleConsole && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={onToggleConsole}
                className={cn(
                  "relative flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors",
                  consoleOpen
                    ? "bg-teal-500/20 text-teal-400"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Terminal className="h-3 w-3" />
                <span className="hidden sm:inline">Console</span>
                {!consoleOpen && consoleUnreadCount > 0 && (
                  <span className="absolute -right-1 -top-1 flex h-3.5 min-w-[14px] animate-pulse items-center justify-center rounded-full bg-teal-500 px-1 text-[9px] font-bold text-white">
                    {consoleUnreadCount > 99 ? "99+" : consoleUnreadCount}
                  </span>
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top">
              {consoleOpen ? "Close agent console" : "Open agent console"}
            </TooltipContent>
          </Tooltip>
        )}

        {/* Credits indicator — pushed to the right */}
        {credits?.configured && credits.balance != null && (
          <div className="ml-auto">
            <Tooltip>
              <TooltipTrigger asChild>
                <a
                  href={credits.top_up_url ?? "https://openrouter.ai/settings/credits"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn(
                    "cursor-pointer font-medium tabular-nums transition-colors hover:underline",
                    credits.status === "ok" && "text-green-600 dark:text-green-400",
                    credits.status === "low" && "text-yellow-600 dark:text-yellow-400",
                    credits.status === "exhausted" && "text-red-600 dark:text-red-400",
                    credits.status === "error" && "text-muted-foreground",
                  )}
                >
                  {credits.status === "exhausted" ? (
                    "Credits exhausted"
                  ) : (
                    `$${credits.balance.toFixed(2)}`
                  )}
                </a>
              </TooltipTrigger>
              <TooltipContent side="top" className="space-y-1">
                <p className="font-medium">OpenRouter Credits</p>
                <p className="text-muted-foreground">Balance: ${credits.balance?.toFixed(2)}</p>
                {credits.usage_daily != null && (
                  <p className="text-muted-foreground">Today: ${credits.usage_daily.toFixed(4)}</p>
                )}
                {credits.usage_weekly != null && (
                  <p className="text-muted-foreground">This week: ${credits.usage_weekly.toFixed(2)}</p>
                )}
                {credits.usage_monthly != null && (
                  <p className="text-muted-foreground">This month: ${credits.usage_monthly.toFixed(2)}</p>
                )}
                {credits.warning && (
                  <p className="font-medium text-amber-600 dark:text-yellow-400">{credits.warning}</p>
                )}
                <p className="text-muted-foreground">Click to add credits</p>
              </TooltipContent>
            </Tooltip>
          </div>
        )}
      </div>
    </TooltipProvider>
  )
}
