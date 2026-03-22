// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from "@tanstack/react-query"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchHealth, fetchProviderCredits } from "@/lib/api"
import { cn } from "@/lib/utils"

const SERVICE_INFO: Record<string, { purpose: string; tech: string }> = {
  chromadb: { purpose: "Vector embeddings & semantic search", tech: "ChromaDB" },
  redis: { purpose: "Cache, session state, pub/sub", tech: "Redis" },
  neo4j: { purpose: "Knowledge graph & relationships", tech: "Neo4j" },
}

export function StatusBar() {
  const { data: health, isError, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
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
                    <p className={cn(connected ? "text-green-400" : "text-red-400")}>
                      Status: {connected ? "Connected \u2713" : "Disconnected \u2717"}
                    </p>
                    <p className="text-muted-foreground">Last checked: {lastChecked}</p>
                  </TooltipContent>
                </Tooltip>
              )
            })}
          </div>
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
                  <p className="font-medium text-yellow-400">{credits.warning}</p>
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
