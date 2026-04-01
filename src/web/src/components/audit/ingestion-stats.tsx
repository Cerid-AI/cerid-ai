// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { EmptyState } from "@/components/ui/empty-state"
import { FileUp } from "lucide-react"
import type { AuditIngestion } from "@/lib/types"

interface IngestionStatsProps {
  ingestion: AuditIngestion | undefined
}

export function IngestionStats({ ingestion }: IngestionStatsProps) {
  if (!ingestion) return <EmptyState icon={FileUp} title="No ingestion data" description="Stats appear after files are ingested" />

  const duplicateRatePct = (ingestion.duplicate_rate * 100).toFixed(1)

  return (
    <Card>
      <CardHeader className="p-3 pb-0">
        <CardTitle className="text-sm">Ingestion Stats</CardTitle>
      </CardHeader>
      <CardContent className="p-3">
        {/* Summary stats */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Total Ingests</p>
            <p className="text-lg font-semibold">{ingestion.total_ingests}</p>
          </div>
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Duplicate Rate</p>
            <p className="text-lg font-semibold">{duplicateRatePct}%</p>
            <p className="text-xs text-muted-foreground">{ingestion.total_duplicates} dupes</p>
          </div>
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Avg Chunks/File</p>
            <p className="text-lg font-semibold">{ingestion.avg_chunks_per_file.toFixed(1)}</p>
          </div>
          <div className="rounded-lg bg-muted/50 p-2.5">
            <p className="text-xs text-muted-foreground">Recategorizations</p>
            <p className="text-lg font-semibold">{ingestion.recategorizations}</p>
          </div>
        </div>

        {/* File type distribution */}
        {Object.keys(ingestion.file_type_distribution).length > 0 && (
          <div className="mt-3">
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">File Types</p>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(ingestion.file_type_distribution)
                .sort(([, a], [, b]) => b - a)
                .map(([type, count]) => (
                  <Badge key={type} variant="secondary" className="text-xs">
                    {type}: {count}
                  </Badge>
                ))}
            </div>
          </div>
        )}

        {/* Domain distribution */}
        {Object.keys(ingestion.domain_distribution).length > 0 && (
          <div className="mt-3">
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">By Domain</p>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(ingestion.domain_distribution)
                .sort(([, a], [, b]) => b - a)
                .map(([domain, count]) => (
                  <Badge key={domain} variant="outline" className="text-xs capitalize">
                    {domain}: {count}
                  </Badge>
                ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}