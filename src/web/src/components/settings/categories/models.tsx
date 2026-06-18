// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SaveButton } from "@/components/ui/save-button"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  CheckCircle2,
  Download,
  Loader2,
  Trash2,
  X,
} from "lucide-react"
import { cn, formatCost } from "@/lib/utils"
import {
  fetchOpenRouterKeyStatus,
  putOpenRouterKey,
  testOpenRouterKey,
  fetchWhisperModels,
  startWhisperDownload,
  getWhisperDownloadStatus,
  cancelWhisperDownload,
  deleteWhisperModel,
} from "@/lib/api/settings"
import {
  fetchOllamaStatus,
  fetchOllamaRecommendations,
  enableOllama,
  disableOllama,
  pullOllamaModel,
} from "@/lib/api"
import type { WhisperModelInfo, WhisperDownloadStatus } from "@/lib/api/settings"
import type { OllamaStatus, OllamaRecommendations } from "@/lib/types"
import { SettingRow, AdvancedDisclosure, ConfirmActionButton } from "../settings-primitives"
import { getDef } from "@/lib/settings-registry"
import { logSwallowedError } from "@/lib/log-swallowed"
import type { SettingsCategoryPageProps } from "./page-props"

// ── OpenRouter key field ──────────────────────────────────────────────────────

function OpenRouterKeyInline() {
  const [draft, setDraft] = useState("")
  const [rowError, setRowError] = useState<string | null>(null)
  const qc = useQueryClient()

  const statusQuery = useQuery({
    queryKey: ["openrouter-key-status"],
    queryFn: fetchOpenRouterKeyStatus,
  })

  const putMutation = useMutation({
    mutationFn: putOpenRouterKey,
    onSuccess: () => {
      setDraft("")
      toast.success("OpenRouter key saved")
      qc.invalidateQueries({ queryKey: ["openrouter-key-status"] })
    },
    onError: (err) => {
      setRowError(err instanceof Error ? err.message : "Save failed")
    },
  })

  const testMutation = useMutation({
    mutationFn: (key?: string) => testOpenRouterKey(key),
    onSuccess: (data) => {
      if (data.valid) {
        const credits = data.credits_remaining
        toast.success(
          credits != null
            ? `Key valid — ${formatCost(credits)} credits remaining`
            : "Key valid",
        )
      } else {
        toast.error(data.error ?? "Key validation failed")
      }
    },
    onError: (err) => {
      setRowError(err instanceof Error ? err.message : "Test failed")
    },
  })

  const status = statusQuery.data
  const configured = status?.configured ?? false
  const last4 = status?.last4

  const keyDef = getDef("models.providers.openrouterKey")
  if (!keyDef) return null

  return (
    <SettingRow def={keyDef}>
      <div className="space-y-2 min-w-64">
        {statusQuery.isLoading ? (
          <Skeleton className="h-4 w-40" />
        ) : configured ? (
          <p className="text-label-xs text-muted-foreground">
            Configured — ending in <code className="font-mono">{last4}</code>
          </p>
        ) : (
          <p className="text-label-xs text-muted-foreground">Not configured</p>
        )}
        <div className="flex gap-2">
          <Input
            type="password"
            value={draft}
            onChange={(e) => { setDraft(e.target.value); setRowError(null) }}
            placeholder={configured ? "Enter new key to replace" : "sk-or-..."}
            autoComplete="off"
            spellCheck={false}
            aria-label="OpenRouter API key (write-only)"
            className="flex-1 font-mono h-8 text-sm"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={() => testMutation.mutate(draft || undefined)}
            disabled={!draft && !configured}
          >
            Test
          </Button>
          <SaveButton
            size="sm"
            onSave={async () => { await putMutation.mutateAsync(draft) }}
            disabled={draft.length < 8 || putMutation.isPending}
            idleLabel="Save"
            savedLabel="Saved"
          />
        </div>
        {rowError && (
          <p role="alert" className="text-label-xs text-destructive">{rowError}</p>
        )}
      </div>
    </SettingRow>
  )
}

// ── Whisper model manager ─────────────────────────────────────────────────────

interface ActiveDownload {
  modelId: string
  status: WhisperDownloadStatus
}

function WhisperManagerInline() {
  const [modelsData, setModelsData] = useState<{ models: WhisperModelInfo[]; cache_dir: string; current_default: string } | null>(null)
  const [active, setActive] = useState<Record<string, ActiveDownload>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const pollersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})
  const POLL_TIMEOUT_MS = 120_000

  const refresh = useCallback(async () => {
    try {
      const list = await fetchWhisperModels()
      setModelsData({
        models: list.models ?? [],
        cache_dir: list.cache_dir ?? "",
        current_default: list.current_default ?? "",
      })
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load Whisper models")
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (fetch/subscription); behavior validated in tests
    refresh()
  }, [refresh])

  useEffect(() => {
    const pollers = pollersRef.current
    return () => { Object.values(pollers).forEach(clearInterval) }
  }, [])

  const handleDownload = useCallback(async (modelId: string) => {
    try {
      const { download_id } = await startWhisperDownload(modelId)
      const startedAt = Date.now()
      setActive((prev) => ({
        ...prev,
        [modelId]: {
          modelId,
          status: { download_id, model_id: modelId, state: "pending", bytes_downloaded: 0, bytes_total: null, error: null },
        },
      }))
      const poll = setInterval(async () => {
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          clearInterval(poll)
          delete pollersRef.current[modelId]
          setActive((prev) => {
            const next = { ...prev }
            delete next[modelId]
            return next
          })
          setLoadError("Download timed out after 2 minutes. Retry or check server logs.")
          return
        }
        try {
          const s = await getWhisperDownloadStatus(download_id)
          setActive((prev) => ({ ...prev, [modelId]: { modelId, status: s } }))
          if (s.state === "completed" || s.state === "failed" || s.state === "cancelled") {
            clearInterval(poll)
            delete pollersRef.current[modelId]
            await refresh()
            setTimeout(() => {
              setActive((prev) => { const next = { ...prev }; delete next[modelId]; return next })
            }, s.state === "completed" ? 0 : 3000)
          }
        } catch (e) {
          clearInterval(poll)
          delete pollersRef.current[modelId]
          setLoadError(e instanceof Error ? e.message : "Polling failed")
        }
      }, 500)
      pollersRef.current[modelId] = poll
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Download failed to start")
    }
  }, [refresh])

  const handleCancel = useCallback(async (modelId: string) => {
    const a = active[modelId]
    if (!a) return
    try { await cancelWhisperDownload(a.status.download_id) } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Cancel failed")
    }
  }, [active])

  const managerDef = getDef("models.speechToText.manager")
  if (!managerDef) return null

  return (
    <SettingRow def={managerDef}>
      <div className="w-full space-y-2">
        {loadError && (
          <p role="alert" className="text-label-xs text-destructive">{loadError}</p>
        )}
        {modelsData === null && !loadError && (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        )}
        {modelsData !== null && (
          <div className="space-y-1.5">
            {modelsData.current_default && (
              <p className="text-label-xs text-muted-foreground">
                Active: <code className="font-mono">{modelsData.current_default}</code>
                {" "}(set via <code className="font-mono">WHISPER_MODEL</code>)
              </p>
            )}
            {/* eslint-disable-next-line cerid/no-unsafe-array-on-query-data -- modelsData is from useState (null-guarded above), not useQuery */}
            {modelsData.models.map((m) => {
              const dl = active[m.id]
              const downloading = dl?.status.state === "downloading" || dl?.status.state === "pending"
              const pct = dl?.status.bytes_total
                ? Math.round((dl.status.bytes_downloaded / dl.status.bytes_total) * 100)
                : 0

              return (
                <div
                  key={m.id}
                  className="flex items-center justify-between gap-3 rounded-md border px-2.5 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium">{m.id}</span>
                      {m.cached && (
                        <Badge variant="secondary" className="text-label-xxs px-1 py-0">cached</Badge>
                      )}
                    </div>
                    <p className="text-label-xs text-muted-foreground">
                      {m.size_mb} MB · {m.quality} quality
                    </p>
                    {dl && (
                      <div className="mt-1 text-label-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          {downloading && <Loader2 className="h-3 w-3 animate-spin" />}
                          {dl.status.state}{dl.status.bytes_total ? ` ${pct}%` : ""}
                        </span>
                        {dl.status.error && (
                          <span className="text-destructive"> · {dl.status.error}</span>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {m.cached && !downloading && (
                      <ConfirmActionButton
                        danger="confirm"
                        title={`Delete ${m.id}?`}
                        description={`This removes the cached model file (~${m.size_mb} MB) from disk.`}
                        actionLabel="Delete"
                        onConfirm={async () => {
                          await deleteWhisperModel(m.id)
                          await refresh()
                        }}
                        variant="ghost"
                        size="sm"
                      >
                        <Trash2 className="h-4 w-4" />
                      </ConfirmActionButton>
                    )}
                    {!m.cached && !downloading && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleDownload(m.id)}
                      >
                        <Download className="mr-1 h-3.5 w-3.5" />
                        Download
                      </Button>
                    )}
                    {downloading && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleCancel(m.id)}
                      >
                        <X className="mr-1 h-3.5 w-3.5" />
                        Cancel
                      </Button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </SettingRow>
  )
}

// ── Ollama / Quenchforge wizard ───────────────────────────────────────────────

function OllamaWizardInline({ settings, onRefresh }: Pick<SettingsCategoryPageProps, "settings" | "onRefresh">) {
  const { data: ollamaStatus, refetch: refetchOllama, isLoading } = useQuery<OllamaStatus>({
    queryKey: ["ollama-status"],
    queryFn: fetchOllamaStatus,
    staleTime: 30_000,
  })

  const [toggling, setToggling] = useState(false)
  const [wizardPhase, setWizardPhase] = useState<string | null>(null)
  const [wizardError, setWizardError] = useState<string | null>(null)
  const [pullProgress, setPullProgress] = useState("")
  const [modelRecs, setModelRecs] = useState<OllamaRecommendations | null>(null)
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const POLL_TIMEOUT_MS = 120_000

  const handleToggle = useCallback(async () => {
    if (!ollamaStatus) return
    setToggling(true)
    setWizardError(null)
    try {
      if (settings.internal_llm_provider === "ollama") {
        await disableOllama()
      } else {
        await enableOllama()
      }
      await refetchOllama()
      onRefresh()
    } catch (e) {
      setWizardError(e instanceof Error ? e.message : "Toggle failed")
    }
    setToggling(false)
  }, [ollamaStatus, settings.internal_llm_provider, refetchOllama, onRefresh])

  const pullAndEnable = useCallback(async (model: string) => {
    setWizardPhase("pulling")
    setPullProgress(`Downloading ${model}...`)
    try {
      const res = await pullOllamaModel(model)
      if (res.body) {
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const text = decoder.decode(value, { stream: true })
          for (const line of text.split("\n").filter(Boolean)) {
            try {
              const evt = JSON.parse(line.replace(/^data:\s*/, ""))
              if (evt.total && evt.completed) {
                const pct = Math.round((evt.completed / evt.total) * 100)
                setPullProgress(`Downloading ${model}... ${pct}%`)
              } else if (evt.status) {
                setPullProgress(evt.status)
              }
            } catch (err) { logSwallowedError(err, "json.parse.ollama-pull-progress") }
          }
        }
      }
    } catch (e) {
      setWizardPhase(null)
      setWizardError(e instanceof Error ? e.message : "Model pull failed")
      return
    }
    setWizardPhase("enabling")
    try {
      await enableOllama(model)
      await refetchOllama()
      onRefresh()
      setWizardPhase("complete")
      setTimeout(async () => {
        await refetchOllama()
        onRefresh()
        setWizardPhase(null)
      }, 3000)
    } catch (e) {
      setWizardPhase(null)
      setWizardError(e instanceof Error ? e.message : "Enable failed")
    }
  }, [refetchOllama, onRefresh])

  const startPolling = useCallback(() => {
    setWizardPhase("polling")
    setWizardError(null)
    if (pollingRef.current) clearInterval(pollingRef.current)
    const startedAt = Date.now()
    pollingRef.current = setInterval(async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null }
        setWizardPhase(null)
        setWizardError("Ollama did not become reachable within 2 minutes. Verify the daemon is running on the expected port.")
        return
      }
      try {
        const status = await fetchOllamaStatus()
        if (status.reachable) {
          if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null }
          await refetchOllama()
          try {
            const recs = await fetchOllamaRecommendations()
            setModelRecs(recs)
            setSelectedModel(recs.recommended)
            setWizardPhase("model-select")
          } catch {
            const fallbackModel = status.default_model || ""
            setSelectedModel(fallbackModel || null)
            if (fallbackModel) await pullAndEnable(fallbackModel)
            else setWizardPhase("model-select")
          }
        }
      } catch { /* polling error — keep trying */ }
    }, 3000)
  }, [refetchOllama, pullAndEnable])

  useEffect(() => () => { if (pollingRef.current) clearInterval(pollingRef.current) }, [])

  const isActive = settings.internal_llm_provider === "ollama"
  const ollamaReachable = ollamaStatus?.reachable ?? false

  const statusDef = getDef("models.localInference.ollamaStatus")
  if (!statusDef) return null

  return (
    <SettingRow def={statusDef}>
      <div className="min-w-72 space-y-3">
        <div className="flex items-center justify-between gap-4">
          {isLoading ? (
            <Skeleton className="h-5 w-24" />
          ) : ollamaStatus?.reachable ? (
            <Badge variant="default" className="bg-green-500/20 text-green-700 dark:text-green-400 border-green-500/30 text-label-xs">
              Connected ({ollamaStatus.models.length} model{ollamaStatus.models.length !== 1 ? "s" : ""})
            </Badge>
          ) : ollamaStatus?.enabled ? (
            <Badge variant="outline" className="text-label-xs text-amber-600 border-yellow-500/30">
              Enabled but unreachable
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-label-xs">Not installed</Badge>
          )}
          {ollamaStatus && (
            <Button
              variant={isActive ? "destructive" : "outline"}
              size="sm"
              onClick={handleToggle}
              disabled={toggling || !ollamaStatus}
            >
              {toggling && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              {isActive ? "Disable" : "Enable"}
            </Button>
          )}
        </div>

        {wizardError && (
          <p role="alert" className="text-label-xs text-destructive">{wizardError}</p>
        )}

        {((!ollamaReachable && !isActive) || wizardPhase) && (
          <div className="rounded-lg border border-dashed border-muted-foreground/30 p-3 space-y-2">
            {wizardPhase === "complete" ? (
              <div className="flex items-center gap-2 text-label-sm text-green-600 dark:text-green-400">
                <CheckCircle2 className="h-4 w-4" />
                <span className="font-medium">Ollama is set up and running!</span>
              </div>
            ) : wizardPhase === "enabling" ? (
              <div className="flex items-center gap-2 text-label-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span>Enabling Ollama routing…</span>
              </div>
            ) : wizardPhase === "pulling" ? (
              <div className="space-y-1">
                <div className="flex items-center gap-2 text-label-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>{pullProgress || "Pulling model…"}</span>
                </div>
                <p className="text-label-xs text-muted-foreground/80">This may take several minutes.</p>
              </div>
            ) : wizardPhase === "polling" ? (
              <div className="flex items-center gap-2 text-label-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span>Waiting for Ollama to start…</span>
              </div>
            ) : wizardPhase === "model-select" && modelRecs ? (
              <div className="space-y-2.5">
                <div>
                  <p className="text-label-sm font-medium">Choose a model</p>
                  <p className="text-label-xs text-muted-foreground mt-0.5">
                    {modelRecs.hardware.ram_gb}GB RAM
                    {modelRecs.hardware.gpu ? ` · ${modelRecs.hardware.gpu}` : ""}
                  </p>
                </div>
                <div className="space-y-1.5">
                  {modelRecs.models.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      disabled={!m.compatible}
                      className={cn(
                        "w-full rounded-lg border p-2.5 text-left transition-colors",
                        selectedModel === m.id
                          ? "border-ring bg-primary/5"
                          : m.compatible
                            ? "border-border hover:border-muted-foreground/50"
                            : "border-border opacity-40 cursor-not-allowed",
                      )}
                      onClick={() => m.compatible && setSelectedModel(m.id)}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-label-sm font-medium">{m.name}</span>
                        <span className="text-label-xs text-muted-foreground">{m.size_gb}GB</span>
                      </div>
                      {m.recommended && (
                        <Badge variant="secondary" className="mt-0.5 text-label-xxs px-1 py-0">Recommended</Badge>
                      )}
                      <p className="mt-0.5 text-label-xs text-muted-foreground">{m.description}</p>
                    </button>
                  ))}
                </div>
                <Button
                  size="sm"
                  className="w-full"
                  disabled={!selectedModel}
                  onClick={() => selectedModel && pullAndEnable(selectedModel)}
                >
                  <Download className="mr-1.5 h-3.5 w-3.5" />
                  Download &amp; enable {selectedModel}
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-label-sm">Ollama is not running. Install it or start the daemon, then continue.</p>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" asChild>
                    <a href="https://ollama.com/download" target="_blank" rel="noreferrer">
                      Install Ollama
                    </a>
                  </Button>
                  <Button variant="secondary" size="sm" onClick={startPolling}>
                    I started it — detect
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </SettingRow>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ModelsCategory({ settings, patch, onRefresh }: SettingsCategoryPageProps) {
  return (
    <div className="density-stack">

      {/* Providers */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Providers</h3>
          <OpenRouterKeyInline />
        </CardContent>
      </Card>

      {/* Pipeline tasks */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Pipeline Tasks</h3>

          {(() => {
            const def = getDef("models.pipelineTasks.provider")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Select
                  value={settings.internal_llm_provider ?? "openrouter"}
                  onValueChange={(v) => void patch({ internal_llm_provider: v as "openrouter" | "ollama" | "quenchforge" })}
                >
                  <SelectTrigger className="h-8 w-44 text-sm" aria-label={def.label}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(def.options ?? []).map((o) => (
                      <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </SettingRow>
            )
          })()}

          {(() => {
            const def = getDef("models.pipelineTasks.model")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <ModelTextInput
                  def={def}
                  value={settings.internal_llm_model ?? ""}
                  onCommit={(v) => void patch({ internal_llm_model: v })}
                />
              </SettingRow>
            )
          })()}

          <AdvancedDisclosure category="models" group="pipelineTasks">
            {(() => {
              const embDef = getDef("models.pipelineTasks.embeddingsProvider")
              const rkDef = getDef("models.pipelineTasks.rerankProvider")
              const embedModelDef = getDef("models.pipelineTasks.embedModel")
              const rerankModelDef = getDef("models.pipelineTasks.rerankModel")
              return (
                <>
                  {embDef && (
                    <SettingRow def={embDef}>
                      <Select
                        value={settings.embeddings_provider ?? "sidecar"}
                        onValueChange={(v) => void patch({ embeddings_provider: v as "sidecar" | "quenchforge" | "in-process" })}
                      >
                        <SelectTrigger className="h-8 w-44 text-sm" aria-label={embDef.label}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(embDef.options ?? []).map((o) => (
                            <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </SettingRow>
                  )}
                  {embedModelDef && settings.embeddings_provider === "quenchforge" && (
                    <SettingRow def={embedModelDef} />
                  )}
                  {rkDef && (
                    <SettingRow def={rkDef}>
                      <Select
                        value={settings.rerank_provider ?? "sidecar"}
                        onValueChange={(v) => void patch({ rerank_provider: v as "sidecar" | "quenchforge" | "in-process" })}
                      >
                        <SelectTrigger className="h-8 w-44 text-sm" aria-label={rkDef.label}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(rkDef.options ?? []).map((o) => (
                            <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </SettingRow>
                  )}
                  {rerankModelDef && settings.rerank_provider === "quenchforge" && (
                    <SettingRow def={rerankModelDef} />
                  )}
                </>
              )
            })()}
          </AdvancedDisclosure>
        </CardContent>
      </Card>

      {/* Routing */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Routing</h3>

          {(() => {
            const def = getDef("models.routing.costSensitivity")
            if (!def) return null
            return (
              <SettingRow def={def}>
                <Select
                  value={settings.cost_sensitivity ?? "medium"}
                  onValueChange={(v) => void patch({ cost_sensitivity: v })}
                >
                  <SelectTrigger className="h-8 w-44 text-sm" aria-label={def.label}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(def.options ?? []).map((o) => (
                      <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </SettingRow>
            )
          })()}
        </CardContent>
      </Card>

      {/* Local Inference */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Local Inference</h3>
          <OllamaWizardInline settings={settings} onRefresh={onRefresh} />

          <AdvancedDisclosure category="models" group="localInference">
            {(() => {
              const tierDef = getDef("models.localInference.inferenceTier")
              const endpointDef = getDef("models.localInference.endpoint")
              return (
                <>
                  {tierDef && <SettingRow def={tierDef} />}
                  {endpointDef && <SettingRow def={endpointDef} />}
                </>
              )
            })()}
          </AdvancedDisclosure>
        </CardContent>
      </Card>

      {/* Model catalog */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Model Catalog</h3>
          <AdvancedDisclosure category="models" group="catalog">
            {(() => {
              const updatesDef = getDef("models.catalog.updates")
              const compatDef = getDef("models.catalog.compatDoctor")
              return (
                <>
                  {updatesDef && <SettingRow def={updatesDef} />}
                  {compatDef && <SettingRow def={compatDef} />}
                </>
              )
            })()}
          </AdvancedDisclosure>
        </CardContent>
      </Card>

      {/* Model cache */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Model Cache</h3>
          <AdvancedDisclosure category="models" group="cache">
            {(() => {
              const def = getDef("models.cache.inferenceModels")
              if (!def) return null
              return <SettingRow def={def} />
            })()}
          </AdvancedDisclosure>
        </CardContent>
      </Card>

      {/* Speech-to-text */}
      <Card>
        <CardContent className="pt-4 space-y-4">
          <h3 className="text-label-xs font-medium tracking-wide text-muted-foreground uppercase">Speech to Text</h3>
          <WhisperManagerInline />
        </CardContent>
      </Card>

    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function ModelTextInput({
  def,
  value,
  onCommit,
}: {
  def: { label: string }
  value: string
  onCommit: (v: string) => void
}) {
  const [draft, setDraft] = useState(value)
  // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (server settings refresh); behavior validated in tests
  useEffect(() => { setDraft(value) }, [value])
  return (
    <div className="flex gap-2">
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Leave empty for provider default"
        className="h-8 w-52 font-mono text-sm"
        aria-label={def.label}
      />
      <Button
        variant="secondary"
        size="sm"
        disabled={draft === value}
        onClick={() => onCommit(draft)}
      >
        Apply
      </Button>
    </div>
  )
}
