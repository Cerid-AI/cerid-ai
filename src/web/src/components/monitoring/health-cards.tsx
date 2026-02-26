import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { MaintenanceHealth } from "@/lib/types"
import { Database, GitBranch, HardDrive, Cpu } from "lucide-react"

const SERVICE_META: Record<string, { label: string; icon: typeof Database }> = {
  chromadb: { label: "ChromaDB", icon: Database },
  neo4j: { label: "Neo4j", icon: GitBranch },
  redis: { label: "Redis", icon: HardDrive },
  bifrost: { label: "Bifrost", icon: Cpu },
}

interface HealthCardsProps {
  health: MaintenanceHealth | undefined
}

export function HealthCards({ health }: HealthCardsProps) {
  if (!health) return null

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {Object.entries(health.services).map(([name, status]) => {
        const meta = SERVICE_META[name] ?? { label: name, icon: Database }
        const Icon = meta.icon
        const normalizedStatus = status.toLowerCase()
        const isOk = normalizedStatus === "connected" || normalizedStatus === "ok" || normalizedStatus === "healthy"
        const isSkipped = normalizedStatus.startsWith("skipped")
        const statusLabel = isOk ? "connected" : isSkipped ? "skipped" : "error"
        // Extract meaningful error detail for tooltip (strip "error: " prefix)
        const errorDetail = !isOk && !isSkipped && status.startsWith("error:")
          ? status.slice(7).trim()
          : !isOk && !isSkipped ? status : ""

        return (
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
      })}
    </div>
  )
}
