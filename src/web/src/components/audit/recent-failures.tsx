// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AlertTriangle } from "lucide-react"
import type { IngestLogEntry } from "@/lib/types"

interface RecentFailuresProps {
  failures: IngestLogEntry[] | undefined
}

export function RecentFailures({ failures }: RecentFailuresProps) {
  if (!failures || failures.length === 0) return null

  return (
    <Card className="border-destructive/30">
      <CardHeader className="flex flex-row items-center gap-2 space-y-0 p-3 pb-0">
        <AlertTriangle className="h-4 w-4 text-destructive" />
        <CardTitle className="text-sm text-destructive">Recent Failures ({failures.length})</CardTitle>
      </CardHeader>
      <CardContent className="p-3">
        <div className="space-y-1">
          {failures.slice(0, 10).map((entry, i) => (
            <div
              key={`${entry.timestamp}-${i}`}
              className="flex items-center gap-2 rounded-md bg-destructive/5 px-2 py-1.5 text-xs"
            >
              <span className="font-medium text-destructive">{entry.event}</span>
              <span className="min-w-0 flex-1 truncate">{entry.filename || entry.artifact_id?.slice(0, 8)}</span>
              <span className="shrink-0 text-muted-foreground">
                {new Date(entry.timestamp).toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}