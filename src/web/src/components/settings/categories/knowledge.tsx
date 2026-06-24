// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  FolderOpen, Database,
  Plus, Scan, Trash2, AlertTriangle, CheckCircle,
  ChevronDown, ChevronRight, RefreshCw,
} from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { EmptyState } from "@/components/ui/empty-state"
import {
  SettingRow, AdvancedDisclosure, ConfirmActionButton, ToggleRow, ReadOnlyEnvHint,
} from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import {
  fetchWatchedFolders, addWatchedFolder, removeWatchedFolder, scanWatchedFolder,
  updateWatchedFolder,
  fetchDataSources, enableDataSource, disableDataSource,
  fetchBriefSettings, updateBriefSettings,
  fetchKBStats, adminRebuildIndexes, adminRescore, adminRegenerateSummaries, adminClearDomain,
  type WatchedFolder, type VaultConfig,
} from "@/lib/api"
import { logSwallowedError } from "@/lib/log-swallowed"
import type { SettingsCategoryPageProps } from "./page-props"

// ── Helpers ─────────────────────────────────────────────────────────────────

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

// ── Watched Folders ──────────────────────────────────────────────────────────

function WatchedFolderRow({
  folder,
  onRemoved,
  onUpdated,
}: {
  folder: WatchedFolder
  onRemoved: () => void
  onUpdated: () => void
}) {
  const [scanStatus, setScanStatus] = useState<"idle" | "scanning" | "done" | "error">("idle")
  const [scanMsg, setScanMsg] = useState("")
  const [vaultOpen, setVaultOpen] = useState(false)
  const [vaultConfig, setVaultConfig] = useState<VaultConfig>(folder.vault_config ?? {})
  const [vaultDirty, setVaultDirty] = useState(false)
  const [vaultSaving, setVaultSaving] = useState(false)
  const [vaultError, setVaultError] = useState("")
  const [vaultProfile] = useState<{ yaml_present: boolean; profile: Record<string, unknown> } | null>(null)

  const handleScan = async () => {
    setScanStatus("scanning")
    setScanMsg("")
    try {
      const res = await scanWatchedFolder(folder.id)
      setScanStatus("done")
      setScanMsg(res.status === "queued" ? "Scan queued" : "Scan started")
    } catch (err) {
      setScanStatus("error")
      setScanMsg(err instanceof Error ? err.message : "Scan failed")
      logSwallowedError(err, "knowledge.scanWatchedFolder")
    }
  }

  const handleToggleActive = async (enabled: boolean) => {
    try {
      await updateWatchedFolder(folder.id, { enabled })
      onUpdated()
    } catch (err) {
      logSwallowedError(err, "knowledge.updateWatchedFolder.enabled")
    }
  }

  const handleToggleSearch = async (search_enabled: boolean) => {
    try {
      await updateWatchedFolder(folder.id, { search_enabled })
      onUpdated()
    } catch (err) {
      logSwallowedError(err, "knowledge.updateWatchedFolder.search_enabled")
    }
  }

  const handleToggleVault = async (is_vault: boolean) => {
    try {
      await updateWatchedFolder(folder.id, { is_vault })
      onUpdated()
    } catch (err) {
      logSwallowedError(err, "knowledge.updateWatchedFolder.is_vault")
    }
  }

  const handleSaveVaultConfig = async () => {
    setVaultSaving(true)
    setVaultError("")
    try {
      await updateWatchedFolder(folder.id, {
        vault_config: {
          mocs_folders: vaultConfig.mocs_folders,
          daily_folders: vaultConfig.daily_folders,
          templates_folders: vaultConfig.templates_folders,
          attachments_folders: vaultConfig.attachments_folders,
          skip_folders: vaultConfig.skip_folders,
          default_domain: vaultConfig.default_domain,
        },
      })
      setVaultDirty(false)
      onUpdated()
    } catch (err) {
      setVaultError(err instanceof Error ? err.message : "Save failed")
      logSwallowedError(err, "knowledge.updateWatchedFolder.vault_config")
    } finally {
      setVaultSaving(false)
    }
  }

  const setVcField = (field: keyof VaultConfig, value: string) => {
    setVaultConfig((prev) => ({
      ...prev,
      [field]: field === "default_domain"
        ? value
        : value.split(",").map((s) => s.trim()).filter(Boolean),
    }))
    setVaultDirty(true)
  }

  return (
    <div className="border rounded-md p-3 density-stack">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="font-medium truncate text-sm">{folder.label || folder.path}</span>
            {folder.is_vault && <Badge variant="outline" className="text-label-xs">Vault</Badge>}
          </div>
          {folder.label && (
            <p className="text-label-xs text-muted-foreground mt-0.5 truncate pl-6">{folder.path}</p>
          )}
          {folder.stats && (
            <p className="text-label-xs text-muted-foreground pl-6">
              {folder.stats.ingested} ingested · {folder.stats.errored} errors
              {folder.last_scanned_at && ` · Last scanned ${new Date(folder.last_scanned_at).toLocaleDateString()}`}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleScan}
            disabled={scanStatus === "scanning"}
            aria-label={`Scan ${folder.label || folder.path} now`}
            className="h-8 px-2"
          >
            <Scan className="h-4 w-4" />
            <span className="sr-only">Scan now</span>
          </Button>
          <ConfirmActionButton
            danger="confirm"
            title="Remove watched folder?"
            description={`Stop watching "${folder.label || folder.path}". Existing ingested content is not deleted.`}
            actionLabel="Remove"
            onConfirm={async () => {
              await removeWatchedFolder(folder.id)
              onRemoved()
            }}
            variant="ghost"
            size="sm"
            className="h-8 px-2"
          >
            <Trash2 className="h-4 w-4" aria-hidden />
            <span className="sr-only">Remove folder</span>
          </ConfirmActionButton>
        </div>
      </div>

      {scanStatus === "done" && (
        <div className="flex items-center gap-1.5 text-label-xs text-green-600 dark:text-green-400 pl-6">
          <CheckCircle className="h-3 w-3" />
          {scanMsg}
        </div>
      )}
      {scanStatus === "error" && (
        <div className="flex items-center gap-1.5 text-label-xs text-destructive pl-6">
          <AlertTriangle className="h-3 w-3" />
          {scanMsg}
        </div>
      )}

      <div className="flex items-center gap-4 pl-6">
        <ToggleRow
          label="Active"
          enabled={folder.enabled}
          onToggle={handleToggleActive}
        />
        <ToggleRow
          label="Searchable"
          enabled={folder.search_enabled}
          onToggle={handleToggleSearch}
        />
      </div>

      {/* Vault config expander */}
      <div className="pl-6">
        <button
          type="button"
          onClick={() => setVaultOpen((v) => !v)}
          className="flex items-center gap-1 text-label-xs text-muted-foreground hover:text-foreground transition-colors"
          aria-expanded={vaultOpen}
        >
          {vaultOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          Vault settings
        </button>
        {vaultOpen && (
          <div className="mt-2 density-stack border-l border-border/50 pl-3">
            <ToggleRow
              label="This folder is a vault"
              enabled={!!folder.is_vault}
              onToggle={handleToggleVault}
              info="Enable Obsidian-style vault semantics — subfolder names infer document roles."
            />
            {folder.is_vault && (
              <>
                <div className="density-stack">
                  {(
                    [
                      ["MOCs folder", "mocs_folders"],
                      ["Daily notes folder", "daily_folders"],
                      ["Templates folder", "templates_folders"],
                      ["Attachments folder", "attachments_folders"],
                      ["Skip folders (comma-separated)", "skip_folders"],
                    ] as [string, keyof VaultConfig][]
                  ).map(([label, field]) => (
                    <div key={field} className="grid grid-cols-[1fr_auto] gap-2 items-center">
                      <Label className="text-sm">{label}</Label>
                      <Input
                        value={
                          field === "default_domain"
                            ? (vaultConfig[field] ?? "")
                            : (Array.isArray(vaultConfig[field]) ? (vaultConfig[field] as string[]).join(", ") : "")
                        }
                        onChange={(e) => setVcField(field, e.target.value)}
                        placeholder={field === "skip_folders" ? ".trash, archive" : `e.g. ${field.replace(/_folders$/, "").replace(/_/g, "-")}`}
                        className="h-8 text-sm max-w-48"
                      />
                    </div>
                  ))}
                </div>
                {vaultProfile && (
                  <div className="text-label-xs text-muted-foreground rounded bg-muted/50 p-2">
                    <span className="font-medium">Effective profile</span>
                    {vaultProfile.yaml_present && <Badge variant="outline" className="ml-1 text-label-xs">From .cerid-vault.yaml</Badge>}
                    <pre className="mt-1 overflow-auto">{JSON.stringify(vaultProfile.profile, null, 2)}</pre>
                  </div>
                )}
                {vaultError && (
                  <Alert variant="destructive">
                    <AlertDescription className="text-label-xs">{vaultError}</AlertDescription>
                  </Alert>
                )}
                {vaultDirty && (
                  <Button
                    size="sm"
                    onClick={handleSaveVaultConfig}
                    disabled={vaultSaving}
                    className="h-8"
                  >
                    {vaultSaving ? "Saving…" : "Save vault config"}
                  </Button>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function WatchedFoldersGroup() {
  const qc = useQueryClient()
  const {
    data, isLoading, isError, refetch,
  } = useQuery({
    queryKey: ["watched-folders"],
    queryFn: fetchWatchedFolders,
    staleTime: 30_000,
  })
  const [showAdd, setShowAdd] = useState(false)
  const [newPath, setNewPath] = useState("")
  const [newLabel, setNewLabel] = useState("")
  const [addError, setAddError] = useState("")
  const [adding, setAdding] = useState(false)

  const handleAdd = async () => {
    if (!newPath.trim()) { setAddError("Path is required"); return }
    setAdding(true)
    setAddError("")
    try {
      await addWatchedFolder({ path: newPath.trim(), label: newLabel.trim() || undefined })
      await qc.invalidateQueries({ queryKey: ["watched-folders"] })
      setNewPath("")
      setNewLabel("")
      setShowAdd(false)
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Add failed")
      logSwallowedError(err, "knowledge.addWatchedFolder")
    } finally {
      setAdding(false)
    }
  }

  const folders = data?.folders ?? []

  return (
    <SectionCard title="Watched Folders">
      {isLoading && (
        <div className="density-stack">
          <Skeleton className="h-14 w-full rounded-md" />
          <Skeleton className="h-14 w-full rounded-md" />
        </div>
      )}
      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load watched folders.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {!isLoading && !isError && folders.length === 0 && !showAdd && (
        <EmptyState
          icon={FolderOpen}
          title="No watched folders"
          description="Add a directory path and Cerid will auto-ingest new and changed files."
        />
      )}
      {folders.map((f) => (
        <WatchedFolderRow
          key={f.id}
          folder={f}
          onRemoved={() => void qc.invalidateQueries({ queryKey: ["watched-folders"] })}
          onUpdated={() => void qc.invalidateQueries({ queryKey: ["watched-folders"] })}
        />
      ))}
      {showAdd ? (
        <div className="border border-dashed rounded-md p-3 density-stack">
          <div className="density-stack">
            <div>
              <Label htmlFor="new-folder-path" className="text-sm">Folder path</Label>
              <Input
                id="new-folder-path"
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
                placeholder="/home/user/documents"
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="new-folder-label" className="text-sm">Label (optional)</Label>
              <Input
                id="new-folder-label"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                placeholder="My Documents"
                className="mt-1"
              />
            </div>
          </div>
          {addError && (
            <Alert variant="destructive">
              <AlertDescription className="text-label-xs">{addError}</AlertDescription>
            </Alert>
          )}
          <div className="flex gap-2">
            <Button size="sm" onClick={handleAdd} disabled={adding}>
              {adding ? "Adding…" : "Add folder"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setShowAdd(false); setAddError("") }}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowAdd(true)}
          className="gap-1.5"
        >
          <Plus className="h-4 w-4" />
          Add folder
        </Button>
      )}
    </SectionCard>
  )
}

// ── Data Sources ─────────────────────────────────────────────────────────────

function DataSourcesGroup() {
  const qc = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["data-sources"],
    queryFn: fetchDataSources,
    staleTime: 30_000,
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const handleToggle = async (name: string, enabled: boolean) => {
    setErrors((prev) => ({ ...prev, [name]: "" }))
    try {
      if (enabled) {
        await enableDataSource(name)
      } else {
        await disableDataSource(name)
      }
      await qc.invalidateQueries({ queryKey: ["data-sources"] })
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Toggle failed"
      setErrors((prev) => ({ ...prev, [name]: msg }))
      logSwallowedError(err, "knowledge.toggleDataSource")
    }
  }

  const def = getDef("knowledge.sources.enable")!
  const sources = data?.sources ?? []

  return (
    <SectionCard title="Data Sources">
      {isLoading && (
        <div className="density-stack">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full rounded-md" />)}
        </div>
      )}
      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load data sources.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {!isLoading && !isError && sources.length === 0 && (
        <EmptyState
          icon={Database}
          title="No data sources configured"
          description="Data sources appear once your deployment includes external API adapters."
        />
      )}
      {sources.map((src) => (
        <div key={src.name}>
          <SettingRow def={def}>
            <div className="flex flex-col gap-1">
              <div className="flex items-center justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium">{src.name}</span>
                  {src.description && (
                    <p className="text-label-xs text-muted-foreground">{src.description}</p>
                  )}
                  {!src.configured && src.api_key_env_var && (
                    <ReadOnlyEnvHint envVar={src.api_key_env_var} />
                  )}
                </div>
                <Switch
                  checked={src.enabled}
                  onCheckedChange={(checked) => void handleToggle(src.name, checked)}
                  disabled={!src.configured}
                  aria-label={`${src.enabled ? "Disable" : "Enable"} ${src.name}`}
                  className="shrink-0"
                />
              </div>
              {errors[src.name] && (
                <Alert variant="destructive">
                  <AlertDescription className="text-label-xs">{errors[src.name]}</AlertDescription>
                </Alert>
              )}
            </div>
          </SettingRow>
        </div>
      ))}
    </SectionCard>
  )
}

// ── Ingestion ─────────────────────────────────────────────────────────────────

function IngestionGroup({ settings, patch }: Pick<SettingsCategoryPageProps, "settings" | "patch">) {
  const storageDef = getDef("knowledge.ingestion.storageMode")!
  const categorizeDef = getDef("knowledge.ingestion.categorizeMode")!
  const chunkDef = getDef("knowledge.ingestion.chunkSize")!
  const contextualDef = getDef("knowledge.ingestion.contextualChunks")!

  return (
    <SectionCard title="Ingestion">
      <SettingRow def={storageDef}>
        <Select
          value={settings.storage_mode ?? "extract_only"}
          onValueChange={(v) => void patch({ storage_mode: v })}
        >
          <SelectTrigger className="w-40 h-8" aria-label="Storage mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="extract_only">Extract only</SelectItem>
            <SelectItem value="archive">Archive</SelectItem>
          </SelectContent>
        </Select>
      </SettingRow>
      <SettingRow def={categorizeDef} renderControl={(ent) => (
        <Select
          value={settings.categorize_mode ?? "smart"}
          onValueChange={(v) => void patch({ categorize_mode: v })}
          disabled={ent.state === "locked"}
        >
          <SelectTrigger className="w-44 h-8" aria-label="Categorization mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="manual">Manual</SelectItem>
            <SelectItem value="smart">Smart</SelectItem>
            <SelectItem value="pro" disabled={ent.state === "locked"}>
              Pro — multi-label
            </SelectItem>
          </SelectContent>
        </Select>
      )} />
      <AdvancedDisclosure category="knowledge" group="ingestion">
        <SettingRow def={chunkDef}>
          <span className="text-sm font-mono text-muted-foreground">
            {settings.chunk_max_tokens ?? "—"}
          </span>
        </SettingRow>
        <SettingRow def={contextualDef}>
          <Switch
            checked={!!settings.enable_contextual_chunks}
            onCheckedChange={(v) => void patch({ enable_contextual_chunks: v })}
            aria-label="Contextual chunks"
          />
        </SettingRow>
      </AdvancedDisclosure>
    </SectionCard>
  )
}

// ── Briefs ─────────────────────────────────────────────────────────────────

function BriefsGroup() {
  const qc = useQueryClient()
  const { data: briefSettings, isLoading: briefLoading } = useQuery({
    queryKey: ["brief-settings"],
    queryFn: fetchBriefSettings,
    staleTime: 60_000,
  })
  const [error, setError] = useState("")

  const saveField = async (update: Partial<{ write_to_vault: boolean; vault_id: string | null; vault_folder: string }>) => {
    if (!briefSettings) return
    setError("")
    try {
      await updateBriefSettings({ ...briefSettings, ...update })
      await qc.invalidateQueries({ queryKey: ["brief-settings"] })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed")
      logSwallowedError(err, "knowledge.updateBriefSettings")
    }
  }

  const { data: foldersData } = useQuery({
    queryKey: ["watched-folders"],
    queryFn: fetchWatchedFolders,
    staleTime: 30_000,
  })
  const vaultFolders = (foldersData?.folders ?? []).filter((f) => f.is_vault)

  const writeToVaultDef = getDef("knowledge.briefs.writeToVault")!
  const targetVaultDef = getDef("knowledge.briefs.targetVault")!
  const folderPrefixDef = getDef("knowledge.briefs.folderPrefix")!

  if (briefLoading) {
    return (
      <SectionCard title="Briefs">
        <Skeleton className="h-10 w-full" />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Briefs">
      <SettingRow def={writeToVaultDef}>
        <Switch
          checked={!!briefSettings?.write_to_vault}
          onCheckedChange={(v) => void saveField({ write_to_vault: v })}
          aria-label="Write briefs to vault"
        />
      </SettingRow>
      {briefSettings?.write_to_vault && (
        <>
          <SettingRow def={targetVaultDef}>
            {vaultFolders.length === 0 ? (
              <span className="text-label-xs text-muted-foreground">
                No vault folders. Mark a watched folder as a vault above.
              </span>
            ) : (
              <Select
                value={briefSettings.vault_id ?? ""}
                onValueChange={(v) => void saveField({ vault_id: v || null })}
              >
                <SelectTrigger className="w-48 h-8" aria-label="Target vault">
                  <SelectValue placeholder="Choose vault…" />
                </SelectTrigger>
                <SelectContent>
                  {vaultFolders.map((f) => (
                    <SelectItem key={f.id} value={f.id}>{f.label || f.path}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </SettingRow>
          <AdvancedDisclosure category="knowledge" group="briefs">
            <SettingRow def={folderPrefixDef}>
              <Input
                value={briefSettings.vault_folder ?? "_briefs"}
                onChange={(e) => void saveField({ vault_folder: e.target.value })}
                placeholder="_briefs"
                className="h-8 w-36 text-sm"
              />
            </SettingRow>
          </AdvancedDisclosure>
        </>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{error}</AlertDescription>
        </Alert>
      )}
    </SectionCard>
  )
}

// ── KB Maintenance / Danger Zone (relocated from System, ST12) ────────────────

function KBMaintenanceGroup() {
  const qc = useQueryClient()
  const rebuildDef = getDef("knowledge.maintenance.rebuildIndexes")!
  const rescoreDef = getDef("knowledge.maintenance.rescore")!
  const regenDef = getDef("knowledge.maintenance.regenerateSummaries")!
  const clearDomainDef = getDef("knowledge.maintenance.clearDomain")!

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
    <SectionCard title="KB Maintenance" className="border-red-500/30">
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
          {Object.keys(kbStats.domains ?? {}).length > 0 && (
            <div className="divide-y divide-border rounded border mt-1">
              {Object.entries(kbStats.domains ?? {}).map(([domain, stats]) => (
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
                logSwallowedError(err, "knowledge.clearDomain")
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

// ── Page ─────────────────────────────────────────────────────────────────────

export default function KnowledgeCategory({ settings, patch }: SettingsCategoryPageProps) {
  return (
    <div className="density-stack">
      <WatchedFoldersGroup />
      <DataSourcesGroup />
      <IngestionGroup settings={settings} patch={patch} />
      <BriefsGroup />
      <KBMaintenanceGroup />
    </div>
  )
}
