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
import { Separator } from "@/components/ui/separator"
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
} from "lucide-react"

type LoadState = "loading" | "error" | "ready"

export default function SettingsPane() {
  const [settings, setSettings] = useState<ServerSettings | null>(null)
  const [loadState, setLoadState] = useState<LoadState>("loading")
  const [error, setError] = useState("")
  const [patchError, setPatchError] = useState("")

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
    <div className="flex h-full flex-col">
      <Header />
      {patchError && (
        <div className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          Save failed: {patchError}
        </div>
      )}
      <ScrollArea className="flex-1">
        <div className="space-y-6 p-4">
          {/* Connection */}
          <section>
            <SectionHeading icon={Server} label="Connection" />
            <Card>
              <CardContent className="grid gap-3 pt-4">
                <Row label="Server Version" value={settings.version} />
                <Row label="Machine ID" value={settings.machine_id} mono />
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Feature Tier</span>
                  <Badge variant={settings.feature_tier === "pro" ? "default" : "secondary"}>
                    {settings.feature_tier}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          </section>

          <Separator />

          {/* Ingestion */}
          <section>
            <SectionHeading icon={Database} label="Ingestion" />
            <Card>
              <CardContent className="grid gap-4 pt-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Categorization Mode</span>
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
                />
              </CardContent>
            </Card>
          </section>

          <Separator />

          {/* Features */}
          <section>
            <SectionHeading icon={ToggleLeft} label="Features" />
            <Card>
              <CardContent className="grid gap-3 pt-4">
                <ToggleRow
                  label="Feedback Loop"
                  enabled={settings.enable_feedback_loop}
                  onToggle={(v) => patch({ enable_feedback_loop: v })}
                />
                <ToggleRow
                  label="Hallucination Check"
                  enabled={settings.enable_hallucination_check}
                  onToggle={(v) => patch({ enable_hallucination_check: v })}
                />
                <ToggleRow
                  label="Memory Extraction"
                  enabled={settings.enable_memory_extraction}
                  onToggle={(v) => patch({ enable_memory_extraction: v })}
                />

                <Separator />

                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm text-muted-foreground">Hallucination Threshold</p>
                    <p className="text-xs text-muted-foreground/70">
                      {settings.hallucination_threshold.toFixed(2)}
                    </p>
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

                <Separator />

                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Cost Sensitivity</span>
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
          </section>

          <Separator />

          {/* Taxonomy */}
          <section>
            <SectionHeading icon={Tag} label="Taxonomy" />
            <div className="grid gap-3">
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
          </section>

          <Separator />

          {/* Encryption */}
          <section>
            <SectionHeading icon={Shield} label="Encryption" />
            <Card>
              <CardContent className="grid gap-3 pt-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <Badge variant={settings.enable_encryption ? "default" : "secondary"}>
                    {settings.enable_encryption ? "Enabled" : "Disabled"}
                  </Badge>
                </div>
                <Row label="Sync Backend" value={settings.sync_backend} />
              </CardContent>
            </Card>
          </section>

          <Separator />

          {/* Feature Flags */}
          <section>
            <SectionHeading icon={Layers} label="Feature Flags" />
            <Card>
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
          </section>
        </div>
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

function SectionHeading({ icon: Icon, label }: { icon: typeof Cpu; label: string }) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-medium">{label}</h3>
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={cn("text-sm", mono && "font-mono text-xs")}>{value}</span>
    </div>
  )
}

function ToggleRow({
  label,
  enabled,
  onToggle,
}: {
  label: string
  enabled: boolean
  onToggle: (value: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
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