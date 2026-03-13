// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Loader2 } from "lucide-react"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { LastUpdated } from "@/components/ui/last-updated"
import { DigestCard } from "./digest-card"
import { HealthCards } from "./health-cards"
import { CollectionChart } from "./collection-chart"
import { IngestionTimeline } from "./ingestion-timeline"
import { SchedulerStatus } from "./scheduler-status"
import { KBOperations } from "./kb-operations"
import { fetchMaintenance, fetchIngestLog, fetchSchedulerStatus, fetchDigest } from "@/lib/api"

export function MonitoringPane() {
  const { data: maintenance, isLoading: loadingMaintenance, dataUpdatedAt } = useQuery({
    queryKey: ["maintenance"],
    queryFn: () => fetchMaintenance(["health", "collections"]),
    refetchInterval: 30_000,
  })

  const { data: ingestLog } = useQuery({
    queryKey: ["ingest-log"],
    queryFn: () => fetchIngestLog(200),
    refetchInterval: 30_000,
  })

  const { data: scheduler } = useQuery({
    queryKey: ["scheduler"],
    queryFn: fetchSchedulerStatus,
    refetchInterval: 30_000,
  })

  const [digestHours, setDigestHours] = useState(24)
  const { data: digest, isLoading: loadingDigest } = useQuery({
    queryKey: ["digest", digestHours],
    queryFn: () => fetchDigest(digestHours),
    refetchInterval: 60_000,
  })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Health</h2>
          <LastUpdated timestamp={dataUpdatedAt} />
        </div>
        <p className="text-xs text-muted-foreground">Live infrastructure status and recent operations</p>
      </div>

      {loadingMaintenance ? (
        <div className="flex flex-1 items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading system status...
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-4">
            <PaneErrorBoundary label="Knowledge Digest">
              <DigestCard digest={digest} isLoading={loadingDigest} onPeriodChange={setDigestHours} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Health Cards">
              <HealthCards health={maintenance?.health} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Collection Chart">
              <CollectionChart collections={maintenance?.collections} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="KB Operations">
              <KBOperations />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Ingestion Timeline">
              <IngestionTimeline entries={ingestLog?.entries} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Scheduler Status">
              <SchedulerStatus scheduler={scheduler} />
            </PaneErrorBoundary>
          </div>
        </ScrollArea>
      )}
    </div>
  )
}

export default MonitoringPane
