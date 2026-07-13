// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Sources / Connectors panel. Master list of ingestion sources from the
// /sources endpoint, filtered to ingestion kinds via listIngestionSources(),
// merged with connector rows from /connectors via listConnectors(), an
// email row derived from fetchEmailStatus, and Apple bridge rows (desktop-only).
// Selecting a source row opens SourceDetailPane; connector rows open ConnectorDetail;
// the email row opens EmailDetail (SR2); Apple rows open AppleDetail (B2).
// External-API and plugin sections are removed (Settings → Extensions concerns).

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { listIngestionSources, type SourceRecord } from "@/lib/api/sources"
import { listConnectors } from "@/lib/api/connectors"
import { fetchEmailStatus } from "@/lib/api/email"
import { updateWatchedFolder } from "@/lib/api/settings"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { descriptorFor } from "./source-kind-icons"
import { SourceDetailPane } from "./source-detail-pane"
import { ConnectorDetail } from "./connector-detail"
import { EmailDetail } from "./email-detail"
import { AppleDetail } from "./apple-detail"
import { SourcesEmptyGallery } from "./sources-empty-gallery"
import { sourceToRow, connectorToRow, emailToRow, appleRows, type DisplayRow } from "./source-rows"
import type { ConnectorStatus } from "@/lib/api/connectors"
import type { AppleBridgeKind } from "./apple-detail"

// ---------------------------------------------------------------------------
// Source list row (display-only; driven by DisplayRow)
// ---------------------------------------------------------------------------

function SourceRow({
  row,
  selected,
  onSelect,
  onToggle,
  busy,
}: {
  row: DisplayRow
  selected: boolean
  onSelect: () => void
  onToggle?: () => void
  busy?: boolean
}) {
  const desc = descriptorFor(row.kind)
  const Icon = desc.icon
  const enabled = row.status === "connected"
  const isFolderKind = row.rowType === "source" && row.kind === "folder"

  // Status badge label: connector rows show "connected"/"available"/"error",
  // source rows keep the original on/off vocabulary.
  const badgeLabel = row.rowType === "source"
    ? (enabled ? "on" : "off")
    : row.status

  return (
    <li className={`flex items-center gap-1 rounded-md transition-colors ${selected ? "bg-accent text-accent-foreground" : "hover:bg-accent/40"}`}>
      {/* Row select button — occupies all space except the toggle */}
      <button
        type="button"
        onClick={onSelect}
        className="flex min-w-0 grow items-start gap-2 px-2 py-2 text-left"
        aria-pressed={selected}
      >
        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="grow min-w-0">
          <span className="block truncate text-sm" title={row.displayName}>{row.displayName}</span>
          {/* Connector rows carry a sync-semantics explainer; other rows show
              the kind label. Hover title only for the (truncated) explainer. */}
          <span className="block truncate text-label-xxs text-muted-foreground" title={row.detail}>
            {row.detail ?? desc.label}
          </span>
        </span>
      </button>
      {/* Toggle — only for folder sources */}
      {isFolderKind && onToggle ? (
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-busy={busy}
          aria-label={`${enabled ? "Disable" : "Enable"} ${row.displayName}`}
          disabled={busy}
          onClick={onToggle}
          className={`mr-2 flex h-5 shrink-0 items-center rounded-full px-2 text-label-xxs font-medium transition-colors ${
            enabled
              ? "bg-primary/15 text-primary"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : badgeLabel}
        </button>
      ) : (
        <span
          className={`mr-2 flex h-5 shrink-0 items-center rounded-full px-2 text-label-xxs font-medium ${
            enabled
              ? "bg-primary/15 text-primary"
              : "bg-muted text-muted-foreground"
          }`}
          aria-hidden="true"
        >
          {badgeLabel}
        </span>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SourcesConnectors({ onAddSource }: { onAddSource?: (kind: string) => void } = {}) {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  const {
    data: sources = [],
    isLoading: sourcesLoading,
    isError: sourcesError,
    refetch: refetchSources,
  } = useQuery({
    queryKey: ["ingestion-sources"],
    queryFn: listIngestionSources,
    staleTime: 30_000,
  })

  const {
    data: connectors = [],
    isLoading: connectorsLoading,
    isError: connectorsError,
    refetch: refetchConnectors,
  } = useQuery({
    queryKey: ["connectors"],
    queryFn: listConnectors,
    staleTime: 30_000,
  })

  // Email status is an auxiliary row source — a failure here just omits the
  // email row (see `emailStatus ? … : []` below), it never triggers the
  // blocking error state.
  const { data: emailStatus, refetch: refetchEmailStatus } = useQuery({
    queryKey: ["email-status"],
    queryFn: fetchEmailStatus,
    staleTime: 30_000,
  })

  // List-driving queries gate loading/error; email is auxiliary.
  const isLoading = sourcesLoading || connectorsLoading
  const isError = sourcesError || connectorsError

  const handleRetry = () => {
    void refetchSources()
    void refetchConnectors()
    void refetchEmailStatus()
  }

  // Unified row list: source rows, connector rows, email row, Apple bridge rows.
  // appleRows() returns [] in browser builds (no window.cerid.appleConnectors).
  const rows: DisplayRow[] = [
    ...sources.map(sourceToRow),
    ...connectors.map(connectorToRow),
    ...(emailStatus ? [emailToRow(emailStatus)] : []),
    ...appleRows(),
  ]

  // "Configured" = a source the user actually added, or a connector/email/
  // apple row that reached the connected state. The 7-entry connector
  // catalog and the always-present email affordance are NOT configured on
  // a fresh install, so rows.length alone can never be 0 — this is the
  // definition that actually reaches the empty state.
  const configured = rows.filter((r) => r.rowType === "source" || r.status === "connected")
  const isEmpty = !isLoading && !isError && configured.length === 0

  const selectedRow = selectedId ? (rows.find((r) => r.id === selectedId) ?? null) : null

  // SourceDetailPane expects a SourceRecord; only pass backing for source rows.
  const selectedSource: SourceRecord | null =
    selectedRow?.rowType === "source" ? (selectedRow.backing as SourceRecord) : null

  const handleSelect = (row: DisplayRow) => {
    setSelectedId(row.id)
    setDetailOpen(true)
  }

  const handleToggle = async (row: DisplayRow) => {
    if (row.rowType !== "source" || row.kind !== "folder") return
    const src = row.backing as SourceRecord
    const folderId = src.id.replace(/^folder:/, "")
    const nextEnabled = src.status !== "connected"
    setBusyId(row.id)
    try {
      await updateWatchedFolder(folderId, { enabled: nextEnabled })
      qc.invalidateQueries({ queryKey: ["ingestion-sources"] })
    } finally {
      setBusyId(null)
    }
  }

  const handleDeleted = () => {
    setSelectedId(null)
    setDetailOpen(false)
    void refetchSources()
  }

  if (isLoading) {
    return (
      <div
        data-testid="sources-loading"
        className="grid h-full grid-cols-1 md:grid-cols-[280px_1fr]"
      >
        <div className="overflow-y-auto border-r bg-card/20 p-2">
          <div className="flex flex-col gap-1.5">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} className="h-9 w-full rounded-md" />
            ))}
          </div>
        </div>
        <div className="p-4" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="w-full max-w-md">
          <PaneError
            title="Couldn't load sources"
            description="The sources backend is unreachable."
            onRetry={handleRetry}
          />
        </div>
      </div>
    )
  }

  if (isEmpty) {
    // The gallery replaces the row list, but connector rows still back the
    // catalog — oauth tiles open the matching ConnectorDetail directly so
    // "connect in Settings" dead-end copy is replaced with the actual flow.
    return (
      <>
        <SourcesEmptyGallery
          onSelectKind={onAddSource ?? (() => {})}
          onOpenConnector={(kind) => {
            const id = `connector:${kind}`
            if (rows.some((r) => r.id === id)) {
              setSelectedId(id)
              setDetailOpen(true)
            }
          }}
        />
        {selectedRow?.rowType === "connector" && (
          <ConnectorDetail
            open={detailOpen}
            connector={selectedRow.backing as ConnectorStatus}
            onClose={() => setDetailOpen(false)}
          />
        )}
      </>
    )
  }

  return (
    <>
      <div className="grid h-full grid-cols-1 md:grid-cols-[280px_1fr]">
        {/* List column */}
        <div className="overflow-y-auto border-r bg-card/20 p-2">
          <div className="mb-2 text-label-xs uppercase tracking-wide text-muted-foreground">
            {rows.length} source{rows.length === 1 ? "" : "s"}
          </div>
          <ul className="flex flex-col gap-0.5">
            {rows.map((row) => (
              <SourceRow
                key={row.id}
                row={row}
                selected={selectedRow?.id === row.id}
                onSelect={() => handleSelect(row)}
                onToggle={row.rowType === "source" && row.kind === "folder" ? () => handleToggle(row) : undefined}
                busy={busyId === row.id}
              />
            ))}
          </ul>
        </div>

        {/* Detail column placeholder when no selection */}
        <div className="overflow-y-auto p-4">
          {!selectedRow && (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              Select a source or connector to view details.
            </div>
          )}
        </div>
      </div>

      {/* Source detail pane */}
      <SourceDetailPane
        open={detailOpen && selectedRow?.rowType === "source"}
        source={selectedSource}
        onClose={() => setDetailOpen(false)}
        onDeleted={handleDeleted}
      />

      {/* Connector detail pane */}
      {selectedRow?.rowType === "connector" && (
        <ConnectorDetail
          open={detailOpen}
          connector={selectedRow.backing as ConnectorStatus}
          onClose={() => setDetailOpen(false)}
        />
      )}

      {/* Email detail pane */}
      <EmailDetail
        open={detailOpen && selectedRow?.rowType === "email"}
        onClose={() => setDetailOpen(false)}
      />

      {/* Apple detail pane (desktop-only; AppleDetail gates itself on desktopAvailable) */}
      {selectedRow?.rowType === "apple" && (
        <AppleDetail
          kind={selectedRow.kind as AppleBridgeKind}
          open={detailOpen}
          onClose={() => setDetailOpen(false)}
        />
      )}
    </>
  )
}
