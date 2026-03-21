// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback, useRef } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Loader2,
  AlertCircle,
  CheckCircle2,
  Upload,
  Download,
  RefreshCw,
} from "lucide-react"
import { fetchSyncStatus, triggerSyncExport, triggerSyncImport } from "@/lib/api"
import { CONFLICT_STRATEGIES } from "@/lib/types"
import type {
  SyncStatus,
  SyncExportResult,
  SyncImportResult,
  ConflictStrategy,
} from "@/lib/types"

const STATUS_RESET_MS = 8000

type OpState = "idle" | "running" | "success" | "error"

export function SyncSection() {
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [statusError, setStatusError] = useState("")

  const [exportState, setExportState] = useState<OpState>("idle")
  const [exportResult, setExportResult] = useState<SyncExportResult | null>(null)
  const [exportError, setExportError] = useState("")

  const [importState, setImportState] = useState<OpState>("idle")
  const [importResult, setImportResult] = useState<SyncImportResult | null>(null)
  const [importError, setImportError] = useState("")

  const [conflictStrategy, setConflictStrategy] = useState<ConflictStrategy>("remote_wins")
  const timeoutRefs = useRef<ReturnType<typeof setTimeout>[]>([])

  useEffect(() => () => timeoutRefs.current.forEach(clearTimeout), [])

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    setStatusError("")
    try {
      const data = await fetchSyncStatus()
      setStatus(data)
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : "Failed to load sync status")
    } finally {
      setStatusLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setStatusLoading(true)
    fetchSyncStatus()
      .then((data) => { if (!cancelled) setStatus(data) })
      .catch((e) => { if (!cancelled) setStatusError(e instanceof Error ? e.message : "Failed to load sync status") })
      .finally(() => { if (!cancelled) setStatusLoading(false) })
    return () => { cancelled = true }
  }, [])

  const handleExport = async () => {
    setExportState("running")
    setExportError("")
    setExportResult(null)
    try {
      const result = await triggerSyncExport()
      setExportResult(result)
      setExportState("success")
      loadStatus()
      timeoutRefs.current.push(setTimeout(() => setExportState("idle"), STATUS_RESET_MS))
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed")
      setExportState("error")
      timeoutRefs.current.push(setTimeout(() => setExportState("idle"), 5000))
    }
  }

  const handleImport = async () => {
    setImportState("running")
    setImportError("")
    setImportResult(null)
    try {
      const result = await triggerSyncImport({ conflict_strategy: conflictStrategy })
      setImportResult(result)
      setImportState("success")
      loadStatus()
      timeoutRefs.current.push(setTimeout(() => setImportState("idle"), STATUS_RESET_MS))
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed")
      setImportState("error")
      timeoutRefs.current.push(setTimeout(() => setImportState("idle"), 5000))
    }
  }

  const isBusy = exportState === "running" || importState === "running"

  return (
    <Card className="mb-4">
      <CardContent className="grid gap-4 pt-4">
        {/* Status overview */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">Sync Status</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={loadStatus}
            disabled={statusLoading}
          >
            {statusLoading ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3 w-3" />
            )}
            Refresh
          </Button>
        </div>

        {statusError && (
          <div className="flex items-center gap-1.5 text-xs text-destructive">
            <AlertCircle className="h-3 w-3 shrink-0" />
            {statusError}
          </div>
        )}

        {status && !statusError && (
          <div className="space-y-2">
            {/* Last sync info */}
            {status.manifest ? (
              <div className="space-y-1 text-xs text-muted-foreground">
                <div className="flex justify-between">
                  <span>Last export</span>
                  <span className="font-mono text-[10px]">
                    {formatTimestamp(status.manifest.last_exported_at)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Source machine</span>
                  <span className="font-mono text-[10px]">{status.manifest.machine_id}</span>
                </div>
                {status.manifest.is_incremental && (
                  <Badge variant="outline" className="text-[10px]">Incremental</Badge>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No sync manifest found — run an export first
              </p>
            )}

            {/* Count comparison */}
            {status.manifest && (
              <div className="rounded-md border p-2">
                <p className="mb-1.5 text-[10px] font-medium text-muted-foreground">Local vs Sync</p>
                <div className="grid grid-cols-3 gap-x-3 gap-y-1 text-[10px]">
                  <span className="text-muted-foreground">Store</span>
                  <span className="text-center text-muted-foreground">Local</span>
                  <span className="text-center text-muted-foreground">Sync</span>

                  <span>Artifacts</span>
                  <span className="text-center tabular-nums">{status.local.neo4j_artifacts}</span>
                  <span className="text-center tabular-nums">{status.sync.neo4j_artifacts}</span>

                  <span>Domains</span>
                  <span className="text-center tabular-nums">{status.local.neo4j_domains}</span>
                  <span className="text-center tabular-nums">{status.sync.neo4j_domains}</span>

                  <span>Relations</span>
                  <span className="text-center tabular-nums">{status.local.neo4j_relationships}</span>
                  <span className="text-center tabular-nums">{status.sync.neo4j_relationships}</span>

                  <span>Audit log</span>
                  <span className="text-center tabular-nums">{status.local.redis_entries}</span>
                  <span className="text-center tabular-nums">{status.sync.redis_entries}</span>
                </div>

                {/* Chroma per-domain */}
                {Object.keys(status.local.chroma_chunks).length > 0 && (
                  <>
                    <p className="mb-1 mt-2 text-[10px] font-medium text-muted-foreground">Chunks by domain</p>
                    <div className="grid grid-cols-3 gap-x-3 gap-y-0.5 text-[10px]">
                      {Object.entries(status.local.chroma_chunks).map(([domain, count]) => (
                        <ChunkRow
                          key={domain}
                          domain={domain}
                          local={count}
                          sync={status.sync.chroma_chunks[domain] ?? 0}
                        />
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        <div className="h-px bg-border" />

        {/* Export */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">Export to Sync</span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={handleExport}
            disabled={isBusy}
          >
            {exportState === "running" ? (
              <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Exporting...</>
            ) : (
              <><Upload className="mr-1.5 h-3 w-3" />Export</>
            )}
          </Button>
        </div>

        {exportState === "success" && exportResult && (
          <ExportResultSummary result={exportResult} />
        )}
        {exportState === "error" && (
          <div className="flex items-center gap-1.5 text-xs text-destructive">
            <AlertCircle className="h-3 w-3 shrink-0" />
            {exportError}
          </div>
        )}

        <div className="h-px bg-border" />

        {/* Import */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Import from Sync</span>
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={handleImport}
              disabled={isBusy}
            >
              {importState === "running" ? (
                <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Importing...</>
              ) : (
                <><Download className="mr-1.5 h-3 w-3" />Import</>
              )}
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-muted-foreground">Conflict strategy:</span>
            <Select
              value={conflictStrategy}
              onValueChange={(v) => setConflictStrategy(v as ConflictStrategy)}
            >
              <SelectTrigger className="h-6 w-36 text-[10px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CONFLICT_STRATEGIES.map((s) => (
                  <SelectItem key={s.value} value={s.value} className="text-xs">
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {importState === "success" && importResult && (
          <ImportResultSummary result={importResult} />
        )}
        {importState === "error" && (
          <div className="flex items-center gap-1.5 text-xs text-destructive">
            <AlertCircle className="h-3 w-3 shrink-0" />
            {importError}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ExportResultSummary({ result }: { result: SyncExportResult }) {
  const totalChroma = Object.values(result.chroma).reduce((a, b) => a + b, 0)
  return (
    <div className="flex items-start gap-1.5 text-xs text-green-600 dark:text-green-400">
      <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" />
      <span>
        Exported {result.neo4j.artifacts} artifacts, {totalChroma} chunks, {result.redis} audit entries
        {result.tombstones > 0 && `, ${result.tombstones} tombstones`}
      </span>
    </div>
  )
}

function ImportResultSummary({ result }: { result: SyncImportResult }) {
  const { neo4j } = result
  const totalChroma = Object.values(result.chroma).reduce((a, b) => a + b, 0)
  const hasConflicts = neo4j.artifacts_conflict > 0

  return (
    <div className="space-y-1">
      <div className="flex items-start gap-1.5 text-xs text-green-600 dark:text-green-400">
        <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          Created {neo4j.artifacts_created}, updated {neo4j.artifacts_updated}, skipped {neo4j.artifacts_skipped}
          {totalChroma > 0 && ` — ${totalChroma} chunks`}
        </span>
      </div>
      {hasConflicts && (
        <div className="space-y-0.5">
          <p className="text-xs text-yellow-600 dark:text-yellow-400">
            {neo4j.artifacts_conflict} conflict{neo4j.artifacts_conflict !== 1 ? "s" : ""}:
          </p>
          {neo4j.conflicts.slice(0, 5).map((c) => (
            <div key={c.artifact_id} className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <span className="min-w-0 truncate">{c.filename}</span>
              <Badge variant="outline" className="ml-auto shrink-0 text-[9px]">{c.resolution}</Badge>
            </div>
          ))}
          {neo4j.conflicts.length > 5 && (
            <p className="text-[9px] text-muted-foreground">+{neo4j.conflicts.length - 5} more</p>
          )}
        </div>
      )}
      {result.consistency_warnings.length > 0 && (
        <div className="space-y-0.5">
          <p className="text-[10px] text-yellow-600 dark:text-yellow-400">
            {result.consistency_warnings.length} warning{result.consistency_warnings.length !== 1 ? "s" : ""}
          </p>
          {result.consistency_warnings.slice(0, 3).map((w, i) => (
            <p key={i} className="text-[10px] text-muted-foreground">{w}</p>
          ))}
        </div>
      )}
    </div>
  )
}

function ChunkRow({ domain, local, sync }: { domain: string; local: number; sync: number }) {
  return (
    <>
      <span className="capitalize">{domain}</span>
      <span className="text-center tabular-nums">{local}</span>
      <span className="text-center tabular-nums">{sync}</span>
    </>
  )
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  } catch {
    return iso
  }
}
