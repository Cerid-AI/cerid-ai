// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Cpu, ExternalLink, Loader2, Check, Download, Star, HardDrive, Copy } from "lucide-react"
import { pullOllamaModel, fetchOllamaRecommendations } from "@/lib/api"

interface OllamaState {
  detected: boolean
  enabled: boolean
  model: string | null
  pulling: boolean
}

interface OllamaStepProps {
  ollamaDetected: boolean
  ollamaModels: string[]
  state: OllamaState
  onChange: (state: OllamaState) => void
}

const RECOMMENDED_MODEL = "llama3.2:3b"
const RECOMMENDED_MODEL_SIZE = "2.0 GB"

interface HardwareInfo {
  ram_gb: number
  cpu: string
  gpu: string
}

interface ModelRecommendation {
  id: string
  name: string
  origin: string
  size_gb: number
  description: string
  strengths: string
  compatible: boolean
  recommended: boolean
  expected_tokens_per_sec?: number
  ram_usage_pct?: number
}

export function OllamaStep({ ollamaDetected, ollamaModels, state, onChange }: OllamaStepProps) {
  const [pullProgress, setPullProgress] = useState<string | null>(null)
  const [pullError, setPullError] = useState<string | null>(null)
  const [hardware, setHardware] = useState<HardwareInfo | null>(null)
  const [modelRecs, setModelRecs] = useState<ModelRecommendation[]>([])

  useEffect(() => {
    if (!ollamaDetected) return
    fetchOllamaRecommendations().then((data) => {
      if (data?.hardware) setHardware(data.hardware)
      if (data?.models) setModelRecs(data.models)
    }).catch(() => { /* non-critical */ })
  }, [ollamaDetected])

  const handlePull = useCallback(async () => {
    onChange({ ...state, pulling: true })
    setPullError(null)
    setPullProgress("Starting download...")

    try {
      await pullOllamaModel(RECOMMENDED_MODEL)
      onChange({ ...state, pulling: false, model: RECOMMENDED_MODEL })
      setPullProgress(null)
    } catch {
      setPullError("Failed to pull model — check Ollama is running")
      onChange({ ...state, pulling: false })
      setPullProgress(null)
    }
  }, [state, onChange])

  const hasRecommendedModel = ollamaModels.some(
    (m) => m === RECOMMENDED_MODEL || m.startsWith("llama3.2"),
  )

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Cpu className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Local LLM</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">Optional</p>

      <div className="space-y-4">
        <p className="text-center text-sm text-muted-foreground">
          Ollama runs AI models locally for free. Cerid uses it for background tasks like
          verification and claim extraction — your main chat still uses OpenRouter.
        </p>

        {/* Connection Status */}
        <div className="flex items-center justify-center gap-2">
          {ollamaDetected ? (
            <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
              <Check className="mr-1 h-3 w-3" />
              Connected
            </Badge>
          ) : (
            <Badge variant="outline" className="border-yellow-500/30 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
              Not detected
            </Badge>
          )}
        </div>

        {ollamaDetected ? (
          <>
            {/* Hardware info card */}
            {hardware && (
              <div className="rounded-lg border bg-card p-3">
                <div className="flex items-center gap-2 mb-2">
                  <HardDrive className="h-3 w-3 text-muted-foreground" />
                  <p className="text-[11px] font-medium text-muted-foreground">Your Hardware</p>
                </div>
                <div className="grid grid-cols-3 gap-2 text-[10px]">
                  <div>
                    <p className="text-muted-foreground">RAM</p>
                    <p className="font-medium">{hardware.ram_gb} GB</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">CPU</p>
                    <p className="font-medium truncate">{hardware.cpu || "—"}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">GPU</p>
                    <p className="font-medium truncate">{hardware.gpu || "—"}</p>
                  </div>
                </div>
                {!hardware.gpu?.includes("Metal") && !hardware.gpu?.includes("NVIDIA") && (
                  <p className="mt-2 text-[9px] text-yellow-600 dark:text-yellow-400">
                    CPU-only detected — inference will be slower. GPU acceleration available with Apple Silicon or NVIDIA.
                  </p>
                )}
              </div>
            )}

            {/* Model recommendations (dynamic from backend) */}
            {modelRecs.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[11px] font-medium text-muted-foreground">Recommended Models</p>
                {modelRecs.map((m) => {
                  // Match the full tag (`llama3.2:1b`) — not the base name —
                  // so installing `llama3.2:3b` doesn't make `llama3.2:1b`
                  // also show as "Installed". Trailing variant suffixes
                  // (e.g. `llama3.1:8b-instruct-q4_K_M`) still match.
                  const installed = ollamaModels.some(
                    (om) => om === m.id || om.startsWith(`${m.id}-`),
                  )
                  return (
                    <div key={m.id} className={`rounded-lg border bg-card p-3 ${!m.compatible ? "opacity-50" : ""}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <p className="text-xs font-medium">{m.name}</p>
                            {m.recommended && (
                              <Badge variant="outline" className="text-[9px] px-1 py-0 border-brand/30 text-brand">
                                <Star className="mr-0.5 h-2 w-2" /> Recommended
                              </Badge>
                            )}
                            {installed && (
                              <Badge variant="outline" className="text-[9px] px-1 py-0 border-green-500/30 text-green-600">
                                Installed
                              </Badge>
                            )}
                          </div>
                          <p className="text-[10px] text-muted-foreground mt-0.5">{m.description}</p>
                          <p className="text-[9px] text-muted-foreground/80 mt-0.5">
                            {m.origin} · {m.size_gb} GB
                            {(m.expected_tokens_per_sec ?? 0) > 0 && ` · ~${m.expected_tokens_per_sec} tok/s`}
                            {(m.ram_usage_pct ?? 0) > 0 && ` · ${m.ram_usage_pct}% RAM`}
                          </p>
                        </div>
                        {!installed && m.compatible && (
                          <Button size="sm" variant="outline" className="shrink-0 h-7" onClick={handlePull} disabled={state.pulling}>
                            {state.pulling ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Download className="mr-1 h-3 w-3" />}
                            Pull
                          </Button>
                        )}
                        {!m.compatible && (
                          <span className="text-[9px] text-muted-foreground shrink-0">Needs {m.size_gb * 2}+ GB RAM</span>
                        )}
                      </div>
                    </div>
                  )
                })}
                {pullProgress && <p className="text-[10px] text-muted-foreground">{pullProgress}</p>}
                {pullError && <p className="text-[10px] text-destructive">{pullError}</p>}
              </div>
            )}

            {/* Fallback: static recommendation if backend didn't respond */}
            {modelRecs.length === 0 && !hasRecommendedModel && !state.model && (
              <div className="rounded-lg border bg-card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium">{RECOMMENDED_MODEL}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {RECOMMENDED_MODEL_SIZE} — best balance of speed and quality for pipeline tasks
                    </p>
                  </div>
                  <Button size="sm" variant="outline" onClick={handlePull} disabled={state.pulling} className="shrink-0">
                    {state.pulling ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Download className="mr-1 h-3 w-3" />}
                    Pull
                  </Button>
                </div>
                {pullProgress && <p className="mt-2 text-[10px] text-muted-foreground">{pullProgress}</p>}
                {pullError && <p className="mt-2 text-[10px] text-destructive">{pullError}</p>}
              </div>
            )}

            {/* Installed Models */}
            {ollamaModels.length > 0 && (
              <div className="rounded-lg border bg-card p-3">
                <p className="mb-2 text-[11px] font-medium text-muted-foreground">
                  Installed Models
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {ollamaModels.map((model) => (
                    <Badge key={model} variant="secondary" className="text-[10px]">
                      {model}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {state.model && (
              <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 text-center text-xs text-green-600 dark:text-green-400">
                <Check className="mr-1 inline h-3 w-3" />
                {state.model} ready
              </div>
            )}

            {/* Enable toggle */}
            <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2.5">
              <div>
                <Label className="text-xs font-medium">Enable for pipeline tasks</Label>
                <p className="text-[10px] text-muted-foreground">
                  Query routing, claim extraction, topic detection (not full verification)
                </p>
              </div>
              <Switch
                checked={state.enabled}
                onCheckedChange={(checked) => onChange({ ...state, enabled: checked })}
              />
            </div>
          </>
        ) : (
          /* Not detected — platform-specific install instructions */
          <div className="space-y-2">
            <div className="rounded-lg border bg-card p-3">
              <p className="text-xs text-muted-foreground">
                Ollama is not running on this machine. Install it to enable free local AI for
                verification and extraction tasks.
              </p>
            </div>
            <div className="rounded-lg border bg-card p-3 space-y-2">
              <p className="text-[11px] font-medium text-muted-foreground">Quick Install</p>
              {navigator.platform?.includes("Mac") ? (
                <div className="flex items-center gap-2 rounded bg-muted px-3 py-1.5 font-mono text-[10px]">
                  <span className="flex-1 select-all">brew install ollama && ollama serve</span>
                  <Button variant="ghost" size="sm" className="h-5 w-5 p-0 shrink-0"
                    onClick={() => navigator.clipboard.writeText("brew install ollama && ollama serve")}>
                    <Copy className="h-2.5 w-2.5" />
                  </Button>
                </div>
              ) : navigator.platform?.includes("Linux") ? (
                <div className="flex items-center gap-2 rounded bg-muted px-3 py-1.5 font-mono text-[10px]">
                  <span className="flex-1 select-all">curl -fsSL https://ollama.com/install.sh | sh</span>
                  <Button variant="ghost" size="sm" className="h-5 w-5 p-0 shrink-0"
                    onClick={() => navigator.clipboard.writeText("curl -fsSL https://ollama.com/install.sh | sh")}>
                    <Copy className="h-2.5 w-2.5" />
                  </Button>
                </div>
              ) : (
                <a
                  href="https://ollama.com/download"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 rounded bg-muted px-3 py-1.5 text-[10px] font-medium text-brand hover:bg-brand/5"
                >
                  Download from ollama.com
                  <ExternalLink className="h-2.5 w-2.5" />
                </a>
              )}
            </div>
            <a
              href="https://ollama.com/download"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-2 rounded-lg border bg-card p-3 text-xs font-medium text-brand hover:bg-brand/5"
            >
              All platforms
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}
      </div>
    </>
  )
}
