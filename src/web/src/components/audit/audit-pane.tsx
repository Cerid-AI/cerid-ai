import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Loader2 } from "lucide-react"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { LastUpdated } from "@/components/ui/last-updated"
import { ActivityChart } from "./activity-chart"
import { CostBreakdown } from "./cost-breakdown"
import { QueryStats } from "./query-stats"
import { IngestionStats } from "./ingestion-stats"
import { RecentFailures } from "./recent-failures"
import { fetchAudit } from "@/lib/api"
import { cn } from "@/lib/utils"

const TIME_RANGES = [
  { label: "24h", hours: 24 },
  { label: "48h", hours: 48 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
] as const

export function AuditPane() {
  const [hours, setHours] = useState(24)

  const { data: audit, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["audit", hours],
    queryFn: () => fetchAudit(["activity", "ingestion", "costs", "queries"], hours),
    refetchInterval: 60_000,
  })

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold">Audit & Analytics</h2>
            <LastUpdated timestamp={dataUpdatedAt} />
          </div>
          <p className="text-xs text-muted-foreground">Auto-refreshes every 60 seconds</p>
        </div>
        <div className="flex gap-1">
          {TIME_RANGES.map((range) => (
            <Button
              key={range.hours}
              variant="ghost"
              size="sm"
              className={cn(
                "h-7 text-xs",
                hours === range.hours && "bg-muted font-medium",
              )}
              onClick={() => setHours(range.hours)}
            >
              {range.label}
            </Button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading audit data...
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="space-y-4 p-4">
            <PaneErrorBoundary label="Activity Chart">
              <ActivityChart activity={audit?.activity} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Cost Breakdown">
              <CostBreakdown costs={audit?.costs} hours={hours} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Query Stats">
              <QueryStats queries={audit?.queries} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Ingestion Stats">
              <IngestionStats ingestion={audit?.ingestion} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Recent Failures">
              <RecentFailures failures={audit?.activity?.recent_failures} />
            </PaneErrorBoundary>
          </div>
        </ScrollArea>
      )}
    </div>
  )
}

export default AuditPane
