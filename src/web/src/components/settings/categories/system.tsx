// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  RefreshCw, Download, Upload, Trash2, AlertTriangle,
} from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import {
  SettingRow, AdvancedDisclosure, ConfirmActionButton, ReadOnlyEnvHint,
} from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import {
  fetchSystemCheck, fetchStorageMetrics,
  triggerSyncExport, triggerSyncImport,
  fetchKBStats, adminRebuildIndexes, adminRescore, adminRegenerateSummaries, adminClearDomain,
} from "@/lib/api"
import { logSwallowedError } from "@/lib/log-swallowed"
import { ConnectionSection } from "@/components/settings/connection-section"
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
                style={{ width: `${Math.max(seg.pct, seg.mb > 0 ? 0.5 : 0)}%` }}
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
          <ReadOnlyEnvHint envVar="CERID_FEATURE_memory_recall" />
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

// ── KB Maintenance / Danger Zone ──────────────────────────────────────────────

function KBMaintenanceSection() {
  const qc = useQueryClient()
  const rebuildDef = getDef("system.danger.rebuildIndexes")!
  const rescoreDef = getDef("system.danger.rescore")!
  const regenDef = getDef("system.danger.regenerateSummaries")!
  const clearDomainDef = getDef("system.danger.clearDomain")!

  const { data: kbStats, isLoading: statsLoading, isError: statsError, refetch: refetchStats } = useQuery({
    queryKey: ["kb-stats"],
    queryFn: fetchKBStats,
    staleTime: 30_000,
  })

  const [rebuildResult, setRebuildResult] = useState<string | null>(null)
  const [rescoreResult, setRescoreResult] = useState<string | null>(null)
  const [regenResult, setRegenResult] = useState<string | null>(null)
  const [clearDomainInput, setClearDomainInput] = useState("")
  const [clearError, setClearError] = useState("")

  return (
    <SectionCard
      title="KB Maintenance"
      className="border-red-500/30"
    >
      <div className="flex items-center gap-2 pb-1">
        <AlertTriangle className="h-4 w-4 text-destructive" />
        <p className="text-xs text-destructive font-medium">
          Danger Zone — these operations modify or delete knowledge base data.
        </p>
      </div>

      {/* Stats */}
      {statsLoading && <Skeleton className="h-16 w-full" />}
      {statsError && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">
            Failed to load KB stats.{" "}
            <button type="button" onClick={() => void refetchStats()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {kbStats && (
        <div className="rounded-md border p-3 text-xs text-muted-foreground density-stack">
          <div className="flex gap-4">
            <span><strong>{kbStats.total_artifacts}</strong> artifacts</span>
            <span><strong>{kbStats.total_chunks}</strong> chunks</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void refetchStats()}
              className="h-6 text-xs ml-auto gap-1"
              aria-label="Refresh KB stats"
            >
              <RefreshCw className="h-3 w-3" />
              Refresh Stats
            </Button>
          </div>
          {Object.keys(kbStats.domains).length > 0 && (
            <div className="divide-y divide-border rounded border mt-1">
              {Object.entries(kbStats.domains).map(([domain, stats]) => (
                <div key={domain} className="flex items-center justify-between px-2 py-1">
                  <span className="font-medium">{domain}</span>
                  <span>{stats.artifacts} / {stats.chunks}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <SettingRow def={rebuildDef}>
        <div className="density-stack w-full">
          <ConfirmActionButton
            danger="confirm"
            title="Rebuild indexes?"
            description="Rebuilds BM25 keyword indexes for all domains. Takes 1–5 minutes. No data is deleted."
            actionLabel="Rebuild Indexes"
            onConfirm={async () => {
              const result = await adminRebuildIndexes()
              setRebuildResult(`${result.message} (${result.domains_rebuilt} domains)`)
            }}
            variant="outline"
            size="sm"
          >
            Rebuild Indexes
          </ConfirmActionButton>
          {rebuildResult && <p className="text-xs text-muted-foreground">{rebuildResult}</p>}
        </div>
      </SettingRow>

      <SettingRow def={rescoreDef}>
        <div className="density-stack w-full">
          <ConfirmActionButton
            danger="confirm"
            title="Rescore all artifacts?"
            description="Re-computes quality scores for all artifacts. Takes 2–10 minutes. Does not change stored content."
            actionLabel="Rescore All"
            onConfirm={async () => {
              const result = await adminRescore()
              setRescoreResult(`${result.message} (${result.artifacts_scored} artifacts, avg quality ${result.avg_quality_score.toFixed(2)})`)
            }}
            variant="outline"
            size="sm"
          >
            Rescore All
          </ConfirmActionButton>
          {rescoreResult && <p className="text-xs text-muted-foreground">{rescoreResult}</p>}
        </div>
      </SettingRow>

      <SettingRow def={regenDef}>
        <div className="density-stack w-full">
          <ConfirmActionButton
            danger="confirm"
            title="Regenerate summaries?"
            description="Re-runs LLM synopsis generation for all artifacts. Takes 5–20 minutes."
            actionLabel="Regenerate Summaries"
            onConfirm={async () => {
              const result = await adminRegenerateSummaries()
              setRegenResult(`${result.message} (${result.synopses_generated} generated)`)
            }}
            variant="outline"
            size="sm"
          >
            Regenerate Summaries
          </ConfirmActionButton>
          {regenResult && <p className="text-xs text-muted-foreground">{regenResult}</p>}
        </div>
      </SettingRow>

      <SettingRow def={clearDomainDef}>
        <div className="density-stack w-full">
          <p className="text-xs text-destructive">
            Permanently deletes ALL chunks, embeddings, and artifacts in a domain. Cannot be undone.
          </p>
          <ConfirmActionButton
            danger="type-to-confirm"
            title="Clear domain — permanently delete all data?"
            description="Type the domain name exactly to confirm this irreversible deletion."
            confirmPhrase={clearDomainInput.trim() || "domain"}
            actionLabel="Delete domain data"
            onConfirm={async () => {
              if (!clearDomainInput.trim()) throw new Error("Enter a domain name")
              setClearError("")
              try {
                await adminClearDomain(clearDomainInput.trim())
                setClearDomainInput("")
                await qc.invalidateQueries({ queryKey: ["kb-stats"] })
              } catch (err) {
                setClearError(err instanceof Error ? err.message : "Clear failed")
                logSwallowedError(err, "system.clearDomain")
                throw err
              }
            }}
            variant="destructive"
            size="sm"
          >
            <Trash2 className="h-4 w-4 mr-1" />
            Clear domain
          </ConfirmActionButton>
          <Input
            value={clearDomainInput}
            onChange={(e) => setClearDomainInput(e.target.value)}
            placeholder="Enter domain name to clear"
            className="h-8 max-w-xs text-sm"
            aria-label="Domain to clear"
          />
          {clearError && <p className="text-xs text-destructive">{clearError}</p>}
        </div>
      </SettingRow>
    </SectionCard>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SystemCategory({ settings }: SettingsCategoryPageProps) {
  return (
    <div className="density-stack">
      <ConnectionSection />
      <ServerInfoSection settings={settings} />
      <PlatformSection />
      <StorageSection />
      <SyncSection />
      <InfraSection />
      <TogglesSection />
      <KBMaintenanceSection />
    </div>
  )
}
