// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Loader2,
  RefreshCw,
  Lock,
  FileText,
  Mic,
  BarChart3,
  Puzzle,
  ChevronDown,
  ChevronRight,
  Package,
} from "lucide-react"
import {
  fetchPlugins,
  enablePlugin,
  disablePlugin,
  getPluginConfig,
  updatePluginConfig,
  scanPlugins,
} from "@/lib/api"
import type { Plugin, PluginConfig, PluginStatus } from "@/lib/types"

const STATUS_BADGE: Record<PluginStatus, { label: string; className: string }> = {
  active: { label: "Active", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30" },
  installed: { label: "Installed", className: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30" },
  disabled: { label: "Disabled", className: "bg-zinc-500/15 text-zinc-500 dark:text-zinc-400 border-zinc-500/30" },
  error: { label: "Error", className: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30" },
  requires_pro: { label: "Requires Pro", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30" },
  requires_enterprise: { label: "Requires Enterprise", className: "bg-purple-500/15 text-purple-700 dark:text-purple-400 border-purple-500/30" },
}

function pluginIcon(capabilities: string[]) {
  if (capabilities.includes("parser")) {
    // Try to narrow by name convention
    return FileText
  }
  if (capabilities.includes("analytics")) return BarChart3
  return Puzzle
}

function pluginIconByName(name: string, capabilities: string[]) {
  if (name.includes("ocr")) return FileText
  if (name.includes("audio")) return Mic
  if (name.includes("analytics")) return BarChart3
  return pluginIcon(capabilities)
}

interface PluginCardProps {
  plugin: Plugin
  onToggle: (name: string, enabled: boolean) => Promise<void>
  toggling: boolean
}

function PluginCard({ plugin, onToggle, toggling }: PluginCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [config, setConfig] = useState<PluginConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(false)
  const [configDraft, setConfigDraft] = useState<Record<string, string>>({})
  const [configSaving, setConfigSaving] = useState(false)
  const [configError, setConfigError] = useState("")

  const badge = STATUS_BADGE[plugin.status] ?? STATUS_BADGE.disabled
  const Icon = pluginIconByName(plugin.name, plugin.capabilities)
  const isProLocked = plugin.status === "requires_pro"

  const loadConfig = useCallback(async () => {
    if (config !== null) return
    setConfigLoading(true)
    try {
      const c = await getPluginConfig(plugin.name)
      setConfig(c)
      setConfigDraft(
        Object.fromEntries(Object.entries(c.values).map(([k, v]) => [k, String(v)]))
      )
    } catch {
      /* ignore */
    } finally {
      setConfigLoading(false)
    }
  }, [plugin.name, config])

  const handleExpand = () => {
    const next = !expanded
    setExpanded(next)
    if (next) loadConfig()
  }

  const saveConfig = async () => {
    setConfigSaving(true)
    setConfigError("")
    try {
      const values: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(configDraft)) {
        // Try to parse numbers/booleans
        if (v === "true") values[k] = true
        else if (v === "false") values[k] = false
        else if (!isNaN(Number(v)) && v.trim() !== "") values[k] = Number(v)
        else values[k] = v
      }
      const updated = await updatePluginConfig(plugin.name, { values })
      setConfig(updated)
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : "Failed to save config")
    } finally {
      setConfigSaving(false)
    }
  }

  return (
    <Card className="border-border/50 transition-colors hover:border-border">
      <CardHeader className="cursor-pointer pb-2 pt-3 px-4" onClick={handleExpand}>
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-teal-500/10">
            <Icon className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-sm font-medium">{plugin.name}</CardTitle>
              <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${badge.className}`}>
                {isProLocked && <Lock className="mr-0.5 h-2.5 w-2.5" />}
                {badge.label}
              </Badge>
              <span className="text-[10px] text-muted-foreground">v{plugin.version}</span>
            </div>
            <CardDescription className="text-xs mt-0.5 line-clamp-1">
              {plugin.description}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
            <Switch
              checked={plugin.enabled}
              disabled={isProLocked || toggling}
              onCheckedChange={(checked) => onToggle(plugin.name, checked)}
              className="data-[state=checked]:bg-teal-600"
            />
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            )}
          </div>
        </div>
      </CardHeader>

      {expanded && (
        <CardContent className="px-4 pb-3 pt-0">
          <div className="space-y-2 text-xs">
            {/* Metadata */}
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
              <span>Tier: <strong className="text-foreground">{plugin.tier_required}</strong></span>
              {plugin.capabilities.length > 0 && (
                <span>
                  Capabilities:{" "}
                  {plugin.capabilities.map((c) => (
                    <Badge key={c} variant="secondary" className="mr-1 text-[10px] px-1 py-0">{c}</Badge>
                  ))}
                </span>
              )}
              {plugin.file_types.length > 0 && (
                <span>
                  File types:{" "}
                  {plugin.file_types.map((ft) => (
                    <code key={ft} className="mr-1 rounded bg-muted px-1">{ft}</code>
                  ))}
                </span>
              )}
            </div>

            {/* Config section */}
            {configLoading ? (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading configuration...
              </div>
            ) : config && Object.keys(config.values).length > 0 ? (
              <div className="space-y-2 rounded border border-border/50 p-2">
                <span className="text-[11px] font-medium text-muted-foreground">Configuration</span>
                {Object.entries(configDraft).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-2">
                    <Label className="w-32 shrink-0 text-[11px]">{key}</Label>
                    <Input
                      value={value}
                      onChange={(e) => setConfigDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                      className="h-7 text-xs"
                    />
                  </div>
                ))}
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[11px]"
                    disabled={configSaving}
                    onClick={saveConfig}
                  >
                    {configSaving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                    Save Config
                  </Button>
                  {configError && <span className="text-red-500 text-[11px]">{configError}</span>}
                </div>
              </div>
            ) : config ? (
              <p className="text-muted-foreground italic">No configurable options</p>
            ) : null}
          </div>
        </CardContent>
      )}
    </Card>
  )
}

export function PluginsSection() {
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [error, setError] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const data = await fetchPlugins()
      // Backend may return plugins as {} (empty object) or [] (array)
      const raw = data.plugins
      setPlugins(Array.isArray(raw) ? raw : Object.values(raw))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load plugins")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleScan = async () => {
    setScanning(true)
    try {
      const data = await scanPlugins()
      const scanned = data.plugins
      setPlugins(Array.isArray(scanned) ? scanned : Object.values(scanned))
    } catch {
      /* ignore */
    } finally {
      setScanning(false)
    }
  }

  const handleToggle = async (name: string, enabled: boolean) => {
    setToggling(name)
    try {
      const updated = enabled ? await enablePlugin(name) : await disablePlugin(name)
      setPlugins((prev) => prev.map((p) => (p.name === name ? updated : p)))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Toggle failed")
    } finally {
      setToggling(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading plugins...
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-500">
        {error}
        <Button variant="ghost" size="sm" className="ml-2 h-5 text-[11px]" onClick={load}>
          Retry
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">Plugin Management</span>
          <Badge variant="secondary" className="text-[10px]">{plugins.length}</Badge>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          disabled={scanning}
          onClick={handleScan}
        >
          {scanning ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />}
          Scan for Plugins
        </Button>
      </div>

      {/* Plugin cards */}
      {plugins.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-8 text-center">
            <Puzzle className="mb-2 h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">No plugins installed</p>
            <p className="text-xs text-muted-foreground/70 mt-1">
              See docs/INTEGRATION_GUIDE.md for plugin development
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-2">
          {plugins.map((plugin) => (
            <PluginCard
              key={plugin.name}
              plugin={plugin}
              onToggle={handleToggle}
              toggling={toggling === plugin.name}
            />
          ))}
        </div>
      )}
    </div>
  )
}
