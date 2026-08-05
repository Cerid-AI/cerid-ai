// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState, useCallback, useEffect } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Cpu, ExternalLink, Loader2, Check, Download, Star, HardDrive, Copy, Zap } from "lucide-react"
import { pullOllamaModel, fetchOllamaRecommendations } from "@/lib/api"
import { isModelInstalled } from "@/lib/model-alias"
import type { RecommendedLocalBackend } from "@/lib/types"

interface OllamaState {
  detected: boolean
  enabled: boolean
  model: string | null
  pulling: boolean
}

interface LocalLLMStepProps {
  /** User's chosen inference backend from the Welcome step. Drives the rendered UX. */
  inferenceBackend: RecommendedLocalBackend | null
  ollamaDetected: boolean
  ollamaModels: string[]
  state: OllamaState
  onChange: (state: OllamaState) => void
  /** Hardware detected by the system check; used to gate the CPU-only warning. */
  hardwareGpu?: string | null
  hardwareGpuAcceleration?: string | null
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

/**
 * Step 4 — Local LLM. Backend-aware: renders Ollama / Quenchforge / Cloud-skip
 * UX based on the inference backend chosen on the Welcome step.
 *
 * Backwards-compat: when `inferenceBackend` is null (resumed v2 state, or
 * Welcome step left at default), we fall back to the Ollama UX since that
 * was the historical behavior.
 */
export function LocalLLMStep({
  inferenceBackend,
  ollamaDetected,
  ollamaModels,
  state,
  onChange,
  hardwareGpu,
  hardwareGpuAcceleration,
}: LocalLLMStepProps) {
  const backend: RecommendedLocalBackend = inferenceBackend ?? "ollama"

  if (backend === "cloud") {
    return <CloudBackendStep />
  }
  if (backend === "quenchforge") {
    return (
      <QuenchforgeBackendStep
        ollamaDetected={ollamaDetected}
        state={state}
        onChange={onChange}
        hardwareGpu={hardwareGpu}
        hardwareGpuAcceleration={hardwareGpuAcceleration}
      />
    )
  }
  return (
    <OllamaBackendStep
      ollamaDetected={ollamaDetected}
      ollamaModels={ollamaModels}
      state={state}
      onChange={onChange}
      hardwareGpu={hardwareGpu}
      hardwareGpuAcceleration={hardwareGpuAcceleration}
    />
  )
}

// Backwards-compat export for any callers / tests still importing the old name.
export const OllamaStep = LocalLLMStep

// ---------------------------------------------------------------------------
// Cloud backend: skip-style copy
// ---------------------------------------------------------------------------

function CloudBackendStep() {
  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Cpu className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Local LLM</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">Not required for cloud setup</p>
      <div className="rounded-lg border bg-card p-4 text-center">
        <p className="text-sm text-muted-foreground">
          You chose <span className="font-medium text-foreground">Cloud</span> as your inference
          backend. Pipeline tasks (verification, claim extraction, routing) will run against your
          configured cloud providers. No local model setup is needed.
        </p>
        <p className="mt-3 text-label-xs text-muted-foreground/80">
          You can install Ollama or Quenchforge later from Settings &rarr; Local Inference.
        </p>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Quenchforge backend: GPU-aware, no Ollama Pull buttons, model labels match the
// actual quenchforge slot aliases (llama3.1-8b, bge-reranker-v2-m3, etc).
// ---------------------------------------------------------------------------

function gpuLooksAccelerated(gpu: string | null | undefined, accel: string | null | undefined): boolean {
  if (accel && accel !== "none") return true
  if (!gpu) return false
  const g = gpu.toLowerCase()
  return (
    g.includes("metal") ||
    g.includes("nvidia") ||
    g.includes("cuda") ||
    g.includes("amd") ||
    g.includes("radeon") ||
    g.includes("rocm") ||
    g.includes("vega") ||
    g.includes("apple")
  )
}

function QuenchforgeBackendStep({
  ollamaDetected,
  state,
  onChange,
  hardwareGpu,
  hardwareGpuAcceleration,
}: {
  ollamaDetected: boolean
  state: OllamaState
  onChange: (state: OllamaState) => void
  hardwareGpu?: string | null
  hardwareGpuAcceleration?: string | null
}) {
  const gpuAccelerated = gpuLooksAccelerated(hardwareGpu, hardwareGpuAcceleration)

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Cpu className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Local LLM (Quenchforge)</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">
        Optional — Quenchforge handles model serving for AMD-Mac, NVIDIA, and Apple Silicon GPUs
      </p>

      <div className="space-y-4">
        <p className="text-center text-sm text-muted-foreground">
          Quenchforge speaks the Ollama API and is what Cerid will use for local inference. It
          serves chat, embedding, code-embedding, and rerank slots from a single process.
        </p>

        <div className="flex items-center justify-center gap-2">
          {ollamaDetected ? (
            <Badge
              variant="outline"
              className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400"
            >
              <Check className="mr-1 h-3 w-3" />
              Quenchforge connected
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-yellow-500/30 bg-yellow-500/10 text-yellow-600 dark:text-yellow-400"
            >
              Quenchforge not detected
            </Badge>
          )}
        </div>

        {gpuAccelerated && (
          <div className="flex items-center justify-center gap-1.5">
            <Zap className="h-3 w-3 text-green-500" />
            <p className="text-label-xs text-green-600 dark:text-green-400">
              GPU acceleration available
              {hardwareGpuAcceleration && hardwareGpuAcceleration !== "none"
                ? ` (${hardwareGpuAcceleration})`
                : hardwareGpu
                  ? ` (${hardwareGpu})`
                  : ""}
            </p>
          </div>
        )}

        <div className="rounded-lg border bg-card p-3 space-y-2">
          <p className="text-label-sm font-medium text-muted-foreground">Default Quenchforge slots</p>
          <ul className="space-y-1 text-label-xs text-muted-foreground">
            <li>
              <span className="font-mono text-foreground">llama3.1-8b</span> &mdash; chat
            </li>
            <li>
              <span className="font-mono text-foreground">nomic-embed-text-v1.5</span> &mdash; embeddings
            </li>
            <li>
              <span className="font-mono text-foreground">jina-embeddings-v2-base-code</span> &mdash;
              code embeddings
            </li>
            <li>
              <span className="font-mono text-foreground">bge-reranker-v2-m3</span> &mdash; reranker
              (not a chat model)
            </li>
          </ul>
          <p className="text-label-xxs text-muted-foreground/80">
            Slot models come from your Quenchforge LaunchAgent env; configure there to change.
          </p>
        </div>

        {/* Enable toggle (drives pipeline routing) */}
        <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2.5">
          <div>
            <Label htmlFor="quenchforge-enable-toggle" className="text-xs font-medium">Enable Quenchforge for pipeline tasks</Label>
            <p className="text-label-xs text-muted-foreground">
              Query routing, claim extraction, topic detection
            </p>
          </div>
          <Switch
            id="quenchforge-enable-toggle"
            checked={state.enabled}
            onCheckedChange={(checked) =>
              onChange({
                ...state,
                enabled: checked,
                // Default chat model for the summary card; Quenchforge serves
                // `llama3.1-8b` as the chat slot regardless of detected list.
                model: checked ? state.model ?? "llama3.1-8b" : state.model,
              })
            }
          />
        </div>

        {!ollamaDetected && (
          <div className="rounded-lg border bg-card p-3 space-y-1">
            <p className="text-label-sm font-medium text-muted-foreground">Install Quenchforge</p>
            <p className="text-label-xs text-muted-foreground">
              Cerid expects Quenchforge listening on{" "}
              <span className="font-mono">127.0.0.1:11434</span>. Build from source or use the
              prebuilt LaunchAgent install script.
            </p>
            <a
              href="https://github.com/Cerid-AI/quenchforge"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-label-xs font-medium text-brand hover:underline"
            >
              Quenchforge install guide
              <ExternalLink className="h-2.5 w-2.5" />
            </a>
          </div>
        )}
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Ollama backend (original UX, with the CPU-only warning gated to actually
// CPU-only hardware — was previously firing on AMD-Mac+Quenchforge users).
// ---------------------------------------------------------------------------

function OllamaBackendStep({
  ollamaDetected,
  ollamaModels,
  state,
  onChange,
  hardwareGpu,
  hardwareGpuAcceleration,
}: {
  ollamaDetected: boolean
  ollamaModels: string[]
  state: OllamaState
  onChange: (state: OllamaState) => void
  hardwareGpu?: string | null
  hardwareGpuAcceleration?: string | null
}) {
  const [pullProgress, setPullProgress] = useState<string | null>(null)
  const [pullError, setPullError] = useState<string | null>(null)
  const [hardware, setHardware] = useState<HardwareInfo | null>(null)
  const [modelRecs, setModelRecs] = useState<ModelRecommendation[]>([])

  useEffect(() => {
    if (!ollamaDetected) return
    fetchOllamaRecommendations()
      .then((data) => {
        if (data?.hardware) setHardware(data.hardware)
        if (data?.models) setModelRecs(data.models)
      })
      .catch(() => {
        /* non-critical */
      })
  }, [ollamaDetected])

  const handlePull = useCallback(async () => {
    onChange({ ...state, pulling: true })
    setPullError(null)
    setPullProgress("Starting download...")

    try {
      await pullOllamaModel(RECOMMENDED_MODEL)
      onChange({ ...state, pulling: false, model: RECOMMENDED_MODEL })
      setPullProgress(null)
    } catch (err) {
      setPullError(
        err instanceof Error && err.message
          ? err.message
          : "Failed to pull model — check Ollama is running",
      )
      onChange({ ...state, pulling: false })
      setPullProgress(null)
    }
  }, [state, onChange])

  const hasRecommendedModel = ollamaModels.some(
    (m) => m === RECOMMENDED_MODEL || m.startsWith("llama3.2"),
  )

  // CPU-only warning: was previously firing whenever the GPU string didn't
  // contain "Metal" or "NVIDIA" — which is wrong on AMD-Mac where the user
  // chose Quenchforge specifically because the AMD GPU IS accelerated.
  // Now we check the full set of accelerated-GPU substrings AND prefer the
  // backend-reported `gpu_acceleration` field when available.
  const showCpuOnlyWarning =
    hardware !== null && !gpuLooksAccelerated(hardware.gpu ?? hardwareGpu, hardwareGpuAcceleration)

  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <Cpu className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-2 text-center text-lg font-semibold">Local LLM (Ollama)</h3>
      <p className="mb-4 text-center text-xs text-muted-foreground">Optional</p>

      <div className="space-y-4">
        <p className="text-center text-sm text-muted-foreground">
          Ollama runs AI models locally for free. Cerid uses it for background tasks like
          verification and claim extraction &mdash; your main chat still uses OpenRouter.
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
                  <p className="text-label-sm font-medium text-muted-foreground">Your Hardware</p>
                </div>
                <div className="grid grid-cols-3 gap-2 text-label-xs">
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
                {showCpuOnlyWarning && (
                  <p className="mt-2 text-label-xxs text-yellow-600 dark:text-yellow-400">
                    CPU-only detected &mdash; inference will be slower. GPU acceleration available with Apple Silicon, NVIDIA, or AMD via Quenchforge.
                  </p>
                )}
              </div>
            )}

            {/* Model recommendations (dynamic from backend) */}
            {modelRecs.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-label-sm font-medium text-muted-foreground">Recommended Models</p>
                {modelRecs.map((m) => {
                  // Cross-match recommended (colon-tag, `llama3.2:3b`) against
                  // installed (Quenchforge dash-alias, `llama3.2-3b`) by
                  // normalizing `:`<->`-` and stripping GGUF quant suffixes.
                  // Still tag-specific: installing `llama3.2:3b` doesn't make
                  // `llama3.2:1b` show as Installed.
                  const installed = isModelInstalled(m.id, ollamaModels)
                  return (
                    <div key={m.id} className={`rounded-lg border bg-card p-3 ${!m.compatible ? "opacity-50" : ""}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <p className="text-xs font-medium">{m.name}</p>
                            {m.recommended && (
                              <Badge variant="outline" className="text-label-xxs px-1 py-0 border-brand/30 text-brand">
                                <Star className="mr-0.5 h-2 w-2" /> Recommended
                              </Badge>
                            )}
                            {installed && (
                              <Badge variant="outline" className="text-label-xxs px-1 py-0 border-green-500/30 text-green-600">
                                Installed
                              </Badge>
                            )}
                          </div>
                          <p className="text-label-xs text-muted-foreground mt-0.5">{m.description}</p>
                          <p className="text-label-xxs text-muted-foreground/80 mt-0.5">
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
                          <span className="text-label-xxs text-muted-foreground shrink-0">Needs {m.size_gb * 2}+ GB RAM</span>
                        )}
                      </div>
                    </div>
                  )
                })}
                {pullProgress && <p className="text-label-xs text-muted-foreground">{pullProgress}</p>}
                {pullError && <p className="text-label-xs text-destructive">{pullError}</p>}
              </div>
            )}

            {/* Fallback: static recommendation if backend didn't respond */}
            {modelRecs.length === 0 && !hasRecommendedModel && !state.model && (
              <div className="rounded-lg border bg-card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium">{RECOMMENDED_MODEL}</p>
                    <p className="text-label-xs text-muted-foreground">
                      {RECOMMENDED_MODEL_SIZE} &mdash; best balance of speed and quality for pipeline tasks
                    </p>
                  </div>
                  <Button size="sm" variant="outline" onClick={handlePull} disabled={state.pulling} className="shrink-0">
                    {state.pulling ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Download className="mr-1 h-3 w-3" />}
                    Pull
                  </Button>
                </div>
                {pullProgress && <p className="mt-2 text-label-xs text-muted-foreground">{pullProgress}</p>}
                {pullError && <p className="mt-2 text-label-xs text-destructive">{pullError}</p>}
              </div>
            )}

            {/* Installed Models */}
            {ollamaModels.length > 0 && (
              <div className="rounded-lg border bg-card p-3">
                <p className="mb-2 text-label-sm font-medium text-muted-foreground">
                  Installed Models
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {ollamaModels.map((model) => (
                    <Badge key={model} variant="secondary" className="text-label-xs">
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
                <Label htmlFor="ollama-enable-toggle" className="text-xs font-medium">Enable for pipeline tasks</Label>
                <p className="text-label-xs text-muted-foreground">
                  Query routing, claim extraction, topic detection (not full verification)
                </p>
              </div>
              <Switch
                id="ollama-enable-toggle"
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
              <p className="text-label-sm font-medium text-muted-foreground">Quick Install</p>
              {navigator.platform?.includes("Mac") ? (
                <div className="flex items-center gap-2 rounded bg-muted px-3 py-1.5 font-mono text-label-xs">
                  <span className="flex-1 select-all">brew install ollama && ollama serve</span>
                  <Button variant="ghost" size="sm" className="h-5 w-5 p-0 shrink-0"
                    onClick={() => navigator.clipboard.writeText("brew install ollama && ollama serve")}>
                    <Copy className="h-2.5 w-2.5" />
                  </Button>
                </div>
              ) : navigator.platform?.includes("Linux") ? (
                <div className="flex items-center gap-2 rounded bg-muted px-3 py-1.5 font-mono text-label-xs">
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
                  className="flex items-center justify-center gap-2 rounded bg-muted px-3 py-1.5 text-label-xs font-medium text-brand hover:bg-brand/5"
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
