// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Puzzle, Server, Globe, Zap, Plus, RefreshCw, Trash2,
  AlertTriangle,
} from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/ui/empty-state"
import { InfoTip } from "@/components/ui/info-tip"
import {
  SettingRow, AdvancedDisclosure, ConfirmActionButton, ReadOnlyEnvHint,
} from "@/components/settings/settings-primitives"
import { getDef } from "@/lib/settings-registry"
import {
  fetchPlugins, enablePlugin, disablePlugin, scanPlugins, getPluginConfig, updatePluginConfig,
  fetchExternalAPIs, toggleExternalAPI,
  fetchDataSources, enableDataSource, disableDataSource,
  listProAutomations, updateProAutomation, runProAutomationNow,
  type AutomationState,
} from "@/lib/api"
import {
  fetchMcpServers, addMcpServer, deleteMcpServer, reconnectMcpServer,
  type McpServerAddRequest,
} from "@/lib/api/governance"
import { MCP_BASE } from "@/lib/api/common"
import type { Plugin, PluginConfig } from "@/lib/types"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useNavigation, type NavigationOptions } from "@/contexts/navigation-context"
import type { Pane } from "@/components/layout/sidebar"
import { LicenseNotice } from "@/components/settings/license-notice"

/** Inline cross-pane route. Naming a destination in prose with no way to get
    there is just a complaint — every seam this page draws links across. */
function InlineNavLink({
  pane,
  options,
  children,
}: {
  pane: Pane
  options?: NavigationOptions
  children: React.ReactNode
}) {
  const { goTo } = useNavigation()
  return (
    <button
      type="button"
      onClick={() => goTo(pane, options)}
      className="font-medium underline underline-offset-2 hover:no-underline"
    >
      {children}
    </button>
  )
}

/** Inline route to the plan pane. */
function PlanLink({ children }: { children: React.ReactNode }) {
  return (
    <InlineNavLink pane="settings" options={{ category: "plan" }}>
      {children}
    </InlineNavLink>
  )
}
import { logSwallowedError } from "@/lib/log-swallowed"

/** Locked-state footnote. When the capabilities fetch FAILED the lock is a
    fail-closed fallback, not a settled verdict — say so instead of pitching
    an upgrade to a user who may already own the plan. */
function PlanGateNote({ tier = "Pro", unverified }: { tier?: string; unverified: boolean }) {
  if (unverified) {
    return (
      <p className="text-label-xs text-muted-foreground">
        Couldn&apos;t verify your plan, so this stays locked. Check your
        connection or retry from <PlanLink>Plan &amp; Billing</PlanLink>.
      </p>
    )
  }
  return (
    <p className="text-label-xs text-muted-foreground">
      Requires {tier} plan —{" "}
      <PlanLink>see plans or start a free trial</PlanLink>.
    </p>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function SectionCard({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase text-muted-foreground tracking-wider">{title}</span>
        {description && (
          <p className="text-label-sm leading-snug text-muted-foreground normal-case tracking-normal">
            {description}
          </p>
        )}
      </CardHeader>
      <CardContent className="density-stack">{children}</CardContent>
    </Card>
  )
}

// ── Plugins ───────────────────────────────────────────────────────────────────

function PluginRow({ plugin }: { plugin: Plugin }) {
  const qc = useQueryClient()
  const ent = useEntitlements()
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({})
  const [configLoaded, setConfigLoaded] = useState(false)
  const [configError, setConfigError] = useState("")
  const [toggleError, setToggleError] = useState("")
  const [savingConfig, setSavingConfig] = useState(false)

  // Older servers predate display_name/plugin_type — fall back to the raw id.
  const displayName = plugin.display_name ?? plugin.name
  const backsConnector = plugin.plugin_type === "connector"

  const tierRequired = (plugin.tier_required ?? "").toLowerCase()
  const needsEnterprise = tierRequired === "enterprise"
  const fallbackTier = needsEnterprise
    ? ("enterprise" as const)
    : tierRequired === "pro"
      ? ("pro" as const)
      : undefined

  // Per-FLAG resolution off the manifest's own feature_flags (carried on the
  // /plugins payload) instead of the coarse tier_required. The plugin is
  // usable when ANY of its flags is available; otherwise "locked" wins over
  // "flag-off" so the row can name the tier an upgrade would actually fix.
  // Flagless manifests keep the tier_required fallback, which also makes a
  // paid plugin fail CLOSED while capabilities are unknown.
  const flagInfos = (
    plugin.feature_flags?.length ? plugin.feature_flags : [undefined]
  ).map((flag) => ent.forFlag(flag, fallbackTier))
  const entInfo =
    flagInfos.find((i) => i.state === "available")
    ?? flagInfos.find((i) => i.state === "locked")
    ?? flagInfos[0]

  // Verdict suppressed while capabilities load — tier defaults to "community"
  // in flight, and a paying customer must not see a lock on first paint. A
  // FAILED fetch keeps the fail-closed fallback; the enable call's own 403
  // stays authoritative and surfaces in toggleError.
  const isLocked = !ent.isLoading && entInfo.state === "locked"
  const requiredTierLabel =
    entInfo.requiredTier === "enterprise" || needsEnterprise ? "Enterprise" : "Pro"

  const handleToggle = async (checked: boolean) => {
    setToggleError("")
    try {
      if (checked) {
        await enablePlugin(plugin.name)
      } else {
        await disablePlugin(plugin.name)
      }
      await qc.invalidateQueries({ queryKey: ["plugins"] })
    } catch (err) {
      setToggleError(err instanceof Error ? err.message : "Toggle failed")
      logSwallowedError(err, "extensions.togglePlugin")
    }
  }

  useEffect(() => {
    if (!plugin.config_schema || Object.keys(plugin.config_schema).length === 0) return
    if (configLoaded) return
    getPluginConfig(plugin.name)
      .then((cfg) => { setConfigValues(cfg.values); setConfigLoaded(true) })
      .catch((err) => { setConfigError(err instanceof Error ? err.message : "Failed to load config"); logSwallowedError(err, "extensions.getPluginConfig") })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on mount
  }, [])

  const handleSaveConfig = async () => {
    setSavingConfig(true)
    setConfigError("")
    try {
      await updatePluginConfig(plugin.name, { values: configValues } as PluginConfig)
      await qc.invalidateQueries({ queryKey: ["plugins"] })
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "Save failed")
      logSwallowedError(err, "extensions.updatePluginConfig")
    } finally {
      setSavingConfig(false)
    }
  }

  return (
    <div className="border rounded-md p-3 density-stack">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{displayName}</span>
            {plugin.version && (
              <Badge variant="outline" className="text-label-xs font-mono">{plugin.version}</Badge>
            )}
            {displayName !== plugin.name && (
              <Badge variant="outline" className="text-label-xs font-mono">{plugin.name}</Badge>
            )}
            {tierRequired && tierRequired !== "community" && (
              <Badge
                variant="outline"
                className={`text-label-xs ${isLocked ? "opacity-60" : ""}`}
              >
                {needsEnterprise ? "Enterprise" : "Pro"}
              </Badge>
            )}
            {plugin.status === "error" && (
              <Badge variant="destructive" className="text-label-xs">Error</Badge>
            )}
          </div>
          {plugin.description && (
            <p className="text-label-xs text-muted-foreground mt-0.5">{plugin.description}</p>
          )}
          {backsConnector && (
            <p className="text-label-xs text-muted-foreground mt-0.5">
              Backs the {displayName} connector — connect and sync it in{" "}
              <InlineNavLink pane="sources" options={{ sourcesMode: "connectors" }}>
                Sources → Connectors
              </InlineNavLink>.
            </p>
          )}
        </div>
        <Switch
          checked={plugin.enabled}
          onCheckedChange={handleToggle}
          disabled={ent.isLoading || isLocked}
          aria-label={`${plugin.enabled ? "Disable" : "Enable"} ${displayName}`}
          className="shrink-0"
        />
      </div>
      {toggleError && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{toggleError}</AlertDescription>
        </Alert>
      )}
      {isLocked && <PlanGateNote tier={requiredTierLabel} unverified={ent.isError} />}
      {plugin.config_schema && Object.keys(plugin.config_schema).length > 0 && (
        <AdvancedDisclosure category="extensions" group="plugins">
          <div className="density-stack">
            {configError && (
              <Alert variant="destructive">
                <AlertDescription className="text-label-xs">{configError}</AlertDescription>
              </Alert>
            )}
            {configLoaded && Object.entries(configValues).map(([key, val]) => (
              <div key={key} className="flex items-center gap-2">
                <Label htmlFor={`plugin-cfg-${plugin.name}-${key}`} className="w-32 shrink-0 text-xs">
                  {key}
                </Label>
                <Input
                  id={`plugin-cfg-${plugin.name}-${key}`}
                  value={String(val ?? "")}
                  onChange={(e) => setConfigValues((prev) => ({ ...prev, [key]: e.target.value }))}
                  className="h-7 text-xs"
                />
              </div>
            ))}
            {configLoaded && (
              <Button size="sm" onClick={handleSaveConfig} disabled={savingConfig} className="h-7 text-xs">
                {savingConfig ? "Saving…" : "Save config"}
              </Button>
            )}
          </div>
        </AdvancedDisclosure>
      )}
    </div>
  )
}

function PluginsSection() {
  const qc = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["plugins"],
    queryFn: fetchPlugins,
    staleTime: 30_000,
  })
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState("")

  const handleScan = async () => {
    setScanning(true)
    setScanError("")
    try {
      await scanPlugins()
      await qc.invalidateQueries({ queryKey: ["plugins"] })
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed")
      logSwallowedError(err, "extensions.scanPlugins")
    } finally {
      setScanning(false)
    }
  }

  const plugins = data?.plugins ?? []

  return (
    <SectionCard
      title="Plugins"
      description="Local capability packs installed on this server. Lowest-friction way to add tools and connectors."
    >
      {isLoading && (
        <div className="density-stack">
          {[1, 2].map((i) => <Skeleton key={i} className="h-14 w-full rounded-md" />)}
        </div>
      )}
      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load plugins.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {!isLoading && !isError && plugins.length === 0 && (
        <EmptyState icon={Puzzle} title="No plugins installed" description="Place plugin packages in the configured plugin directory and scan." />
      )}
      {plugins.map((p) => (
        <PluginRow key={p.name} plugin={p} />
      ))}
      {scanError && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{scanError}</AlertDescription>
        </Alert>
      )}
      <Button variant="outline" size="sm" onClick={handleScan} disabled={scanning} className="gap-1.5">
        <RefreshCw className={`h-4 w-4 ${scanning ? "animate-spin" : ""}`} />
        Scan for plugins
      </Button>
    </SectionCard>
  )
}

// ── MCP Servers ───────────────────────────────────────────────────────────────

function McpServerRow({ server }: { server: { name: string; transport: string; status: string; error?: string | null; tool_count?: number } }) {
  const qc = useQueryClient()
  const [reconnecting, setReconnecting] = useState(false)
  const [reconnectError, setReconnectError] = useState("")

  const handleReconnect = async () => {
    setReconnecting(true)
    setReconnectError("")
    try {
      await reconnectMcpServer(server.name)
      await qc.invalidateQueries({ queryKey: ["mcp-servers"] })
    } catch (err) {
      setReconnectError(err instanceof Error ? err.message : "Reconnect failed")
      logSwallowedError(err, "extensions.reconnectMcpServer")
    } finally {
      setReconnecting(false)
    }
  }

  return (
    <div className="border rounded-md p-3 density-stack">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{server.name}</span>
            <Badge variant="outline" className="text-label-xs font-mono">{server.transport}</Badge>
            <Badge
              variant={server.status === "connected" ? "outline" : "destructive"}
              className="text-label-xs"
            >
              {server.status}
            </Badge>
            {server.tool_count !== undefined && server.tool_count > 0 && (
              <span className="text-label-xs text-muted-foreground">{server.tool_count} tools</span>
            )}
          </div>
          {server.error && (
            <p className="text-label-xs text-destructive mt-1">{server.error}</p>
          )}
        </div>
        <div className="flex gap-1 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleReconnect}
            disabled={reconnecting}
            className="h-8 px-2 min-h-6"
            aria-label={`Reconnect ${server.name}`}
          >
            <RefreshCw className={`h-4 w-4 ${reconnecting ? "animate-spin" : ""}`} />
          </Button>
          <ConfirmActionButton
            danger="confirm"
            title={`Remove ${server.name}?`}
            description="This disconnects the MCP server and removes its tools from all agents."
            actionLabel="Remove"
            onConfirm={async () => {
              await deleteMcpServer(server.name)
              await qc.invalidateQueries({ queryKey: ["mcp-servers"] })
            }}
            variant="ghost"
            size="sm"
            className="h-8 px-2 min-h-6"
          >
            <Trash2 className="h-4 w-4" aria-hidden />
            <span className="sr-only">Remove server</span>
          </ConfirmActionButton>
        </div>
      </div>
      {reconnectError && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{reconnectError}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

function AddMcpServerForm({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState("")
  const [transport, setTransport] = useState<"stdio" | "sse">("stdio")
  const [command, setCommand] = useState("")
  const [args, setArgs] = useState("")
  const [url, setUrl] = useState("")
  const [envPairs, setEnvPairs] = useState([{ key: "", value: "" }])
  const [headerPairs, setHeaderPairs] = useState([{ key: "", value: "" }])
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState("")

  const handleAdd = async () => {
    if (!name.trim()) { setError("Name is required"); return }
    setAdding(true)
    setError("")
    const req: McpServerAddRequest = {
      name: name.trim(),
      transport,
      ...(transport === "stdio"
        ? {
            command: command.trim() || undefined,
            args: args.trim() ? args.split(/\s+/).filter(Boolean) : undefined,
            env: Object.fromEntries(envPairs.filter(p => p.key).map(p => [p.key, p.value])),
          }
        : {
            url: url.trim() || undefined,
            headers: Object.fromEntries(headerPairs.filter(p => p.key).map(p => [p.key, p.value])),
          }),
    }
    try {
      await addMcpServer(req)
      onAdded()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Add failed")
      logSwallowedError(err, "extensions.addMcpServer")
    } finally {
      setAdding(false)
    }
  }

  const setEnvPair = (i: number, field: "key" | "value", value: string) => {
    setEnvPairs((prev) => {
      const next = [...prev]
      next[i] = { ...next[i], [field]: value }
      return next
    })
  }

  const setHeaderPair = (i: number, field: "key" | "value", value: string) => {
    setHeaderPairs((prev) => {
      const next = [...prev]
      next[i] = { ...next[i], [field]: value }
      return next
    })
  }

  return (
    <div className="border border-dashed rounded-md p-3 density-stack">
      <div className="density-stack">
        <div>
          <Label htmlFor="mcp-name" className="text-sm">Name</Label>
          <Input
            id="mcp-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my-server"
            className="mt-1"
          />
        </div>
        <div>
          <Label className="text-sm">Transport</Label>
          <div className="flex gap-2 mt-1" role="radiogroup" aria-label="Transport">
            {(["stdio", "sse"] as const).map((t) => (
              <Button
                key={t}
                type="button"
                variant={transport === t ? "default" : "outline"}
                size="sm"
                onClick={() => setTransport(t)}
                aria-pressed={transport === t}
                className="h-8"
              >
                {t === "stdio" ? "stdio (local subprocess)" : "SSE (remote HTTP)"}
              </Button>
            ))}
          </div>
        </div>
        {transport === "stdio" ? (
          <>
            <div>
              <Label htmlFor="mcp-command" className="text-sm">Command</Label>
              <Input
                id="mcp-command"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="npx"
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="mcp-args" className="text-sm">Arguments (space-separated)</Label>
              <Input
                id="mcp-args"
                value={args}
                onChange={(e) => setArgs(e.target.value)}
                placeholder="-y @example/mcp-server"
                className="mt-1"
              />
            </div>
            <div>
              <Label className="text-sm">Environment variables</Label>
              {envPairs.map((pair, i) => (
                <div key={i} className="flex gap-2 mt-1">
                  <Input
                    value={pair.key}
                    onChange={(e) => setEnvPair(i, "key", e.target.value)}
                    placeholder="KEY"
                    className="h-7 text-xs flex-1"
                    aria-label={`Env key ${i + 1}`}
                  />
                  <Input
                    type="password"
                    value={pair.value}
                    onChange={(e) => setEnvPair(i, "value", e.target.value)}
                    placeholder="value"
                    className="h-7 text-xs flex-1"
                    aria-label={`Env value ${i + 1}`}
                  />
                </div>
              ))}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEnvPairs((p) => [...p, { key: "", value: "" }])}
                className="h-7 text-xs mt-1"
              >
                + Add variable
              </Button>
            </div>
          </>
        ) : (
          <>
            <div>
              <Label htmlFor="mcp-url" className="text-sm">URL</Label>
              <Input
                id="mcp-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/mcp/sse"
                className="mt-1"
              />
            </div>
            <div>
              <Label className="text-sm">Headers</Label>
              {headerPairs.map((pair, i) => (
                <div key={i} className="flex gap-2 mt-1">
                  <Input
                    value={pair.key}
                    onChange={(e) => setHeaderPair(i, "key", e.target.value)}
                    placeholder="Authorization"
                    className="h-7 text-xs flex-1"
                    aria-label={`Header key ${i + 1}`}
                  />
                  <Input
                    type="password"
                    value={pair.value}
                    onChange={(e) => setHeaderPair(i, "value", e.target.value)}
                    placeholder="Bearer …"
                    className="h-7 text-xs flex-1"
                    aria-label={`Header value ${i + 1}`}
                  />
                </div>
              ))}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setHeaderPairs((p) => [...p, { key: "", value: "" }])}
                className="h-7 text-xs mt-1"
              >
                + Add header
              </Button>
            </div>
          </>
        )}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{error}</AlertDescription>
        </Alert>
      )}
      <Button size="sm" onClick={handleAdd} disabled={adding}>
        {adding ? "Adding…" : "Add server"}
      </Button>
    </div>
  )
}

function McpSection() {
  const qc = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: fetchMcpServers,
    staleTime: 30_000,
  })
  const [showAdd, setShowAdd] = useState(false)

  const modeDef = getDef("extensions.mcp.mode")!
  const allowlistDef = getDef("extensions.mcp.allowlist")!
  const strictDef = getDef("extensions.mcp.strictAgents")!

  const servers = data?.servers ?? []

  return (
    <SectionCard
      title="MCP Servers"
      description="External tool providers connected over the Model Context Protocol. Governance-gated and available to agents."
    >
      <SettingRow def={modeDef}>
        <ReadOnlyEnvHint envVar="MCP_CLIENT_MODE" />
      </SettingRow>
      <SettingRow def={allowlistDef}>
        <ReadOnlyEnvHint envVar="MCP_CLIENT_ALLOWLIST" />
      </SettingRow>
      <SettingRow def={strictDef}>
        <ReadOnlyEnvHint envVar="STRICT_AGENTS_ONLY" />
      </SettingRow>
      {isLoading && (
        <div className="density-stack">
          {[1, 2].map((i) => <Skeleton key={i} className="h-14 w-full rounded-md" />)}
        </div>
      )}
      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load MCP servers.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {!isLoading && !isError && servers.length === 0 && !showAdd && (
        <EmptyState icon={Server} title="No external MCP servers" description="Register an external MCP server to extend agent capabilities." />
      )}
      {servers.map((s) => (
        <McpServerRow key={s.name} server={s} />
      ))}
      {showAdd ? (
        <AddMcpServerForm
          onAdded={() => {
            setShowAdd(false)
            void qc.invalidateQueries({ queryKey: ["mcp-servers"] })
          }}
        />
      ) : (
        <Button variant="outline" size="sm" onClick={() => setShowAdd(true)} className="gap-1.5">
          <Plus className="h-4 w-4" />
          Add MCP server
        </Button>
      )}
    </SectionCard>
  )
}

// ── Knowledge Providers (unified: enrichment adapters + chat lookup tools) ────

type ProviderScope = "enrichment" | "chat-tool"

interface ProviderRowData {
  key: string
  /** Sort/adjacency key — duplicate slugs (wikipedia, openlibrary) sort together. */
  slug: string
  name: string
  scope: ProviderScope
  effect: string
  enabled: boolean
  needsKey: boolean
  envVar?: string
  toggle: (enabled: boolean) => Promise<void>
}

const SCOPE_META: Record<ProviderScope, { badge: string; term: string }> = {
  enrichment: { badge: "Enrichment", term: "provider-scope-enrichment" },
  "chat-tool": { badge: "Chat tool", term: "provider-scope-chat-tool" },
}

function ProviderRow({ row, error, onToggle }: {
  row: ProviderRowData
  error?: string
  onToggle: (row: ProviderRowData, enabled: boolean) => void
}) {
  const meta = SCOPE_META[row.scope]
  return (
    <div className="border rounded-md p-3 density-stack">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{row.name}</span>
            <Badge variant={row.scope === "enrichment" ? "outline" : "secondary"} className="text-label-xs">
              {meta.badge}
            </Badge>
            <InfoTip term={meta.term} />
          </div>
          <p className="text-label-xs text-muted-foreground mt-0.5">{row.effect}</p>
          {row.needsKey && (
            row.envVar
              ? <ReadOnlyEnvHint envVar={row.envVar} />
              : <p className="text-label-xs text-muted-foreground">Needs API key env var to enable.</p>
          )}
        </div>
        <Switch
          checked={row.enabled}
          onCheckedChange={(checked) => onToggle(row, checked)}
          disabled={row.needsKey}
          aria-label={`${row.enabled ? "Disable" : "Enable"} ${row.name} (${meta.badge})`}
          className="shrink-0"
        />
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{error}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

function KnowledgeProvidersSection() {
  const qc = useQueryClient()
  const external = useQuery({
    queryKey: ["external-apis"],
    queryFn: () => fetchExternalAPIs(),
    staleTime: 30_000,
  })
  const dataSources = useQuery({
    queryKey: ["data-sources"],
    queryFn: () => fetchDataSources(),
    staleTime: 30_000,
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const def = getDef("extensions.externalApis.enable")!

  const isLoading = external.isLoading || dataSources.isLoading
  const isError = external.isError || dataSources.isError
  const handleRetry = () => {
    if (external.isError) void external.refetch()
    if (dataSources.isError) void dataSources.refetch()
  }

  const rows: ProviderRowData[] = [
    ...(external.data ?? []).map((api): ProviderRowData => ({
      key: `enrichment:${api.slug}`,
      slug: api.slug,
      name: api.display_name,
      scope: "enrichment",
      effect: "Used when enriching wiki entities and verifying answers.",
      enabled: api.enabled,
      needsKey: api.requires_key && !api.key_configured,
      toggle: async (enabled) => { await toggleExternalAPI(api.slug, enabled) },
    })),
    ...(dataSources.data?.sources ?? []).map((src): ProviderRowData => ({
      key: `chat-tool:${src.name}`,
      slug: src.name,
      name: src.name,
      scope: "chat-tool",
      effect: "Available to chat as a live lookup tool when answering.",
      enabled: src.enabled,
      needsKey: src.requires_api_key && !src.configured,
      envVar: src.api_key_env_var || undefined,
      toggle: async (enabled) => {
        if (enabled) await enableDataSource(src.name)
        else await disableDataSource(src.name)
      },
    })),
  ].sort((a, b) =>
    a.slug.localeCompare(b.slug) || a.scope.localeCompare(b.scope))

  const handleToggle = (row: ProviderRowData, enabled: boolean) => {
    setErrors((prev) => ({ ...prev, [row.key]: "" }))
    void row.toggle(enabled)
      .then(() => qc.invalidateQueries({
        queryKey: [row.scope === "enrichment" ? "external-apis" : "data-sources"],
      }))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Toggle failed"
        setErrors((prev) => ({ ...prev, [row.key]: msg }))
        logSwallowedError(err, "extensions.toggleKnowledgeProvider")
      })
  }

  return (
    <SectionCard
      title="Knowledge Providers"
      description="Read-only public sources Cerid can consult, in two scopes: Enrichment providers feed wiki enrichment and answer verification; Chat tools are queried live during conversations. The same service (e.g. Wikipedia) can appear once per scope — the toggles are independent."
    >
      <SettingRow def={def} />
      {isLoading && (
        <div className="density-stack">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full rounded-md" />)}
        </div>
      )}
      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load knowledge providers.{" "}
            <button type="button" onClick={handleRetry} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState icon={Globe} title="No knowledge providers" description="Providers appear when your deployment includes external adapters or data sources." />
      )}
      {!isLoading && !isError && rows.map((row) => (
        <ProviderRow key={row.key} row={row} error={errors[row.key]} onToggle={handleToggle} />
      ))}
    </SectionCard>
  )
}

// ── Pro Automations ───────────────────────────────────────────────────────────

function AutomationRow({ automation, onChanged }: { automation: AutomationState; onChanged: () => void }) {
  const ent = useEntitlements()
  const entInfo = ent.forFlag("pro_meeting_capture", "pro")
  // Verdicts suppressed while capabilities load (tier defaults "community" in
  // flight); a FAILED fetch keeps the fail-closed fallback and the note below
  // says the plan is unverified instead of pitching an upgrade.
  const isLocked = !ent.isLoading && entInfo.state === "locked"
  const isFlagOff = !ent.isLoading && entInfo.state === "flag-off"
  const [runError, setRunError] = useState("")
  const [running, setRunning] = useState(false)

  const handleToggle = async (checked: boolean) => {
    try {
      await updateProAutomation(automation.feature, { enabled: checked })
      onChanged()
    } catch (err) {
      logSwallowedError(err, "extensions.updateProAutomation")
    }
  }

  const handleCadenceChange = async (schedule: string) => {
    try {
      await updateProAutomation(automation.feature, { schedule })
      onChanged()
    } catch (err) {
      logSwallowedError(err, "extensions.updateProAutomation.schedule")
    }
  }

  const handleRunNow = async () => {
    setRunning(true)
    setRunError("")
    try {
      await runProAutomationNow(automation.feature)
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Run failed")
      logSwallowedError(err, "extensions.runProAutomationNow")
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="border rounded-md p-3 density-stack">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <span className="text-sm font-medium">{automation.display_name}</span>
          {automation.description && (
            <p className="text-label-xs text-muted-foreground">{automation.description}</p>
          )}
          {isFlagOff && (
            <p className="text-label-xs text-muted-foreground">
              This feature is disabled on this server (flag off).
            </p>
          )}
        </div>
        <Switch
          checked={automation.enabled}
          onCheckedChange={handleToggle}
          disabled={ent.isLoading || isLocked || isFlagOff}
          aria-label={`${automation.enabled ? "Disable" : "Enable"} ${automation.display_name}`}
          className="shrink-0"
        />
      </div>
      {automation.enabled && !isLocked && !isFlagOff && (
        <div className="flex items-center gap-2">
          <Label className="text-xs shrink-0">Cadence</Label>
          <Select value={automation.schedule} onValueChange={handleCadenceChange}>
            <SelectTrigger className="h-7 text-xs w-44" aria-label="Automation cadence">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {automation.cadence_presets.map((p) => (
                <SelectItem key={p.cron} value={p.cron}>{p.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRunNow}
            disabled={running}
            className="h-7 text-xs ml-auto"
          >
            {running ? "Running…" : "Run now"}
          </Button>
        </div>
      )}
      {isLocked && <PlanGateNote unverified={ent.isError} />}
      {runError && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{runError}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

function AutomationsSection() {
  const qc = useQueryClient()
  const ent = useEntitlements()
  const entInfo = ent.forFlag("pro_meeting_capture", "pro")
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["pro-automations"],
    queryFn: listProAutomations,
    staleTime: 30_000,
  })

  const automations = data ?? []

  return (
    <SectionCard
      title="Pro Automations"
      description="Scheduled background tasks (inbox triage, daily digest) that run on a cadence you set."
    >
      {!ent.isLoading && entInfo.state === "locked" && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription className="text-xs">
            {ent.isError ? (
              // Fail-closed fallback, not a settled verdict — don't pitch an
              // upgrade to a user whose plan simply couldn't be fetched.
              <>Couldn&apos;t verify your plan — automations stay locked until it can be checked.</>
            ) : (
              <>
                Scheduled automations are a Pro feature.{" "}
                <PlanLink>See plans or start a free trial</PlanLink>.
              </>
            )}
          </AlertDescription>
        </Alert>
      )}
      {isLoading && (
        <div className="density-stack">
          {[1, 2].map((i) => <Skeleton key={i} className="h-16 w-full rounded-md" />)}
        </div>
      )}
      {isError && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Failed to load automations.{" "}
            <button type="button" onClick={() => void refetch()} className="underline">Retry</button>
          </AlertDescription>
        </Alert>
      )}
      {!isLoading && !isError && automations.length === 0 && (
        <EmptyState icon={Zap} title="No automations configured" description="Pro automations appear once the feature flag is enabled on this server." />
      )}
      {automations.map((a) => (
        <AutomationRow
          key={a.feature}
          automation={a}
          onChanged={() => void qc.invalidateQueries({ queryKey: ["pro-automations"] })}
        />
      ))}
    </SectionCard>
  )
}

// ── Spotlight ─────────────────────────────────────────────────────────────────

/** The Spotlight bridge, or null outside the desktop app. CoreSpotlight is a
    host API — a containerised backend can never reach it, so this section only
    exists when the Electron main process is on the other side. */
function spotlightBridge() {
  if (typeof window === "undefined") return null
  return window.cerid?.appleConnectors?.spotlight ?? null
}

/** Retention windows offered for donated Spotlight items. "Never" is 0, which
    is what the helper reads as "set no expirationDate". */
const SPOTLIGHT_RETENTION_KEY = "cerid-spotlight-retention-days"
const SPOTLIGHT_RETENTION_DEFAULT = "90"
const SPOTLIGHT_RETENTION_OPTIONS = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "0", label: "Never expire" },
] as const

/** A stored value that is no longer an offered option falls back to the
    default. Anything else would apply a window the operator cannot see in the
    control — including, if the key were ever set to junk, an unbounded one. */
function readSpotlightRetention(): string {
  try {
    const raw = localStorage.getItem(SPOTLIGHT_RETENTION_KEY)
    return SPOTLIGHT_RETENTION_OPTIONS.some((o) => o.value === raw)
      ? (raw as string)
      : SPOTLIGHT_RETENTION_DEFAULT
  } catch {
    return SPOTLIGHT_RETENTION_DEFAULT
  }
}

function SpotlightSection() {
  const ent = useEntitlements()
  const entInfo = ent.forFlag("spotlight_donation", "pro")
  // Suppressed while capabilities load; fail-closed fallback survives a
  // failed fetch (the note below says unverified instead of pitching Pro).
  const isLocked = !ent.isLoading && entInfo.state === "locked"
  const bridge = spotlightBridge()
  const [busy, setBusy] = useState<"donating" | "purging" | null>(null)
  const [result, setResult] = useState<string>("")
  const [error, setError] = useState("")
  const [retention, setRetention] = useState<string>(readSpotlightRetention)

  if (bridge === null) return null

  const handleRetentionChange = (value: string) => {
    setRetention(value)
    try {
      localStorage.setItem(SPOTLIGHT_RETENTION_KEY, value)
    } catch (err) {
      logSwallowedError(err, "localStorage.setItem", { key: SPOTLIGHT_RETENTION_KEY })
    }
  }

  const handleDonate = async () => {
    setBusy("donating")
    setError("")
    setResult("")
    try {
      const r = await bridge.donate({
        mcp_base_url: MCP_BASE,
        expiration_days: Number(retention),
      })
      if (r.ok) {
        // Report the window the main process applied, not the one requested —
        // it normalises the value, so those can differ.
        const applied = r.expiration_days
        const window_ =
          applied === undefined
            ? ""
            : applied === 0
              ? " They will not expire."
              : ` They expire after ${applied} days.`
        // `truncated` means the knowledge-base read hit its cap: `scanned` is
        // the cap, not the KB's size, and without the label the number reads
        // as a census of everything donated.
        const cap = r.truncated
          ? ` (truncated at ${r.scanned.toLocaleString()} — the knowledge base holds more)`
          : ""
        setResult(`Donated ${r.donated} of ${r.scanned} artifacts to Spotlight${cap}.${window_}`)
      } else {
        setError(r.error ?? "Donation failed")
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Donation failed")
      logSwallowedError(err, "extensions.spotlightDonate")
    } finally {
      setBusy(null)
    }
  }

  const handlePurge = async () => {
    setBusy("purging")
    setError("")
    setResult("")
    try {
      const r = await bridge.purge()
      if (r.ok) setResult("Removed Cerid's items from Spotlight.")
      else setError(r.error ?? "Removal failed")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Removal failed")
      logSwallowedError(err, "extensions.spotlightPurge")
    } finally {
      setBusy(null)
    }
  }

  return (
    <SectionCard
      title="Spotlight"
      description="Make your knowledge base searchable from Cmd-Space. Titles and summaries are indexed by macOS on this machine; nothing is sent anywhere."
    >
      {ent.isLoading ? null : isLocked ? (
        <PlanGateNote unverified={ent.isError} />
      ) : (
        <div className="density-stack">
          <div className="flex items-center gap-2">
            <Label className="text-xs shrink-0">Keep entries for</Label>
            <Select value={retention} onValueChange={handleRetentionChange}>
              <SelectTrigger className="h-7 text-xs w-44" aria-label="Spotlight retention">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SPOTLIGHT_RETENTION_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="text-label-xs text-muted-foreground">
            Applied when you donate. macOS drops the entries itself once the window
            passes, which is also what cleans up if you uninstall Cerid — removing an
            app runs no code, so nothing else can. Entries already donated keep the
            window they were given until you donate again.
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleDonate}
              disabled={busy !== null}
              className="h-7 text-xs"
            >
              {busy === "donating" ? "Donating…" : "Donate to Spotlight"}
            </Button>
            <ConfirmActionButton
              danger="confirm"
              title="Remove Cerid's Spotlight entries?"
              description="Your knowledge base is untouched — this only clears the macOS search-index entries Cerid donated."
              actionLabel="Remove"
              onConfirm={handlePurge}
              disabled={busy !== null}
              variant="outline"
              className="h-7 text-xs"
            >
              {busy === "purging" ? "Removing…" : "Remove from Spotlight"}
            </ConfirmActionButton>
          </div>
        </div>
      )}
      {result && <p className="text-label-xs text-muted-foreground">{result}</p>}
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="text-label-xs">{error}</AlertDescription>
        </Alert>
      )}
    </SectionCard>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExtensionsCategory() {
  return (
    <div className="density-stack">
      <LicenseNotice />
      <Card>
        <CardContent className="py-3">
          <p className="text-label-sm leading-relaxed text-muted-foreground">
            Plugins are capability packs installed on the server. To connect your
            own data — Apple apps, email, calendars, cloud accounts — go to{" "}
            <InlineNavLink pane="sources" options={{ sourcesMode: "connectors" }}>
              Sources → Connectors
            </InlineNavLink>
            .
          </p>
          <p className="mt-1.5 text-label-sm leading-relaxed text-muted-foreground">
            This page covers four things — local capability packs installed on the
            server, external tool providers connected over MCP for agents to call,
            read-only knowledge providers (enrichment + chat lookup), and scheduled
            background automations. Each has its own section below.
          </p>
        </CardContent>
      </Card>
      <PluginsSection />
      <McpSection />
      <KnowledgeProvidersSection />
      <AutomationsSection />
      <SpotlightSection />
    </div>
  )
}
