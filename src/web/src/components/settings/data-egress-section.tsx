// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, Globe } from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/ui/empty-state"
import { fetchEgressReport } from "@/lib/api"
import type { EgressRow } from "@/lib/types"

// Human labels for the channel keys the backend enumerates
// (app/routers/settings.py::get_egress_report). Falls back to a
// humanized form of the raw key so a server-added channel still
// renders something readable instead of disappearing.
const CHANNEL_LABELS: Record<string, string> = {
  chat_llm: "Chat LLM",
  internal_llm: "Internal pipeline LLM",
  ingest_enrichment: "Ingestion enrichment",
  external_verification: "Claim verification",
  web_search: "Web search",
  model_catalog_refresh: "Model catalog refresh",
  model_downloads: "Model downloads",
  error_reporting: "Error reporting",
  kb_backup_sync: "Knowledge-base sync",
}

function humanizeChannel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ")
}

// Canonical status → badge colour mapping (design contract §"Status →
// badge colour" — do not invent a new scale here).
const STATUS_META: Record<EgressRow["status"], { label: string; className: string }> = {
  local: { label: "Local", className: "bg-green-500/10 text-green-600 dark:text-green-400" },
  external_off: { label: "External · off", className: "bg-amber-500/10 text-amber-600 dark:text-amber-400" },
  external_on: { label: "External · on", className: "bg-red-500/10 text-red-600 dark:text-red-400" },
}

function StatusBadge({ status }: { status: EgressRow["status"] }) {
  const meta = STATUS_META[status]
  return (
    <Badge variant="secondary" className={`shrink-0 ${meta.className}`}>
      {meta.label}
    </Badge>
  )
}

function EgressTableRow({ row }: { row: EgressRow }) {
  return (
    <div className="grid grid-cols-1 gap-1 rounded-md px-2 py-2 hover:bg-muted/40 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] sm:items-center sm:gap-3">
      <div className="min-w-0 space-y-0.5">
        <p className="truncate text-sm font-medium">{humanizeChannel(row.channel)}</p>
        <p className="truncate text-label-xs text-muted-foreground">{row.trigger}</p>
      </div>
      <div className="min-w-0 space-y-0.5">
        <p className="truncate text-label-sm">{row.destination}</p>
        <p className="truncate text-label-xs text-muted-foreground">
          Payload: {row.payload_class} · <span className="font-mono">{row.setting_key}</span>
        </p>
      </div>
      <StatusBadge status={row.status} />
    </div>
  )
}

export function DataEgressSection() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["settings", "egress"],
    queryFn: fetchEgressReport,
  })

  const rows = data?.egress ?? []

  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase text-muted-foreground tracking-wider">Data Egress</span>
      </CardHeader>
      <CardContent className="density-stack">
        <p className="text-label-sm text-muted-foreground">
          Every outbound network path Cerid can take, and whether it is active on this server
          right now. &ldquo;Local&rdquo; means nothing on that channel reaches the network as
          currently configured.
        </p>

        {isLoading && (
          <div className="space-y-2">
            {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-12 w-full rounded-md" />)}
          </div>
        )}

        {isError && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              Failed to load the egress report.{" "}
              <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
            </AlertDescription>
          </Alert>
        )}

        {!isLoading && !isError && rows.length === 0 && (
          <EmptyState
            icon={Globe}
            title="No egress channels reported"
            description="The server did not report any outbound network paths."
          />
        )}

        {!isLoading && !isError && rows.length > 0 && (
          <div className="space-y-1">
            {rows.map((row) => <EgressTableRow key={row.channel} row={row} />)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
