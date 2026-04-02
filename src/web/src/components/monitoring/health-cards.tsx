// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { MaintenanceHealth } from "@/lib/types"
import { Database, GitBranch, HardDrive, Cpu, Server, Bot } from "lucide-react"

const SERVICE_META: Record<string, { label: string; icon: typeof Database; tooltip: string }> = {
  neo4j: {
    label: "Neo4j",
    icon: GitBranch,
    tooltip: "Graph database storing knowledge relationships, artifact metadata, and domain taxonomy",
  },
  chromadb: {
    label: "ChromaDB",
    icon: Database,
    tooltip: "Vector database for semantic search — stores document embeddings for RAG retrieval",
  },
  redis: {
    label: "Redis",
    icon: HardDrive,
    tooltip: "In-memory cache for query results, rate limiting, session data, and audit logs",
  },
  bifrost: {
    label: "Bifrost",
    icon: Cpu,
    tooltip: "LLM gateway routing requests to the optimal AI model based on task type",
  },
  ollama: {
    label: "Ollama",
    icon: Bot,
    tooltip: "Local LLM inference server for air-gapped or low-latency operations",
  },
  mcp: {
    label: "MCP",
    icon: Server,
    tooltip: "Core API server handling all knowledge operations, chat, and verification",
  },
}

const DEFAULT_META = { label: "", icon: Database, tooltip: "" }

interface HealthCardsProps {
  health: MaintenanceHealth | undefined
}

export function HealthCards({ health }: HealthCardsProps) {
  if (!health) return null

  return (
    <TooltipProvider>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Object.entries(health.services).map(([name, status]) => {
          const meta = SERVICE_META[name] ?? { ...DEFAULT_META, label: name }
          const Icon = meta.icon
          const normalizedStatus = status.toLowerCase()
          const isOk = normalizedStatus === "connected" || normalizedStatus === "ok" || normalizedStatus === "healthy"
          const isSkipped = normalizedStatus.startsWith("skipped")
          const statusLabel = isOk ? "connected" : isSkipped ? "skipped" : "error"
          // Extract meaningful error detail for tooltip (strip "error: " prefix, case-insensitive)
          const errorDetail = !isOk && !isSkipped && normalizedStatus.startsWith("error:")
            ? status.slice(7).trim()
            : !isOk && !isSkipped ? status : ""

          const card = (
            <Card key={name}>
              <CardHeader className="flex flex-row items-center gap-2 space-y-0 p-3 pb-1">
                <Icon className="h-4 w-4 text-muted-foreground" />
                <CardTitle className="text-sm">{meta.label}</CardTitle>
              </CardHeader>
              <CardContent className="p-3 pt-0">
                <div className="flex items-center gap-1.5" title={errorDetail || status}>
                  <div
                    className={cn(
                      "h-2 w-2 rounded-full",
                      isOk ? "bg-green-500" : isSkipped ? "bg-yellow-500" : "bg-red-500",
                    )}
                    aria-hidden="true"
                  />
                  <span className="text-xs text-muted-foreground capitalize">{statusLabel}</span>
                </div>
                {errorDetail && (
                  <p className="mt-1 truncate text-xs text-destructive" title={errorDetail}>
                    {errorDetail.length > 60 ? `${errorDetail.slice(0, 60)}...` : errorDetail}
                  </p>
                )}
                {/* Data counts */}
                {name === "chromadb" && health.data?.total_chunks != null && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {health.data.total_chunks.toLocaleString()} chunks
                  </p>
                )}
                {name === "neo4j" && health.data?.artifacts != null && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {health.data.artifacts.toLocaleString()} artifacts
                  </p>
                )}
                {name === "redis" && health.data?.audit_log_entries != null && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {health.data.audit_log_entries.toLocaleString()} log entries
                  </p>
                )}
              </CardContent>
            </Card>
          )

          if (meta.tooltip) {
            return (
              <Tooltip key={name}>
                <TooltipTrigger asChild>{card}</TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-64">
                  {meta.tooltip}
                </TooltipContent>
              </Tooltip>
            )
          }

          return card
        })}
      </div>
    </TooltipProvider>
  )
}
