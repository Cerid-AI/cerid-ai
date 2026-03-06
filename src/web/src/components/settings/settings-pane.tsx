// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from "react"
import { fetchSettings, updateSettings } from "@/lib/api"
import type { ServerSettings, SettingsUpdate } from "@/lib/types"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Settings,
  Server,
  Database,
  Shield,
  Tag,
  ToggleLeft,
  Cpu,
  Layers,
  Loader2,
  AlertCircle,
  RefreshCw,
  Info,
  ChevronDown,
  ChevronRight,
  FolderSync,
} from "lucide-react"
import { SyncSection } from "./sync-section"

type LoadState = "loading" | "error" | "ready"

type SectionKey = "connection" | "ingestion" | "features" | "knowledge" | "taxonomy" | "encryption" | "sync" | "flags"

function readSectionState(): Record<SectionKey, boolean> {
  const defaults: Record<SectionKey, boolean> = {
    connection: true, ingestion: true, features: true, knowledge: true,
    taxonomy: true, encryption: true, sync: true, flags: true,
  }
  try {
    const raw = localStorage.getItem("cerid-settings-sections")
    if (raw) return { ...defaults, ...JSON.parse(raw) }
  } catch { /* noop */ }
  return defaults
}

function persistSectionState(state: Record<SectionKey, boolean>) {
  try { localStorage.setItem("cerid-settings-sections", JSON.stringify(state)) } catch { /* noop */ }
}

export default function SettingsPane() {
  const [settings, setSettings] = useState<ServerSettings | null>(null)
  const [loadState, setLoadState] = useState<LoadState>("loading")
  const [error, setError] = useState("")
  const [patchError, setPatchError] = useState("")
  const [sections, setSections] = useState<Record<SectionKey, boolean>>(readSectionState)

  const toggleSection = (key: SectionKey) => {
    setSections((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      persistSectionState(next)
      return next
    })
  }

  const load = async () => {
    setLoadState("loading")
    setError("")
    try {
      const data = await fetchSettings()
      setSettings(data)
      setLoadState("ready")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch settings")
      setLoadState("error")
    }
  }

  useEffect(() => {
    let cancelled = false
    setLoadState("loading")
    setError("")
    fetchSettings()
      .then((data) => { if (!cancelled) { setSettings(data); setLoadState("ready") } })
      .catch((e) => { if (!cancelled) { setError(e instanceof Error ? e.message : "Failed to fetch settings"); setLoadState("error") } })
    return () => { cancelled = true }
  }, [])

  const patch = async (update: SettingsUpdate) => {
    if (!settings) return
    const prev = { ...settings }
    setSettings({ ...settings, ...update } as ServerSettings)
    setPatchError("")
    try {
      await updateSettings(update)
    } catch (e) {
      setSettings(prev)
      setPatchError(e instanceof Error ? e.message : "Failed to save")
      setTimeout(() => setPatchError(""), 3000)
    }
  }

  if (loadState === "loading") {
    return (
      <div className="flex h-full flex-col">
        <Header />
        <div className="flex flex-1 items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading settings...
        </div>
      </div>
    )
  }

  if (loadState === "error" || !settings) {
    return (
      <div className="flex h-full flex-col">
        <Header />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
          <AlertCircle className="h-8 w-8 text-destructive" />
          <p className="text-sm">{error || "Failed to load settings"}</p>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="mr-2 h-3 w-3" />
            Retry
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header />
      {patchError && (
        <div className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          Save failed: {patchError}
        </div>
      )}
      <ScrollArea className="flex-1">
        <TooltipProvider delayDuration={300}>
          <div className="space-y-1 p-4">
            {/* Connection */}
            <SectionHeading icon={Server} label="Connection" open={sections.connection} onToggle={() => toggleSection("connection")} />
            {sections.connection && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <Row label="Server Version" value={settings.version} info="Current MCP server version" />
                  <Row label="Machine ID" value={settings.machine_id} mono info="Unique identifier for this server instance" />
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Feature Tier" info="Determines available features and rate limits" />
                    <Badge variant={settings.feature_tier === "pro" ? "default" : "secondary"}>
                      {settings.feature_tier}
                    </Badge>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Ingestion */}
            <SectionHeading icon={Database} label="Ingestion" open={sections.ingestion} onToggle={() => toggleSection("ingestion")} />
            {sections.ingestion && (
              <Card className="mb-4">
                <CardContent className="grid gap-4 pt-4">
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Categorization Mode" info="How uploaded documents are categorized into KB domains" />
                    <Select
                      value={settings.categorize_mode}
                      onValueChange={(v) => patch({ categorize_mode: v })}
                    >
                      <SelectTrigger size="sm" className="w-28">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="manual">Manual</SelectItem>
                        <SelectItem value="smart">Smart</SelectItem>
                        <SelectItem value="pro">Pro</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <Row
                    label="Chunk Size"
                    value={`${settings.chunk_max_tokens} tokens / ${settings.chunk_overlap} overlap`}
                    info="Max tokens per chunk and overlap between chunks for embedding"
                  />
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Storage Mode" info="Extract-only parses text and discards the file. Archive keeps a copy in the sync directory." />
                    <Select
                      value={settings.storage_mode}
                      onValueChange={(v) => patch({ storage_mode: v })}
                    >
                      <SelectTrigger size="sm" className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="extract_only">Extract Only</SelectItem>
                        <SelectItem value="archive">Archive</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Features */}
            <SectionHeading icon={ToggleLeft} label="Features" open={sections.features} onToggle={() => toggleSection("features")} />
            {sections.features && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <ToggleRow
                    label="Feedback Loop"
                    enabled={settings.enable_feedback_loop}
                    onToggle={(v) => patch({ enable_feedback_loop: v })}
                    info="Saves AI responses back to your knowledge base for continuous improvement"
                  />
                  <ToggleRow
                    label="Hallucination Check"
                    enabled={settings.enable_hallucination_check}
                    onToggle={(v) => patch({ enable_hallucination_check: v })}
                    info="Verifies factual claims in AI responses against your knowledge base"
                  />
                  <ToggleRow
                    label="Memory Extraction"
                    enabled={settings.enable_memory_extraction}
                    onToggle={(v) => patch({ enable_memory_extraction: v })}
                    info="Extracts key facts and preferences from conversations into long-term memory"
                  />
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Model Router" info="Manual: no suggestions. Recommend: shows switch banner. Auto: silently picks the best model." />
                    <Select
                      value={settings.enable_model_router ? "recommend" : "manual"}
                      onValueChange={(v) => {
                        patch({ enable_model_router: v !== "manual" })
                        try { localStorage.setItem("cerid-routing-mode", v) } catch { /* noop */ }
                      }}
                    >
                      <SelectTrigger size="sm" className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="manual">Manual</SelectItem>
                        <SelectItem value="recommend">Recommend</SelectItem>
                        <SelectItem value="auto">Auto</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="my-1 h-px bg-border" />

                  <div className="flex items-center justify-between gap-4">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <LabelWithInfo
                        label={`Hallucination Threshold: ${settings.hallucination_threshold.toFixed(2)}`}
                        info="Confidence threshold for flagging claims (lower = more sensitive)"
                      />
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={settings.hallucination_threshold}
                      onChange={(e) =>
                        patch({ hallucination_threshold: parseFloat(e.target.value) })
                      }
                      className="h-1.5 w-32 cursor-pointer accent-primary"
                      aria-label="Hallucination threshold"
                    />
                  </div>

                  <div className="my-1 h-px bg-border" />

                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Cost Sensitivity" info="How aggressively the model router optimizes for cost vs quality" />
                    <Select
                      value={settings.cost_sensitivity ?? "medium"}
                      onValueChange={(v) => patch({ cost_sensitivity: v })}
                    >
                      <SelectTrigger size="sm" className="w-28">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="low">Low</SelectItem>
                        <SelectItem value="medium">Medium</SelectItem>
                        <SelectItem value="high">High</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Knowledge */}
            <SectionHeading icon={Database} label="Knowledge" open={sections.knowledge} onToggle={() => toggleSection("knowledge")} />
            {sections.knowledge && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <ToggleRow
                    label="Auto-inject KB Context"
                    enabled={settings.enable_auto_inject}
                    onToggle={(v) => patch({ enable_auto_inject: v })}
                    info="Automatically includes relevant KB context when relevance exceeds threshold"
                  />
                  {settings.enable_auto_inject && (
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <LabelWithInfo
                          label={`Injection Threshold: ${settings.auto_inject_threshold.toFixed(2)}`}
                          info="Minimum relevance score to auto-inject (higher = more selective)"
                        />
                      </div>
                      <input
                        type="range"
                        min={0.5}
                        max={1}
                        step={0.05}
                        value={settings.auto_inject_threshold}
                        onChange={(e) =>
                          patch({ auto_inject_threshold: parseFloat(e.target.value) })
                        }
                        className="h-1.5 w-32 cursor-pointer accent-primary"
                        aria-label="Auto-inject threshold"
                      />
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Taxonomy */}
            <SectionHeading icon={Tag} label="Taxonomy" open={sections.taxonomy} onToggle={() => toggleSection("taxonomy")} />
            {sections.taxonomy && (
              <div className="mb-4 grid gap-3">
                {Object.entries(settings.taxonomy).map(([domain, info]) => (
                  <Card key={domain}>
                    <CardHeader className="p-4 pb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-base">{info.icon}</span>
                        <CardTitle className="text-sm capitalize">{domain}</CardTitle>
                      </div>
                      <CardDescription className="text-xs">{info.description}</CardDescription>
                    </CardHeader>
                    {info.sub_categories.length > 0 && (
                      <CardContent className="flex flex-wrap gap-1.5 px-4 pb-3 pt-0">
                        {info.sub_categories.map((sub) => (
                          <Badge key={sub} variant="outline" className="text-[10px]">
                            {sub}
                          </Badge>
                        ))}
                      </CardContent>
                    )}
                  </Card>
                ))}
              </div>
            )}

            {/* Encryption */}
            <SectionHeading icon={Shield} label="Encryption" open={sections.encryption} onToggle={() => toggleSection("encryption")} />
            {sections.encryption && (
              <Card className="mb-4">
                <CardContent className="grid gap-3 pt-4">
                  <div className="flex items-center justify-between">
                    <LabelWithInfo label="Status" info="Whether data at rest is encrypted" />
                    <Badge variant={settings.enable_encryption ? "default" : "secondary"}>
                      {settings.enable_encryption ? "Enabled" : "Disabled"}
                    </Badge>
                  </div>
                  <Row label="Sync Backend" value={settings.sync_backend} info="Storage backend for cross-device sync" />
                </CardContent>
              </Card>
            )}

            {/* Sync */}
            <SectionHeading icon={FolderSync} label="Sync" open={sections.sync} onToggle={() => toggleSection("sync")} />
            {sections.sync && <SyncSection />}

            {/* Feature Flags */}
            <SectionHeading icon={Layers} label="Feature Flags" open={sections.flags} onToggle={() => toggleSection("flags")} />
            {sections.flags && (
              <Card className="mb-4">
                <CardContent className="grid gap-2 pt-4">
                  {Object.entries(settings.feature_flags).map(([flag, enabled]) => (
                    <div key={flag} className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">{flag}</span>
                      <Badge variant={enabled ? "default" : "outline"} className="text-[10px]">
                        {enabled ? "on" : "off"}
                      </Badge>
                    </div>
                  ))}
                  {Object.keys(settings.feature_flags).length === 0 && (
                    <p className="text-xs text-muted-foreground">No feature flags configured</p>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        </TooltipProvider>
      </ScrollArea>
    </div>
  )
}

function Header() {
  return (
    <div className="border-b px-4 py-3">
      <div className="flex items-center gap-2">
        <Settings className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-lg font-semibold">Settings</h2>
      </div>
      <p className="text-xs text-muted-foreground">Server configuration and feature toggles</p>
    </div>
  )
}

function SectionHeading({
  icon: Icon,
  label,
  open,
  onToggle,
}: {
  icon: typeof Cpu
  label: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      className="mb-2 flex w-full cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-muted/50"
      onClick={onToggle}
      aria-expanded={open}
    >
      {open ? (
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      ) : (
        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
      )}
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-medium">{label}</h3>
    </button>
  )
}

function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Info className="h-3.5 w-3.5 shrink-0 cursor-help text-muted-foreground/50 hover:text-muted-foreground" />
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  )
}

function LabelWithInfo({ label, info }: { label: string; info: string }) {
  return (
    <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
      {label}
      <InfoTip text={info} />
    </span>
  )
}

function Row({ label, value, mono, info }: { label: string; value: string; mono?: boolean; info?: string }) {
  return (
    <div className="flex items-center justify-between">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <span className={cn("text-sm", mono && "font-mono text-xs")}>{value}</span>
    </div>
  )
}

function ToggleRow({
  label,
  enabled,
  onToggle,
  info,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
  info?: string
}) {
  return (
    <div className="flex items-center justify-between">
      {info ? (
        <LabelWithInfo label={label} info={info} />
      ) : (
        <span className="text-sm text-muted-foreground">{label}</span>
      )}
      <Button
        variant="ghost"
        size="sm"
        className={cn("h-7 px-2 text-xs", enabled ? "text-green-500" : "text-muted-foreground")}
        onClick={() => onToggle(!enabled)}
      >
        {enabled ? "On" : "Off"}
      </Button>
    </div>
  )
}
