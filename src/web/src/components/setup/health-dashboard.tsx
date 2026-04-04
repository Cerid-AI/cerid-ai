// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useRef } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Loader2, Info, Copy, RefreshCw } from "lucide-react"
import { fetchSetupHealth } from "@/lib/api"
import type { SetupHealth, SetupServiceHealth } from "@/lib/types"

const MCP_BASE = import.meta.env.VITE_MCP_URL || "http://localhost:8888"

interface HealthDashboardProps {
  polling?: boolean
  interval?: number
  onAllHealthy?: () => void
  lightweightMode?: boolean
  /** Provider IDs that have API keys configured (e.g., ["openrouter", "anthropic"]) */
  configuredProviders?: string[]
}

const SERVICE_META: Record<string, { label: string; port: number; description: string; optional?: boolean; tooltip?: string; fixAction?: string }> = {
  neo4j: { label: "Neo4j (Graph DB)", port: 7687, description: "Graph relationships between documents", tooltip: "Tracks relationships between your documents — which topics connect to which sources", fixAction: "docker compose up neo4j -d" },
  chromadb: { label: "ChromaDB (Vectors)", port: 8001, description: "Semantic search over your knowledge", tooltip: "Stores document embeddings for fast semantic search — finds relevant content even when wording differs", fixAction: "docker compose up chromadb -d" },
  redis: { label: "Redis (Cache)", port: 6379, description: "Query cache and audit log", tooltip: "Speeds up repeated queries and stores your conversation audit trail", fixAction: "docker compose up redis -d" },
  mcp: { label: "MCP Server (API)", port: 8888, description: "Core API — powers everything", tooltip: "The brain of Cerid — processes queries, manages your KB, and coordinates all services", fixAction: "Check Docker Desktop is running" },
  verification_pipeline: { label: "Verification Pipeline", port: 0, description: "Claim verification and fact-checking", optional: true, tooltip: "Fact-checks AI responses against your KB and external sources" },
}

function statusBadge(status: string, serviceName?: string) {
  const label = serviceName ? `${serviceName}: ${status}` : status
  switch (status) {
    case "healthy":
    case "connected":
      return <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400" aria-label={label}>Healthy</Badge>
    case "degraded":
      return <Badge variant="outline" className="border-yellow-500/30 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400" aria-label={label}>Degraded</Badge>
    default:
      return <Badge variant="outline" className="border-destructive/30 bg-destructive/10 text-destructive" aria-label={label}>Offline</Badge>
  }
}

export function HealthDashboard({ polling = true, interval = 2000, onAllHealthy, lightweightMode, configuredProviders = [] }: HealthDashboardProps) {
  const [health, setHealth] = useState<SetupHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retesting, setRetesting] = useState(false)
  const notifiedRef = useRef(false)
  const onAllHealthyRef = useRef(onAllHealthy)
  onAllHealthyRef.current = onAllHealthy

  const checkHealth = useCallback(async () => {
    try {
      const data = await fetchSetupHealth()
      setHealth(data)
      setError(null)

      if (data.all_healthy && !notifiedRef.current) {
        notifiedRef.current = true
        onAllHealthyRef.current?.()
      }
    } catch {
      setError("Cannot reach backend")
    } finally {
      setLoading(false)
    }
  }, [])

  const handleRetestVerification = useCallback(async () => {
    setRetesting(true)
    try {
      await fetch(`${MCP_BASE}/setup/retest-verification`, { method: "POST" })
      await checkHealth()
    } catch {
      // Next poll will update
    } finally {
      setRetesting(false)
    }
  }, [checkHealth])

  useEffect(() => {
    checkHealth()
    if (!polling) return

    const id = setInterval(checkHealth, interval)
    return () => clearInterval(id)
  }, [checkHealth, polling, interval])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Checking services...
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-center text-sm text-destructive">
        {error}
      </div>
    )
  }

  // Filter out bifrost — it runs silently as fallback (handled in Optional Features)
  const services: SetupServiceHealth[] = (health?.services ?? []).filter(
    (svc) => svc.name !== "bifrost",
  )

  return (
    <TooltipProvider delayDuration={200}>
    <div className="space-y-3">
      <div className="space-y-1.5">
        {services.map((svc) => {
          const meta = SERVICE_META[svc.name] ?? { label: svc.name, port: 0, description: "" }
          const isLightweightNeo4j = lightweightMode && svc.name === "neo4j"
          const isOptional = meta.optional ?? false
          const isOffline = svc.status !== "healthy" && svc.status !== "connected" && svc.status !== "degraded"
          return (
            <div key={svc.name} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{meta.label}</span>
                  {meta.port > 0 && (
                    <span className="text-xs text-muted-foreground">:{meta.port}</span>
                  )}
                  {meta.tooltip && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                      </TooltipTrigger>
                      <TooltipContent side="top" className="max-w-[240px] text-xs">
                        {meta.tooltip}
                      </TooltipContent>
                    </Tooltip>
                  )}
                  {isOptional && (
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                      Optional
                    </span>
                  )}
                  {isLightweightNeo4j && (
                    <span className="rounded bg-yellow-500/10 px-1.5 py-0.5 text-[10px] font-medium text-yellow-600 dark:text-yellow-400">
                      Lightweight
                    </span>
                  )}
                </div>
                {meta.description && (
                  <p className="text-[10px] text-muted-foreground">{meta.description}</p>
                )}
                {/* Fix action for offline services */}
                {isOffline && !isOptional && meta.fixAction && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-1 h-6 gap-1 px-1.5 text-[10px] text-muted-foreground"
                    onClick={() => navigator.clipboard.writeText(meta.fixAction!)}
                  >
                    <Copy className="h-2.5 w-2.5" />
                    {meta.fixAction}
                  </Button>
                )}
                {/* Verification pipeline: contextual message + re-check */}
                {svc.name === "verification_pipeline" && isOffline && (
                  <div className="mt-1 flex items-center gap-2">
                    <p className="text-[10px] text-muted-foreground">
                      {configuredProviders.length === 0
                        ? "Requires API key — configure a provider first"
                        : "Self-test failed — click Re-check after configuring keys"}
                    </p>
                    {configuredProviders.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-5 gap-1 px-1.5 text-[10px]"
                        onClick={handleRetestVerification}
                        disabled={retesting}
                      >
                        {retesting ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <RefreshCw className="h-2.5 w-2.5" />}
                        Re-check
                      </Button>
                    )}
                  </div>
                )}
              </div>
              {statusBadge(svc.status, meta?.label)}
            </div>
          )
        })}
      </div>

      {/* Overall status */}
      <div className="rounded-lg border p-3 text-center">
        {health?.all_healthy ? (
          <>
            <p className="text-sm font-medium text-green-600 dark:text-green-400">
              All required services are healthy
            </p>
            {services.some((s) => (SERVICE_META[s.name]?.optional) && s.status !== "healthy" && s.status !== "connected") && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                Optional services can be configured later in Settings.
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            Waiting for required services to become healthy...
          </p>
        )}
      </div>
    </div>
    </TooltipProvider>
  )
}
