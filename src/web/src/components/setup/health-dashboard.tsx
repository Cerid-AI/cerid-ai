// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useRef } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Loader2, Info, Copy, RefreshCw, Server, Cpu, Sparkles } from "lucide-react"
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

type ServiceCategory = "infrastructure" | "ai_pipeline" | "optional"

const SERVICE_META: Record<string, { label: string; port: number; description: string; category: ServiceCategory; optional?: boolean; tooltip?: string; fixAction?: string }> = {
  mcp: { label: "MCP Server (API)", port: 8888, description: "Core API — powers everything", category: "infrastructure", tooltip: "The brain of Cerid — processes queries, manages your KB, and coordinates all services", fixAction: "Check Docker Desktop is running" },
  chromadb: { label: "ChromaDB (Vectors)", port: 8001, description: "Semantic search over your knowledge", category: "infrastructure", tooltip: "Stores document embeddings for fast semantic search — finds relevant content even when wording differs", fixAction: "docker compose up chromadb -d" },
  redis: { label: "Redis (Cache)", port: 6379, description: "Query cache and audit log", category: "infrastructure", tooltip: "Speeds up repeated queries and stores your conversation audit trail", fixAction: "docker compose up redis -d" },
  neo4j: { label: "Neo4j (Graph DB)", port: 7687, description: "Graph relationships between documents", category: "infrastructure", tooltip: "Tracks relationships between your documents — which topics connect to which sources", fixAction: "docker compose up neo4j -d" },
  verification_pipeline: { label: "Verification Pipeline", port: 0, description: "Claim verification and fact-checking", category: "ai_pipeline", optional: true, tooltip: "Fact-checks AI responses against your KB and external sources" },
}

const CATEGORY_META: Record<ServiceCategory, { label: string; icon: typeof Server }> = {
  infrastructure: { label: "Infrastructure", icon: Server },
  ai_pipeline: { label: "AI Pipeline", icon: Cpu },
  optional: { label: "Optional", icon: Sparkles },
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

function ServiceRow({
  svc,
  lightweightMode,
  configuredProviders,
  retesting,
  onRetest,
}: {
  svc: SetupServiceHealth
  lightweightMode?: boolean
  configuredProviders: string[]
  retesting: boolean
  onRetest: () => void
}) {
  const meta = SERVICE_META[svc.name] ?? { label: svc.name, port: 0, description: "", category: "optional" as ServiceCategory }
  const isLightweightNeo4j = lightweightMode && svc.name === "neo4j"
  const isOptional = meta.optional ?? false
  const isOffline = svc.status !== "healthy" && svc.status !== "connected" && svc.status !== "degraded"

  return (
    <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
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
                onClick={onRetest}
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
}

export function HealthDashboard({ polling = true, interval = 2000, onAllHealthy, lightweightMode, configuredProviders = [] }: HealthDashboardProps) {
  const [health, setHealth] = useState<SetupHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retesting, setRetesting] = useState(false)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const notifiedRef = useRef(false)
  const onAllHealthyRef = useRef(onAllHealthy)
  onAllHealthyRef.current = onAllHealthy

  const checkHealth = useCallback(async () => {
    try {
      const data = await fetchSetupHealth()
      setHealth(data)
      setError(null)
      setLastChecked(new Date())

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

  // Filter out bifrost — it runs silently as fallback
  const services: SetupServiceHealth[] = (health?.services ?? []).filter(
    (svc) => svc.name !== "bifrost",
  )

  // Group services by category
  const grouped: Record<ServiceCategory, SetupServiceHealth[]> = {
    infrastructure: [],
    ai_pipeline: [],
    optional: [],
  }
  for (const svc of services) {
    const meta = SERVICE_META[svc.name]
    const cat = meta?.category ?? "optional"
    grouped[cat].push(svc)
  }

  const categories: ServiceCategory[] = ["infrastructure", "ai_pipeline", "optional"]

  return (
    <TooltipProvider delayDuration={200}>
    <div className="space-y-4">
      {/* Auto-refresh indicator */}
      {lastChecked && (
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>
            Last checked: {lastChecked.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </span>
          <Button variant="ghost" size="sm" className="h-5 gap-1 px-1.5 text-[10px]" onClick={checkHealth}>
            <RefreshCw className="h-2.5 w-2.5" />
            Refresh
          </Button>
        </div>
      )}

      {/* Grouped service cards */}
      {categories.map((cat) => {
        const svcs = grouped[cat]
        if (svcs.length === 0) return null
        const catMeta = CATEGORY_META[cat]
        const CatIcon = catMeta.icon
        return (
          <div key={cat} className="space-y-1.5">
            <div className="flex items-center gap-1.5 px-1">
              <CatIcon className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs font-semibold text-muted-foreground">{catMeta.label}</span>
            </div>
            {svcs.map((svc) => (
              <ServiceRow
                key={svc.name}
                svc={svc}
                lightweightMode={lightweightMode}
                configuredProviders={configuredProviders}
                retesting={retesting}
                onRetest={handleRetestVerification}
              />
            ))}
          </div>
        )
      })}

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
