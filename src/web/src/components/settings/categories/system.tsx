// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Download, Upload, AlertTriangle, RefreshCw, Archive, DatabaseBackup,
  ShieldCheck, ShieldAlert,
} from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/ui/empty-state"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import {
  SettingRow, AdvancedDisclosure, ConfirmActionButton, ReadOnlyEnvHint,
} from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import { useEntitlements } from "@/hooks/use-entitlements"
import { EntitlementsUnavailableNote } from "@/components/shared/entitlements-error-notice"
import {
  fetchSystemCheck, fetchStorageMetrics, fetchSyncStatus,
  triggerSyncExport, triggerSyncImport,
  fetchAuditRecords, verifyAuditChain, type AuditRecord,
} from "@/lib/api"
import { checkForUpdates } from "@/lib/api/updates"
import type { UpdateCheckResult } from "@/lib/api/updates"
import { logSwallowedError } from "@/lib/log-swallowed"
import { ConnectionSection } from "@/components/settings/connection-section"
import { PermissionsStep, getCeridBridge } from "@/components/setup/permissions-step"
import type { SettingsCategoryPageProps } from "./page-props"

// ── Helpers ───────────────────────────────────────────────────────────────────

function SectionCard({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase text-muted-foreground tracking-wider">{title}</span>
      </CardHeader>
      <CardContent className="density-stack">{children}</CardContent>
    </Card>
  )
}

// ── Update check ─────────────────────────────────────────────────────────────

type UpdateState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "up-to-date" }
  | { status: "available"; version: string; url: string | null }
  | { status: "error"; message: string }

function UpdateCheckButton() {
  const [state, setState] = useState<UpdateState>({ status: "idle" })

  const isDesktop =
    typeof window !== "undefined" &&
    !!(window as unknown as { cerid?: { app?: { checkUpdate?: unknown } } }).cerid?.app?.checkUpdate

  const handleCheck = async () => {
    setState({ status: "checking" })
    try {
      if (isDesktop) {
        const bridge = (window as unknown as { cerid: { app: { checkUpdate: () => Promise<{ success: boolean }> } } }).cerid.app
        await bridge.checkUpdate()
        // Desktop updater handles its own UI (tray/dialog). Just show feedback.
        setState({ status: "up-to-date" })
        return
      }
      const result: UpdateCheckResult = await checkForUpdates(true)
      if (result.error && !result.update_available) {
        setState({ status: "error", message: result.error })
      } else if (result.update_available) {
        setState({ status: "available", version: result.latest ?? "", url: result.release_url ?? null })
      } else {
        setState({ status: "up-to-date" })
      }
    } catch (err) {
      logSwallowedError(err, "system.checkForUpdates")
      setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" })
    }
  }

  // Tray "Check for Updates" sends app:check-update to the renderer instead of
  // running the check itself, so this listener is what makes the tray click
  // produce the same spinner/dialog feedback as clicking the button here.
  useEffect(() => {
    if (!isDesktop) return
    const bridge = (window as unknown as {
      cerid: { app: { onCheckUpdate: (cb: () => void) => () => void } }
    }).cerid.app
    return bridge.onCheckUpdate(() => {
      void handleCheck()
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handleCheck is stable per render intent; re-subscribing on identity change would drop in-flight tray clicks
  }, [isDesktop])

  return (
    <div className="density-stack w-full">
      <Button
        variant="outline"
        size="sm"
        onClick={() => void handleCheck()}
        disabled={state.status === "checking"}
        className="gap-1.5 w-fit"
        aria-label="Check for updates"
      >
        <RefreshCw className={cn("h-4 w-4", state.status === "checking" && "animate-spin")} />
        {state.status === "checking" ? "Checking…" : "Check for updates"}
      </Button>
      {state.status === "up-to-date" && (
        <p className="text-xs text-muted-foreground">Up to date</p>
      )}
      {state.status === "available" && (
        <p className="text-xs text-emerald-600 dark:text-emerald-400">
          Update available: v{state.version}
          {state.url && (
            <>{" — "}<a href={state.url} target="_blank" rel="noopener noreferrer" className="underline">Release notes</a></>
          )}
        </p>
      )}
      {state.status === "error" && (
        <p className="text-xs text-muted-foreground">Could not check: {state.message}</p>
      )}
    </div>
  )
}

// ── Server Info ───────────────────────────────────────────────────────────────

function ServerInfoSection({ settings }: Pick<SettingsCategoryPageProps, "settings">) {
  const versionDef = getDef("system.server.version")!
  const machineIdDef = getDef("system.server.machineId")!
  const tierDef = getDef("system.server.featureTier")!

  return (
    <SectionCard title="Server Info">
      <SettingRow def={versionDef}>
        <span className="font-mono text-sm">{settings.version ?? "—"}</span>
      </SettingRow>
      <SettingRow def={machineIdDef}>
        <span className="font-mono text-xs text-muted-foreground truncate max-w-48">{settings.machine_id ?? "—"}</span>
      </SettingRow>
      <SettingRow def={tierDef}>
        <ReadOnlyEnvHint envVar="CERID_TIER" />
      </SettingRow>
      <UpdateCheckButton />
    </SectionCard>
  )
}

// ── Platform Capabilities ─────────────────────────────────────────────────────

function PlatformSection() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["system-check"],
    queryFn: fetchSystemCheck,
    staleTime: 120_000,
  })
  const def = getDef("system.capabilities.platform")!

  if (isLoading) {
    return (
      <SectionCard title="Platform">
        <Skeleton className="h-12 w-full" />
      </SectionCard>
    )
  }
  if (isError || !data) {
    return (
      <SectionCard title="Platform">
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>Platform capabilities unavailable.</AlertDescription>
        </Alert>
      </SectionCard>
    )
  }

  const capabilities = [
    { label: "OS", value: data.os },
    { label: "CPU", value: data.cpu },
    { label: "CPU cores", value: data.cpu_cores != null ? String(data.cpu_cores) : "—" },
    { label: "RAM", value: `${data.ram_gb} GB` },
    { label: "GPU", value: data.gpu || "None detected" },
    { label: "GPU acceleration", value: data.gpu_acceleration || "None" },
    ...(data.gpu_type ? [{ label: "GPU type", value: data.gpu_type }] : []),
    ...(data.recommended_local_backend ? [{ label: "Recommended backend", value: data.recommended_local_backend }] : []),
  ]

  return (
    <SectionCard title="Platform">
      <SettingRow def={def}>
        <span className="text-label-xs text-muted-foreground">Detected at startup</span>
      </SettingRow>
      <div className="divide-y divide-border rounded-md border">
        {capabilities.map(({ label, value }) => (
          <Tooltip key={label}>
            <TooltipTrigger asChild>
              <div
                className="flex items-center justify-between px-3 py-1.5 text-sm"
                aria-label={`${label}: ${value}`}
              >
                <span className="text-muted-foreground text-xs">{label}</span>
                <span className="font-mono text-xs truncate ml-2 max-w-48">{value}</span>
              </div>
            </TooltipTrigger>
            <TooltipContent side="left" className="text-xs">
              {label}: {value}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </SectionCard>
  )
}

// ── Storage ───────────────────────────────────────────────────────────────────

const SERVICE_COLORS = {
  chromadb: { bg: "bg-blue-500", label: "ChromaDB" },
  neo4j: { bg: "bg-emerald-500", label: "Neo4j" },
  redis: { bg: "bg-amber-500", label: "Redis" },
  bm25: { bg: "bg-slate-500", label: "BM25" },
} as const

function statusColor(status: string): string {
  if (status === "critical") return "text-red-500"
  if (status === "warning") return "text-yellow-500"
  return "text-emerald-500"
}

function StatusBadge({ status }: { status: string }) {
  const variant = status === "critical" ? "destructive" : status === "warning" ? "outline" : "secondary"
  return <Badge variant={variant}>{status}</Badge>
}

function StorageSection() {
  const def = getDef("system.storage.usage")!
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["system-storage"],
    queryFn: fetchStorageMetrics,
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  if (isLoading) {
    return (
      <SectionCard title="Storage">
        <Skeleton className="h-16 w-full" />
      </SectionCard>
    )
  }
  if (isError || !data) {
    return (
      <SectionCard title="Storage">
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load storage metrics.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      </SectionCard>
    )
  }

  const { chromadb, neo4j, redis, bm25, total_mb, limit_mb, usage_pct, status } = data
  const segments = [
    { key: "chromadb" as const, pct: limit_mb > 0 ? (chromadb.disk_mb / limit_mb) * 100 : 0, mb: chromadb.disk_mb, detail: `${chromadb.collections} collections, ${chromadb.chunks.toLocaleString()} chunks` },
    { key: "neo4j" as const, pct: limit_mb > 0 ? (neo4j.disk_mb / limit_mb) * 100 : 0, mb: neo4j.disk_mb, detail: `${neo4j.nodes.toLocaleString()} nodes, ${neo4j.relationships.toLocaleString()} rels` },
    { key: "redis" as const, pct: limit_mb > 0 ? (redis.memory_mb / limit_mb) * 100 : 0, mb: redis.memory_mb, detail: `${redis.keys.toLocaleString()} keys, peak ${redis.peak_mb} MB` },
    { key: "bm25" as const, pct: limit_mb > 0 ? (bm25.disk_mb / limit_mb) * 100 : 0, mb: bm25.disk_mb, detail: `${bm25.index_count} indexes` },
  ]

  return (
    <SectionCard title="Storage">
      <SettingRow def={def}>
        <div className="flex items-center gap-2">
          <span className={cn("font-mono text-sm font-medium", statusColor(status))}>
            {total_mb.toFixed(1)} / {limit_mb} MB
          </span>
          <StatusBadge status={status} />
        </div>
      </SettingRow>
      <div>
        <div className="relative h-3 w-full rounded-full bg-muted overflow-hidden" role="progressbar" aria-valuenow={Math.round(usage_pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Storage usage">
          <div className="absolute inset-0 flex">
            {segments.map((seg) => (
              <div
                key={seg.key}
                className={cn(SERVICE_COLORS[seg.key].bg, "h-full transition-all duration-500")}
                style={{ width: `${Math.max(seg.pct, seg.mb > 0 ? 0.5 : 0)}%` }} // drift-allowed: StorageBar stacked-segment width is runtime usage data, no static equivalent
              />
            ))}
          </div>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          {segments.map((seg) => (
            <Tooltip key={seg.key}>
              <TooltipTrigger asChild>
                <div
                  className="flex items-center gap-1.5 text-xs text-muted-foreground"
                  aria-label={`${SERVICE_COLORS[seg.key].label}: ${seg.mb.toFixed(1)} MB — ${seg.detail}`}
                >
                  <div className={cn("h-2 w-2 rounded-full shrink-0", SERVICE_COLORS[seg.key].bg)} />
                  <span>{SERVICE_COLORS[seg.key].label}</span>
                  <span className="font-mono">{seg.mb.toFixed(1)} MB</span>
                </div>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="text-xs">
                <p className="font-medium">{SERVICE_COLORS[seg.key].label}: {seg.mb.toFixed(1)} MB</p>
                <p className="text-muted-foreground">{seg.detail}</p>
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </div>
    </SectionCard>
  )
}

// ── Sync ──────────────────────────────────────────────────────────────────────

function SyncSection() {
  const conflictDef = getDef("system.sync.conflictStrategy")!
  const exportDef = getDef("system.sync.export")!
  const importDef = getDef("system.sync.import")!

  const [conflictStrategy, setConflictStrategy] = useState(() => {
    try { return localStorage.getItem("cerid-sync-conflict-strategy") ?? "remote_wins" } catch { return "remote_wins" }
  })
  const [exporting, setExporting] = useState(false)
  const [exportResult, setExportResult] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<string | null>(null)
  const [importError, setImportError] = useState("")

  const handleConflictChange = (value: string) => {
    setConflictStrategy(value)
    try { localStorage.setItem("cerid-sync-conflict-strategy", value) } catch { /* noop */ }
  }

  const handleExport = async () => {
    setExporting(true)
    setExportResult(null)
    try {
      const result = await triggerSyncExport()
      setExportResult(`Export complete — ${(result as { artifacts_exported?: number }).artifacts_exported ?? 0} artifacts`)
    } catch (err) {
      setExportResult("Export failed: " + (err instanceof Error ? err.message : "Unknown error"))
      logSwallowedError(err, "system.syncExport")
    } finally {
      setExporting(false)
    }
  }

  const handleImport = async () => {
    setImportError("")
    setImportResult(null)
    try {
      const result = await triggerSyncImport({ conflict_strategy: conflictStrategy })
      setImportResult(`Import complete — ${(result as { artifacts_imported?: number }).artifacts_imported ?? 0} artifacts`)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Import failed")
      logSwallowedError(err, "system.syncImport")
      throw err
    }
  }

  return (
    <SectionCard title="Sync">
      <SettingRow def={conflictDef}>
        <Select value={conflictStrategy} onValueChange={handleConflictChange}>
          <SelectTrigger className="w-40 h-8" aria-label="Conflict strategy">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="remote_wins">Remote wins</SelectItem>
            <SelectItem value="local_wins">Local wins</SelectItem>
            <SelectItem value="merge">Merge</SelectItem>
          </SelectContent>
        </Select>
      </SettingRow>
      <SettingRow def={exportDef}>
        <div className="density-stack w-full">
          <Button variant="outline" size="sm" onClick={handleExport} disabled={exporting} className="gap-1.5 w-fit">
            <Download className={`h-4 w-4 ${exporting ? "animate-pulse" : ""}`} />
            {exporting ? "Exporting…" : "Export KB"}
          </Button>
          {exportResult && <p className="text-xs text-muted-foreground">{exportResult}</p>}
        </div>
      </SettingRow>
      <SettingRow def={importDef}>
        <div className="density-stack w-full">
          <ConfirmActionButton
            danger="confirm"
            title="Import knowledge base?"
            description={`This imports the sync archive using the "${conflictStrategy.replace("_", " ")}" conflict strategy. ${conflictStrategy === "remote_wins" ? "Local data may be overwritten." : conflictStrategy === "local_wins" ? "Incoming data that conflicts will be discarded." : "Field-level merge will be attempted."}`}
            actionLabel="Import"
            onConfirm={handleImport}
            variant="outline"
            size="sm"
            className="gap-1.5"
          >
            <Upload className="h-4 w-4" />
            Import KB
          </ConfirmActionButton>
          {importResult && <p className="text-xs text-muted-foreground">{importResult}</p>}
          {importError && <p className="text-xs text-destructive">{importError}</p>}
        </div>
      </SettingRow>
    </SectionCard>
  )
}

// ── Backup ────────────────────────────────────────────────────────────────────

/** Desktop-only richer export: main-process handler runs scripts/backup-kb.sh
    into a user-chosen folder. Returns null in browser builds. */
function getDesktopExportData(): (() => Promise<{ success: boolean; path?: string; error?: string }>) | null {
  if (typeof window === "undefined") return null
  const cerid = (window as Window & {
    cerid?: { app?: { exportData?: () => Promise<{ success: boolean; path?: string; error?: string }> } }
  }).cerid
  return cerid?.app?.exportData ?? null
}

function BackupSection() {
  const backupDef = getDef("system.sync.backup")!
  const queryClient = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["sync-status"],
    queryFn: fetchSyncStatus,
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [nativeRunning, setNativeRunning] = useState(false)
  const [nativeResult, setNativeResult] = useState<string | null>(null)
  const exportData = getDesktopExportData()

  const handleNativeExport = async () => {
    if (!exportData) return
    setNativeRunning(true)
    setNativeResult(null)
    try {
      const res = await exportData()
      if (res.success) {
        setNativeResult(res.path ? `Backup archive written to ${res.path}.` : "Backup archive written.")
      } else if (res.error !== "cancelled") {
        setNativeResult("Backup export failed: " + (res.error ?? "Unknown error"))
      }
    } catch (err) {
      setNativeResult("Backup export failed: " + (err instanceof Error ? err.message : "Unknown error"))
      logSwallowedError(err, "system.desktopExportData")
    } finally {
      setNativeRunning(false)
    }
  }

  const handleFullExport = async () => {
    setRunning(true)
    setResult(null)
    try {
      await triggerSyncExport({ full: true })
      setResult("Full backup export complete.")
      await queryClient.invalidateQueries({ queryKey: ["sync-status"] })
    } catch (err) {
      setResult("Full backup export failed: " + (err instanceof Error ? err.message : "Unknown error"))
      logSwallowedError(err, "system.syncBackupExport")
    } finally {
      setRunning(false)
    }
  }

  if (isLoading) {
    return (
      <SectionCard title="Backup">
        <Skeleton className="h-24 w-full" />
      </SectionCard>
    )
  }

  if (isError || !data) {
    return (
      <SectionCard title="Backup">
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load backup status.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      </SectionCard>
    )
  }

  const { manifest, local } = data
  const totalChromaChunks = Object.values(local.chroma_chunks ?? {}).reduce((sum, n) => sum + n, 0)
  const counts: Array<[string, number]> = [
    ["Artifacts", local.neo4j_artifacts],
    ["Memories", local.neo4j_memories],
    ["Entities", local.neo4j_entities],
    ["Relationships", local.neo4j_relationships],
    ["Domains", local.neo4j_domains],
    ["Chroma chunks", totalChromaChunks],
    ["Redis entries", local.redis_entries],
  ]

  return (
    <SectionCard title="Backup">
      {manifest ? (
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm text-muted-foreground">
            Last {manifest.is_incremental ? "incremental" : "full"} export
          </span>
          <span className="font-mono text-xs tabular-nums">
            {manifest.last_exported_at ? new Date(manifest.last_exported_at).toLocaleString() : "—"}
          </span>
        </div>
      ) : (
        <EmptyState
          icon={Archive}
          title="No export yet"
          description="Run a full backup export below to create the first sync snapshot."
        />
      )}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        {counts.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>{label}</span>
            <span className="font-mono tabular-nums text-foreground">{value.toLocaleString()}</span>
          </div>
        ))}
      </div>
      <SettingRow def={backupDef}>
        <div className="density-stack w-full">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleFullExport()}
            disabled={running}
            className="gap-1.5 w-fit"
          >
            <DatabaseBackup className={cn("h-4 w-4", running && "animate-pulse")} />
            {running ? "Exporting…" : "Full backup export"}
          </Button>
          <p className="text-label-xs text-muted-foreground">
            Writes a full snapshot to the sync directory. Run scripts/cerid-backup.sh separately for a portable, restorable archive.
          </p>
          {result && <p className="text-xs text-muted-foreground">{result}</p>}
          {exportData && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleNativeExport()}
                disabled={nativeRunning}
                className="gap-1.5 w-fit"
              >
                <Archive className={cn("h-4 w-4", nativeRunning && "animate-pulse")} />
                {nativeRunning ? "Exporting…" : "Save backup archive to this Mac…"}
              </Button>
              <p className="text-label-xs text-muted-foreground">
                Exports a portable, restorable archive to a folder you choose on this machine — no shell required.
              </p>
              {nativeResult && <p className="text-xs text-muted-foreground">{nativeResult}</p>}
            </>
          )}
        </div>
      </SettingRow>
    </SectionCard>
  )
}

// ── Infrastructure env rows ───────────────────────────────────────────────────

function InfraSection() {
  const bifrostDef = getDef("system.infra.bifrostUrl")!
  const chromaDef = getDef("system.infra.chromaUrl")!
  const neo4jDef = getDef("system.infra.neo4jUri")!
  const archiveDef = getDef("system.infra.archivePath")!
  const chunkingDef = getDef("system.infra.chunkingMode")!
  const syncDef = getDef("system.infra.syncBackend")!

  return (
    <SectionCard title="Infrastructure">
      <AdvancedDisclosure category="system" group="infra">
        <SettingRow def={bifrostDef}>
          <ReadOnlyEnvHint envVar="BIFROST_URL" />
        </SettingRow>
        <SettingRow def={chromaDef}>
          <ReadOnlyEnvHint envVar="CHROMA_URL" />
        </SettingRow>
        <SettingRow def={neo4jDef}>
          <ReadOnlyEnvHint envVar="NEO4J_URI" />
        </SettingRow>
        <SettingRow def={archiveDef}>
          <ReadOnlyEnvHint envVar="ARCHIVE_PATH" />
        </SettingRow>
        <SettingRow def={chunkingDef}>
          <ReadOnlyEnvHint envVar="CHUNKING_MODE" />
        </SettingRow>
        <SettingRow def={syncDef}>
          <ReadOnlyEnvHint envVar="SYNC_BACKEND" />
        </SettingRow>
      </AdvancedDisclosure>
    </SectionCard>
  )
}

// ── Backend toggles (env-only, read-only) ─────────────────────────────────────

function TogglesSection() {
  const memoryDef = getDef("system.toggles.memoryRecall")!
  const parentChildDef = getDef("system.toggles.parentChildRetrieval")!
  const contradictionDef = getDef("system.toggles.contradictionLedger")!

  return (
    <SectionCard title="Pipeline Toggles">
      <AdvancedDisclosure category="system" group="toggles">
        <SettingRow def={memoryDef}>
          <ReadOnlyEnvHint envVar="ENABLE_MEMORY_RECALL" />
        </SettingRow>
        <SettingRow def={parentChildDef}>
          <ReadOnlyEnvHint envVar="CERID_FEATURE_parent_child_retrieval" />
        </SettingRow>
        <SettingRow def={contradictionDef}>
          <ReadOnlyEnvHint envVar="CERID_FEATURE_contradiction_ledger" />
        </SettingRow>
      </AdvancedDisclosure>
    </SectionCard>
  )
}

// ── Audit Log (RA-32) ──────────────────────────────────────────────────────

function VerifyChainChip() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["audit-log-verify"],
    queryFn: verifyAuditChain,
    staleTime: 30_000,
  })

  if (isLoading) return <Skeleton className="h-6 w-32" />
  if (isError || !data) {
    return (
      <Badge variant="outline" className="gap-1 text-label-xs">
        <ShieldAlert className="h-3 w-3" aria-hidden="true" />
        Verify failed
        <button type="button" onClick={() => void refetch()} className="underline">retry</button>
      </Badge>
    )
  }
  return data.ok ? (
    <Badge variant="secondary" className="gap-1 text-label-xs text-emerald-600 dark:text-emerald-400">
      <ShieldCheck className="h-3 w-3" aria-hidden="true" />
      Chain verified ({data.checked} records)
    </Badge>
  ) : (
    <Badge variant="destructive" className="gap-1 text-label-xs">
      <ShieldAlert className="h-3 w-3" aria-hidden="true" />
      Tampered at seq {data.broken_at} — {data.reason}
    </Badge>
  )
}

function AuditLogTable() {
  const [outcome, setOutcome] = useState<"" | "success" | "failure" | "denied">("")

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["audit-log-records", outcome],
    queryFn: () => fetchAuditRecords({ limit: 25, outcome: outcome || undefined }),
    staleTime: 15_000,
  })

  return (
    <div className="w-full density-stack">
      <div className="flex items-center gap-2">
        <VerifyChainChip />
        <Select value={outcome || "all"} onValueChange={(v) => setOutcome(v === "all" ? "" : v as typeof outcome)}>
          <SelectTrigger className="ml-auto h-8 w-32" aria-label="Filter by outcome">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All outcomes</SelectItem>
            <SelectItem value="success">Success</SelectItem>
            <SelectItem value="failure">Failure</SelectItem>
            <SelectItem value="denied">Denied</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void refetch()}
          className="h-8 gap-1 text-xs"
          aria-label="Refresh audit log"
        >
          <RefreshCw className="h-3 w-3" />
          Refresh
        </Button>
      </div>

      {isLoading && <Skeleton className="h-40 w-full" />}

      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load audit log.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}

      {data && data.records.length === 0 && (
        <EmptyState
          icon={ShieldCheck}
          title="No audit records"
          description={outcome ? `No records with outcome "${outcome}".` : "No administrative or security actions have been recorded yet."}
        />
      )}

      {data && data.records.length > 0 && (
        <div className="divide-y divide-border rounded-md border text-xs">
          {data.records.map((r: AuditRecord) => (
            <div key={r.seq} className="flex items-center gap-3 px-3 py-1.5">
              <span className="w-10 shrink-0 font-mono text-muted-foreground">#{r.seq}</span>
              <span className="w-40 shrink-0 truncate font-mono text-muted-foreground">{r.ts}</span>
              <span className="w-24 shrink-0 truncate">{r.actor}</span>
              <span className="min-w-0 flex-1 truncate font-medium">{r.action}</span>
              <Badge
                variant={r.outcome === "success" ? "secondary" : "destructive"}
                className="shrink-0 text-label-xs"
              >
                {r.outcome}
              </Badge>
            </div>
          ))}
        </div>
      )}
      {data && (
        <p className="text-label-xs text-muted-foreground">
          Showing {data.records.length} of {data.total} record{data.total !== 1 && "s"}.
        </p>
      )}
    </div>
  )
}

function AuditLogGroup() {
  const def = getDef("system.audit.log")!
  const { forDef, isError: entitlementsError } = useEntitlements()
  const locked = forDef(def).state !== "available"

  return (
    <SectionCard title="Audit Log">
      <SettingRow def={def} />
      {entitlementsError ? (
        <EntitlementsUnavailableNote />
      ) : locked ? (
        <p className="text-label-xs text-muted-foreground">
          Enterprise license required to view audit records.
        </p>
      ) : (
        <AuditLogTable />
      )}
    </SectionCard>
  )
}

// ── macOS Permissions (desktop app only) ──────────────────────────────────────
//
// Permanent home for the per-machine TCC grants (GUI spec item 3). Mounts the
// same PermissionsStep the desktop setup wizard uses, but only when the
// desktop bridge is present — in browser builds the section renders nothing
// (the component's own browser-fallback copy would be noise here).

function MacPermissionsSection() {
  const def = getDef("system.permissions")!
  if (getCeridBridge() === null) return null
  return (
    <SectionCard title="macOS Permissions">
      <SettingRow def={def} />
      <PermissionsStep hideIntro />
    </SectionCard>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SystemCategory({ settings }: SettingsCategoryPageProps) {
  return (
    <div className="density-stack">
      <ConnectionSection />
      <MacPermissionsSection />
      <ServerInfoSection settings={settings} />
      <PlatformSection />
      <StorageSection />
      <SyncSection />
      <BackupSection />
      <InfraSection />
      <TogglesSection />
      <AuditLogGroup />
    </div>
  )
}
