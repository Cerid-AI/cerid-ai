// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useRef } from "react"
import { Badge } from "@/components/ui/badge"
import { Loader2 } from "lucide-react"
import { fetchSetupHealth } from "@/lib/api"
import type { SetupHealth, SetupServiceHealth } from "@/lib/types"

interface HealthDashboardProps {
  polling?: boolean
  interval?: number
  onAllHealthy?: () => void
}

const SERVICE_META: Record<string, { label: string; port: number }> = {
  neo4j: { label: "Neo4j (Graph DB)", port: 7687 },
  chromadb: { label: "ChromaDB (Vectors)", port: 8001 },
  redis: { label: "Redis (Cache)", port: 6379 },
  bifrost: { label: "Bifrost (LLM Gateway)", port: 8080 },
  mcp: { label: "MCP Server (API)", port: 8888 },
}

function statusBadge(status: string) {
  switch (status) {
    case "healthy":
    case "connected":
      return <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">Healthy</Badge>
    case "degraded":
      return <Badge variant="outline" className="border-yellow-500/30 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">Degraded</Badge>
    default:
      return <Badge variant="outline" className="border-destructive/30 bg-destructive/10 text-destructive">Offline</Badge>
  }
}

export function HealthDashboard({ polling = true, interval = 2000, onAllHealthy }: HealthDashboardProps) {
  const [health, setHealth] = useState<SetupHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const notifiedRef = useRef(false)

  const checkHealth = useCallback(async () => {
    try {
      const data = await fetchSetupHealth()
      setHealth(data)
      setError(null)

      // Check if all services are healthy
      if (data.all_healthy && !notifiedRef.current) {
        notifiedRef.current = true
        onAllHealthy?.()
      }
    } catch {
      setError("Cannot reach backend")
    } finally {
      setLoading(false)
    }
  }, [onAllHealthy])

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

  const services: SetupServiceHealth[] = health?.services ?? []

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        {services.map((svc) => {
          const meta = SERVICE_META[svc.name] ?? { label: svc.name, port: 0 }
          return (
            <div key={svc.name} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex items-center gap-3">
                <div className="text-sm font-medium">{meta.label}</div>
                {meta.port > 0 && (
                  <span className="text-xs text-muted-foreground">:{meta.port}</span>
                )}
              </div>
              {statusBadge(svc.status)}
            </div>
          )
        })}
      </div>

      {/* Overall status */}
      <div className="rounded-lg border p-3 text-center">
        {health?.all_healthy ? (
          <p className="text-sm font-medium text-green-600 dark:text-green-400">
            All services healthy
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Waiting for all services to become healthy...
          </p>
        )}
      </div>
    </div>
  )
}
