// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Sources / Connectors panel — Phase B Day 11. Unified source list
// that consolidates the three source kinds users configure today:
//   - Watched folders (filesystem sources)
//   - External APIs (Wikipedia / ArXiv / GitHub / etc.)
//   - Plugins (ingestion plugins)
//
// Each row shows: name, source-type icon, health/sync state, enabled
// toggle. Selecting a row opens a detail panel on the right with the
// full config + actions for that source. Click outside to deselect.
//
// This replaces the v1 placeholder in Sources/Connectors and is the
// canonical surface users discover when they want to manage where
// Cerid pulls knowledge from.

import { useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Folder,
  Globe,
  Plug,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Pause,
  RefreshCw,
} from "lucide-react"
import { fetchWatchedFolders, scanWatchedFolder, updateWatchedFolder, type WatchedFolder, fetchPlugins, enablePlugin, disablePlugin } from "@/lib/api/settings"
import { fetchExternalAPIs, toggleExternalAPI, fetchExternalAPIHealth } from "@/lib/api/external-apis"
import type { ExternalAPISummary, ExternalAPIHealth } from "@/lib/types/external-apis"
import type { Plugin } from "@/lib/types"
import { AppleConnectorsSection } from "./apple-connectors-section"

type SourceKind = "folder" | "external_api" | "plugin"

interface UnifiedSource {
  id: string
  kind: SourceKind
  name: string
  enabled: boolean
  detail?: string
  lastActivity?: string | null
  raw: WatchedFolder | ExternalAPISummary | Plugin
}

interface KindMeta {
  icon: typeof Folder
  label: string
}

const KIND_META: Record<SourceKind, KindMeta> = {
  folder: { icon: Folder, label: "Folder" },
  external_api: { icon: Globe, label: "External API" },
  plugin: { icon: Plug, label: "Plugin" },
}

// ---------------------------------------------------------------------------
// Adapter helpers
// ---------------------------------------------------------------------------

function adaptFolder(f: WatchedFolder): UnifiedSource {
  const stats = f.stats ?? { ingested: 0, skipped: 0, errored: 0 }
  return {
    id: `folder:${f.id}`,
    kind: "folder",
    name: f.label || f.path,
    enabled: f.enabled,
    detail: `${stats.ingested} ingested · ${stats.errored} errored`,
    lastActivity: f.last_scanned_at,
    raw: f,
  }
}

function adaptExternalAPI(api: ExternalAPISummary): UnifiedSource {
  let detail = api.requires_key
    ? api.key_configured ? "Key configured" : "Key required"
    : "Keyless"
  if (api.enabled && !api.key_configured && api.requires_key) {
    detail += " · misconfigured"
  }
  return {
    id: `external:${api.slug}`,
    kind: "external_api",
    name: api.display_name,
    enabled: api.enabled,
    detail,
    raw: api,
  }
}

function adaptPlugin(p: Plugin): UnifiedSource {
  return {
    id: `plugin:${p.name}`,
    kind: "plugin",
    name: p.name,
    enabled: p.enabled,
    detail: `v${p.version} · ${p.file_types?.join(", ") || "any"}`,
    raw: p,
  }
}

// ---------------------------------------------------------------------------
// Source list — left column
// ---------------------------------------------------------------------------

function SourceRow({
  source,
  selected,
  onSelect,
  onToggle,
  busy,
}: {
  source: UnifiedSource
  selected: boolean
  onSelect: () => void
  onToggle: () => void
  busy?: boolean
}) {
  const Icon = KIND_META[source.kind].icon
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={`flex w-full items-start gap-2 rounded-md px-2 py-2 text-left transition-colors ${
          selected ? "bg-accent text-accent-foreground" : "hover:bg-accent/40"
        }`}
        aria-pressed={selected}
      >
        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="grow">
          <span className="block truncate text-sm" title={source.name}>{source.name}</span>
          {source.detail && (
            <span className="block text-label-xxs text-muted-foreground">{source.detail}</span>
          )}
        </span>
        {/* Inline enabled-state pill — single-click toggle */}
        <span
          role="switch"
          tabIndex={-1}
          aria-checked={source.enabled}
          aria-busy={busy}
          onClick={(e) => {
            e.stopPropagation()
            if (!busy) onToggle()
          }}
          className={`flex h-5 shrink-0 items-center rounded-full px-2 text-label-xxs font-medium transition-colors ${
            source.enabled
              ? "bg-primary/15 text-primary"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : source.enabled ? "on" : "off"}
        </span>
      </button>
    </li>
  )
}

// ---------------------------------------------------------------------------
// Detail panel — right column
// ---------------------------------------------------------------------------

function HealthIndicator({ slug }: { slug: string }) {
  const { data, isLoading, isError } = useQuery<ExternalAPIHealth>({
    queryKey: ["external-api-health", slug],
    queryFn: () => fetchExternalAPIHealth(slug),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  if (isLoading) return <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
  if (isError) return <AlertCircle className="h-3 w-3 text-destructive" />
  if (data?.status === "ok") {
    return <CheckCircle2 className="h-3 w-3 text-primary" aria-label="Healthy" />
  }
  return <AlertCircle className="h-3 w-3 text-amber-500" aria-label={data?.detail ?? "Degraded"} />
}

function FolderDetail({
  folder,
  onChanged,
}: {
  folder: WatchedFolder
  onChanged: () => void
}) {
  const [scanning, setScanning] = useState(false)
  const handleScan = async () => {
    setScanning(true)
    try {
      await scanWatchedFolder(folder.id)
      onChanged()
    } finally {
      setScanning(false)
    }
  }
  const stats = folder.stats ?? { ingested: 0, skipped: 0, errored: 0 }
  return (
    <div className="space-y-3">
      <div>
        <div className="text-label-xs text-muted-foreground">Path</div>
        <div className="break-all font-mono text-xs text-foreground">{folder.path}</div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-md border bg-card/40 p-2">
          <div className="text-base font-semibold text-foreground">{stats.ingested}</div>
          <div className="text-label-xxs text-muted-foreground">Ingested</div>
        </div>
        <div className="rounded-md border bg-card/40 p-2">
          <div className="text-base font-semibold text-muted-foreground">{stats.skipped}</div>
          <div className="text-label-xxs text-muted-foreground">Skipped</div>
        </div>
        <div className="rounded-md border bg-card/40 p-2">
          <div className={`text-base font-semibold ${stats.errored > 0 ? "text-destructive" : "text-foreground"}`}>
            {stats.errored}
          </div>
          <div className="text-label-xxs text-muted-foreground">Errored</div>
        </div>
      </div>
      <div className="text-label-xs text-muted-foreground">
        Last scan: {folder.last_scanned_at ? new Date(folder.last_scanned_at).toLocaleString() : "never"}
      </div>
      {folder.is_vault && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-label-xs text-primary">
          Obsidian vault detected — vault-aware indexing active.
        </div>
      )}
      <button
        type="button"
        onClick={handleScan}
        disabled={scanning || !folder.enabled}
        className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {scanning ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
        {scanning ? "Scanning…" : "Scan now"}
      </button>
    </div>
  )
}

function ExternalAPIDetail({ api }: { api: ExternalAPISummary }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-label-xs text-muted-foreground">Adapter:</span>
        <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{api.slug}</code>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-label-xs text-muted-foreground">Status:</span>
        <HealthIndicator slug={api.slug} />
      </div>
      <div className="space-y-1 text-sm">
        <div className="flex items-center gap-2">
          {api.requires_key ? (
            api.key_configured ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            ) : (
              <Pause className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" />
            )
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
          )}
          <span>
            {api.requires_key
              ? api.key_configured ? "API key configured" : "API key required (configure in Settings → Governance)"
              : "Keyless adapter"}
          </span>
        </div>
      </div>
      {api.enabled && api.requires_key && !api.key_configured && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-label-xs text-amber-700 dark:text-amber-300">
          Adapter is enabled but missing a key — calls will fail until a key is set.
        </div>
      )}
    </div>
  )
}

function PluginDetail({ plugin }: { plugin: Plugin }) {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <div className="text-label-xs uppercase tracking-wide text-muted-foreground">Description</div>
        <p className="text-sm text-foreground/85">{plugin.description}</p>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <div className="text-label-xxs text-muted-foreground">Version</div>
          <div className="font-mono">{plugin.version}</div>
        </div>
        <div>
          <div className="text-label-xxs text-muted-foreground">Required tier</div>
          <div>{plugin.tier_required}</div>
        </div>
      </div>
      {plugin.file_types && plugin.file_types.length > 0 && (
        <div>
          <div className="text-label-xxs uppercase tracking-wide text-muted-foreground">File types</div>
          <div className="flex flex-wrap gap-1">
            {plugin.file_types.map((t) => (
              <span key={t} className="rounded-full bg-muted px-2 py-0.5 text-label-xs">
                .{t}
              </span>
            ))}
          </div>
        </div>
      )}
      {plugin.capabilities && plugin.capabilities.length > 0 && (
        <div>
          <div className="text-label-xxs uppercase tracking-wide text-muted-foreground">Capabilities</div>
          <ul className="ml-4 list-disc text-sm text-foreground/85">
            {plugin.capabilities.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SourcesConnectors() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const { data: foldersResp, refetch: refetchFolders } = useQuery({
    queryKey: ["watched-folders"],
    queryFn: fetchWatchedFolders,
    staleTime: 30_000,
  })
  const { data: apis = [], refetch: refetchAPIs } = useQuery({
    queryKey: ["external-apis"],
    queryFn: fetchExternalAPIs,
    staleTime: 30_000,
  })
  const { data: pluginsResp, refetch: refetchPlugins } = useQuery({
    queryKey: ["plugins"],
    queryFn: fetchPlugins,
    staleTime: 30_000,
  })

  const sources = useMemo<UnifiedSource[]>(() => {
    const folders = foldersResp?.folders ?? []
    const plugins = pluginsResp?.plugins ?? []
    return [
      ...folders.map(adaptFolder),
      ...apis.map(adaptExternalAPI),
      ...plugins.map(adaptPlugin),
    ]
  }, [foldersResp, apis, pluginsResp])

  const selected = selectedId
    ? sources.find((s) => s.id === selectedId) ?? null
    : sources[0] ?? null

  const handleToggle = async (src: UnifiedSource) => {
    setBusyId(src.id)
    try {
      if (src.kind === "folder") {
        const folder = src.raw as WatchedFolder
        await updateWatchedFolder(folder.id, { enabled: !folder.enabled })
        await refetchFolders()
      } else if (src.kind === "external_api") {
        const api = src.raw as ExternalAPISummary
        await toggleExternalAPI(api.slug, !api.enabled)
        await refetchAPIs()
      } else {
        const plugin = src.raw as Plugin
        if (plugin.enabled) await disablePlugin(plugin.name)
        else await enablePlugin(plugin.name)
        await refetchPlugins()
      }
      qc.invalidateQueries({ queryKey: ["external-api-health"] })
    } finally {
      setBusyId(null)
    }
  }

  if (sources.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-12">
        <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
          <h2 className="text-lg font-semibold text-foreground">No connectors configured</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Add watched folders, enable external API adapters, or install plugins
            in Settings to populate this surface.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="grid h-full grid-cols-1 md:grid-cols-[280px_1fr]">
      {/* List column */}
      <div className="overflow-y-auto border-r bg-card/20 p-2">
        <div className="mb-2 text-label-xs uppercase tracking-wide text-muted-foreground">
          {sources.length} source{sources.length === 1 ? "" : "s"}
        </div>
        <ul className="flex flex-col gap-0.5">
          {sources.map((src) => (
            <SourceRow
              key={src.id}
              source={src}
              selected={selected?.id === src.id}
              onSelect={() => setSelectedId(src.id)}
              onToggle={() => handleToggle(src)}
              busy={busyId === src.id}
            />
          ))}
        </ul>
        {/* Apple connectors (desktop-only; renders null in browser builds) */}
        <div className="mt-4 border-t pt-3">
          <AppleConnectorsSection />
        </div>
      </div>

      {/* Detail column */}
      <div className="overflow-y-auto p-4">
        {selected ? (
          <div className="mx-auto max-w-2xl space-y-4">
            <header className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">{selected.name}</h2>
              <span className="rounded-full border bg-card px-2 py-0.5 text-label-xs text-muted-foreground">
                {KIND_META[selected.kind].label}
              </span>
            </header>
            {selected.kind === "folder" && (
              <FolderDetail folder={selected.raw as WatchedFolder} onChanged={refetchFolders} />
            )}
            {selected.kind === "external_api" && (
              <ExternalAPIDetail api={selected.raw as ExternalAPISummary} />
            )}
            {selected.kind === "plugin" && (
              <PluginDetail plugin={selected.raw as Plugin} />
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Select a source to view details.
          </div>
        )}
      </div>
    </div>
  )
}
