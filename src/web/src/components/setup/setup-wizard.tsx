// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useReducer, useCallback, useEffect, useRef, useState, useMemo } from "react"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import {
  Sparkles, Key, CheckCircle2, Activity,
  ChevronRight, ChevronLeft, Loader2, Check, SkipForward,
  AlertTriangle, Info,
} from "lucide-react"
import { ApiKeyInput } from "@/components/setup/api-key-input"
import { CustomProviderInput } from "@/components/setup/custom-provider-input"
import { HealthDashboard } from "@/components/setup/health-dashboard"
import { SystemCheckCard } from "@/components/setup/system-check-card"
import { KBConfigStep } from "@/components/setup/kb-config-step"
import { OllamaStep } from "@/components/setup/ollama-step"
import { FirstDocumentStep } from "@/components/setup/first-document-step"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"
import { StepIndicator, type StepDef } from "@/components/setup/step-indicator"
import { applySetupConfig, fetchProviderCredits, fetchSetupStatus } from "@/lib/api"
import { useUIMode } from "@/contexts/ui-mode-context"
import { assessCapabilities, fromWizardState, CAPABILITY_STATUS_DOT, COST_PROFILE_LABELS } from "@/lib/provider-capabilities"
import type { CapabilityAssessment, Warning as ProviderWarning } from "@/lib/provider-capabilities"
import { cn } from "@/lib/utils"
import type { ProviderCredits, SystemCheckResponse } from "@/lib/types"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STEP_TRANSITION_MS = 800
const TOTAL_STEPS = 8
const SKIPPABLE_STEPS = new Set([2, 3, 6])
const STORAGE_KEY = "cerid-setup-progress"

const STEP_DEFS: StepDef[] = [
  { label: "Welcome", shortLabel: "Welcome" },
  { label: "API Keys", shortLabel: "Keys" },
  { label: "Storage & Archive", shortLabel: "Storage" },
  { label: "Ollama", shortLabel: "Ollama" },
  { label: "Review & Apply", shortLabel: "Apply" },
  { label: "Service Health", shortLabel: "Health" },
  { label: "Try It Out", shortLabel: "Try" },
  { label: "Choose Mode", shortLabel: "Mode" },
]

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

interface ProviderKey {
  key: string
  valid: boolean
}

interface WizardState {
  step: number
  skippedSteps: Set<number>
  keys: Record<string, ProviderKey>
  applying: boolean
  applyError: string | null
  applied: boolean
  allHealthy: boolean
  healthTimedOut: boolean
  credits: ProviderCredits | null
  systemCheck: SystemCheckResponse | null
  kbConfig: {
    archivePath: string
    domains: string[]
    lightweightMode: boolean
    watchFolder: boolean
  }
  ollama: {
    detected: boolean
    enabled: boolean
    model: string | null
    pulling: boolean
  }
  firstDoc: {
    ingested: boolean
    queried: boolean
    skipped: boolean
  }
  selectedMode: "simple" | "advanced"
}

type WizardAction =
  | { type: "SET_STEP"; step: number }
  | { type: "SKIP_STEP"; step: number }
  | { type: "SET_KEY"; provider: string; key: string; valid: boolean }
  | { type: "SET_APPLYING"; applying: boolean }
  | { type: "SET_APPLY_ERROR"; error: string | null }
  | { type: "SET_APPLIED" }
  | { type: "SET_ALL_HEALTHY" }
  | { type: "SET_HEALTH_TIMED_OUT" }
  | { type: "SET_CREDITS"; credits: ProviderCredits }
  | { type: "SET_SYSTEM_CHECK"; result: SystemCheckResponse }
  | { type: "SET_KB_CONFIG"; config: WizardState["kbConfig"] }
  | { type: "SET_OLLAMA"; state: WizardState["ollama"] }
  | { type: "SET_FIRST_DOC"; state: WizardState["firstDoc"] }
  | { type: "SET_MODE"; mode: "simple" | "advanced" }

function createInitialState(): WizardState {
  return {
    step: 0,
    skippedSteps: new Set(),
    keys: {
      openrouter: { key: "", valid: false },
      openai: { key: "", valid: false },
      anthropic: { key: "", valid: false },
      xai: { key: "", valid: false },
    },
    applying: false,
    applyError: null,
    applied: false,
    allHealthy: false,
    healthTimedOut: false,
    credits: null,
    systemCheck: null,
    kbConfig: {
      archivePath: "~/cerid-archive",
      domains: ["general"],
      lightweightMode: false,
      watchFolder: true,
    },
    ollama: {
      detected: false,
      enabled: false,
      model: null,
      pulling: false,
    },
    firstDoc: {
      ingested: false,
      queried: false,
      skipped: false,
    },
    selectedMode: "simple",
  }
}

function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "SET_STEP":
      return { ...state, step: action.step }
    case "SKIP_STEP":
      return { ...state, skippedSteps: new Set([...state.skippedSteps, action.step]) }
    case "SET_KEY":
      return {
        ...state,
        keys: {
          ...state.keys,
          [action.provider]: { key: action.key, valid: action.valid },
        },
      }
    case "SET_APPLYING":
      return { ...state, applying: action.applying }
    case "SET_APPLY_ERROR":
      return { ...state, applyError: action.error }
    case "SET_APPLIED":
      return { ...state, applied: true, applyError: null }
    case "SET_ALL_HEALTHY":
      return { ...state, allHealthy: true }
    case "SET_HEALTH_TIMED_OUT":
      return { ...state, healthTimedOut: true }
    case "SET_CREDITS":
      return { ...state, credits: action.credits }
    case "SET_SYSTEM_CHECK": {
      const result = action.result
      return {
        ...state,
        systemCheck: result,
        kbConfig: {
          ...state.kbConfig,
          archivePath: result.default_archive_path || state.kbConfig.archivePath,
          lightweightMode: result.lightweight_recommended,
        },
        ollama: {
          ...state.ollama,
          detected: result.ollama_detected,
          model: result.ollama_models.length > 0 ? result.ollama_models[0] : null,
        },
      }
    }
    case "SET_KB_CONFIG":
      return { ...state, kbConfig: action.config }
    case "SET_OLLAMA":
      return { ...state, ollama: action.state }
    case "SET_FIRST_DOC":
      return { ...state, firstDoc: action.state }
    case "SET_MODE":
      return { ...state, selectedMode: action.mode }
    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

function saveProgress(state: WizardState) {
  try {
    const data = {
      step: state.step,
      skippedSteps: [...state.skippedSteps],
      kbConfig: state.kbConfig,
      ollama: state.ollama,
      selectedMode: state.selectedMode,
      applied: state.applied,
      ts: Date.now(),
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  } catch { /* noop */ }
}

function loadProgress(): { step: number; skippedSteps: number[] } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    // Expire after 24 hours
    if (Date.now() - data.ts > 86_400_000) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return data
  } catch {
    return null
  }
}

function clearProgress() {
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* noop */ }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SetupWizardProps {
  open: boolean
  onComplete: () => void
}

export function SetupWizard({ open, onComplete }: SetupWizardProps) {
  const [state, dispatch] = useReducer(wizardReducer, undefined, createInitialState)
  const [showResumePrompt, setShowResumePrompt] = useState(false)
  const [resumeStep, setResumeStep] = useState(0)
  const healthTimerRef = useRef<ReturnType<typeof setTimeout>>(null)
  const { setMode } = useUIMode()

  // Check for saved progress on mount
  useEffect(() => {
    const saved = loadProgress()
    if (saved && saved.step > 0) {
      setResumeStep(saved.step)
      setShowResumePrompt(true)
    }
  }, [])

  // Detect pre-configured keys from backend (e.g. already in .env)
  useEffect(() => {
    fetchSetupStatus()
      .then((status) => {
        // Use unified provider_status map when available (WP2 fix)
        const ps = status.provider_status
        if (ps && Object.keys(ps).length > 0) {
          for (const [provider, info] of Object.entries(ps)) {
            if (info.configured) {
              dispatch({ type: "SET_KEY", provider, key: "(from .env)", valid: true })
            }
          }
        } else if (status.configured && status.missing_keys.length === 0) {
          // Fallback: legacy detection for older backends
          dispatch({ type: "SET_KEY", provider: "openrouter", key: "(configured)", valid: true })
          const optionalProviders = ["openai", "anthropic", "xai"]
          const optionalKeyNames: Record<string, string> = {
            openai: "OPENAI_API_KEY",
            anthropic: "ANTHROPIC_API_KEY",
            xai: "XAI_API_KEY",
          }
          for (const p of optionalProviders) {
            if (!status.optional_keys.includes(optionalKeyNames[p])) {
              dispatch({ type: "SET_KEY", provider: p, key: "(configured)", valid: true })
            }
          }
        }
        // Fetch credits if any provider is configured
        if (status.configured_providers?.length > 0) {
          fetchProviderCredits()
            .then((c) => dispatch({ type: "SET_CREDITS", credits: c }))
            .catch(() => {})
        }
      })
      .catch(() => {})
  }, [])

  // Save progress when step changes
  useEffect(() => {
    if (state.step > 0) saveProgress(state)
  }, [state.step, state])

  // Health timeout — allow proceeding after 30s even with degraded services
  useEffect(() => {
    if (state.step === 5 && !state.allHealthy) {
      healthTimerRef.current = setTimeout(() => {
        dispatch({ type: "SET_HEALTH_TIMED_OUT" })
      }, 30_000)
      return () => {
        if (healthTimerRef.current) clearTimeout(healthTimerRef.current)
      }
    }
  }, [state.step, state.allHealthy])

  const handleKeyValidated = useCallback(
    (provider: string) => (key: string, valid: boolean) => {
      dispatch({ type: "SET_KEY", provider, key, valid })
      if (provider === "openrouter" && valid) {
        fetchProviderCredits()
          .then((c) => dispatch({ type: "SET_CREDITS", credits: c }))
          .catch(() => {})
      }
    },
    [],
  )

  const handleSystemCheckComplete = useCallback((result: SystemCheckResponse) => {
    dispatch({ type: "SET_SYSTEM_CHECK", result })
  }, [])

  const canProceedFromKeys = state.keys.openrouter.valid

  // Capability assessment — recomputed when keys or ollama state changes
  const assessment = useMemo(
    () => assessCapabilities(fromWizardState(state.keys, state.ollama)),
    [state.keys, state.ollama],
  )

  // Only show warnings after user has entered at least one key
  const hasInteractedWithKeys = Object.values(state.keys).some((k) => k.key.length > 0 || k.valid)

  const handleApply = useCallback(async () => {
    dispatch({ type: "SET_APPLYING", applying: true })
    dispatch({ type: "SET_APPLY_ERROR", error: null })
    try {
      const config: Record<string, string> = {}
      for (const [provider, { key, valid }] of Object.entries(state.keys)) {
        if (valid && key) config[provider] = key
      }
      const result = await applySetupConfig({
        keys: config,
        archive_path: state.kbConfig.archivePath,
        domains: ["general"],
        lightweight_mode: state.kbConfig.lightweightMode,
        watch_folder: state.kbConfig.watchFolder,
        ollama_enabled: state.ollama.enabled,
        ollama_model: state.ollama.model ?? undefined,
      })
      if (result.success) {
        dispatch({ type: "SET_APPLIED" })
        setTimeout(() => dispatch({ type: "SET_STEP", step: 5 }), STEP_TRANSITION_MS)
      } else {
        dispatch({ type: "SET_APPLY_ERROR", error: "Configuration failed — check backend logs" })
      }
    } catch {
      dispatch({ type: "SET_APPLY_ERROR", error: "Connection failed — is the backend running?" })
    } finally {
      dispatch({ type: "SET_APPLYING", applying: false })
    }
  }, [state.keys, state.kbConfig, state.ollama])

  const handleAllHealthy = useCallback(() => {
    dispatch({ type: "SET_ALL_HEALTHY" })
  }, [])

  const handleFinish = useCallback(() => {
    setMode(state.selectedMode)
    clearProgress()
    localStorage.setItem("cerid-onboarding-complete", "true")
    onComplete()
  }, [state.selectedMode, setMode, onComplete])

  const goNext = useCallback(() => {
    dispatch({ type: "SET_STEP", step: Math.min(state.step + 1, TOTAL_STEPS - 1) })
  }, [state.step])

  const goBack = useCallback(() => {
    // Skip back over skipped steps
    let prev = state.step - 1
    while (prev > 0 && state.skippedSteps.has(prev)) prev--
    dispatch({ type: "SET_STEP", step: Math.max(prev, 0) })
  }, [state.step, state.skippedSteps])

  const handleSkip = useCallback(() => {
    dispatch({ type: "SKIP_STEP", step: state.step })
    dispatch({ type: "SET_STEP", step: state.step + 1 })
  }, [state.step])

  const handleResume = useCallback((resume: boolean) => {
    setShowResumePrompt(false)
    if (resume) {
      dispatch({ type: "SET_STEP", step: resumeStep })
    }
  }, [resumeStep])

  // Compute config summary for mode selection step
  const validProviders = Object.entries(state.keys).filter(([, k]) => k.valid)
  const providerCount = validProviders.length
  const providerNames = validProviders.map(([name]) => name.charAt(0).toUpperCase() + name.slice(1))
  const domainCount = state.kbConfig.domains.length

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent
        className="max-w-xl gap-0 overflow-hidden p-0 [&>button]:hidden flex flex-col max-h-[85vh]"
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogTitle className="sr-only">Cerid AI Setup</DialogTitle>
        <DialogDescription className="sr-only">
          Setup wizard to configure API keys, knowledge base, and local services.
        </DialogDescription>

        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          {/* Resume prompt */}
          {showResumePrompt && (
            <div className="space-y-3 text-center">
              <Sparkles className="mx-auto h-8 w-8 text-brand" />
              <h3 className="text-lg font-semibold">Welcome back</h3>
              <p className="text-sm text-muted-foreground">
                You left off at step {resumeStep + 1} of {TOTAL_STEPS}. Resume where you were?
              </p>
              <div className="flex justify-center gap-2">
                <Button variant="outline" size="sm" onClick={() => handleResume(false)}>
                  Start Over
                </Button>
                <Button size="sm" onClick={() => handleResume(true)}>
                  Resume
                </Button>
              </div>
            </div>
          )}

          {/* Step 0: Welcome */}
          {!showResumePrompt && state.step === 0 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Sparkles className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-2 text-center text-lg font-semibold">
                Welcome to Cerid AI
              </h3>
              <div className="space-y-3">
                <p className="text-center text-sm text-muted-foreground">
                  Your personal AI knowledge companion. Cerid connects your documents to
                  powerful language models with RAG-powered retrieval, intelligent agents,
                  and built-in verification — all running locally on your machine.
                </p>
                <div className="mx-auto flex max-w-sm flex-col gap-2 text-left text-xs text-muted-foreground">
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 text-brand">✦</span>
                    <span>Chat with AI grounded in your own documents and notes</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 text-brand">✦</span>
                    <span>Multi-domain knowledge base with smart query routing</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 text-brand">✦</span>
                    <span>Verify every AI response against your source documents</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 shrink-0 text-brand">✦</span>
                    <span>Privacy-first — your data never leaves your machine</span>
                  </div>
                </div>
                <p className="text-center text-xs text-muted-foreground/70">
                  This wizard will walk you through connecting an LLM provider,
                  configuring your knowledge base, and ingesting your first document.
                </p>
              </div>
              <SystemCheckCard onCheckComplete={handleSystemCheckComplete} />
            </>
          )}

          {/* Step 1: API Keys */}
          {!showResumePrompt && state.step === 1 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Key className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">API Keys</h3>
              <div className="space-y-4">
                <p className="text-center text-xs text-muted-foreground">
                  OpenRouter is required — it&apos;s a unified gateway that connects Cerid to
                  hundreds of AI models (GPT-4o, Claude, Gemini, Llama, and more) through a
                  single API key. OpenAI and Anthropic keys are optional for direct access.
                </p>
                <ApiKeyInput
                  provider="openrouter"
                  label="OpenRouter API Key"
                  required
                  preconfigured={state.keys.openrouter.key === "(configured)"}
                  placeholder="sk-or-v1-..."
                  helpUrl="https://openrouter.ai/keys"
                  onKeyValidated={handleKeyValidated("openrouter")}
                />
                {!state.keys.openrouter.valid && (
                  <div className="rounded-lg border bg-muted/30 px-3 py-2.5">
                    <p className="mb-1 text-xs font-medium text-muted-foreground">
                      Don&apos;t have an OpenRouter account?
                    </p>
                    <ol className="ml-4 list-decimal space-y-0.5 text-xs text-muted-foreground">
                      <li>
                        <a href="https://openrouter.ai/auth" target="_blank" rel="noopener noreferrer" className="text-brand underline hover:text-brand/80">
                          Create a free account at openrouter.ai
                        </a>
                      </li>
                      <li>Add credits ($5 minimum recommended for getting started)</li>
                      <li>
                        Go to{" "}
                        <a href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer" className="text-brand underline hover:text-brand/80">
                          Keys
                        </a>
                        {" "}and create a new API key
                      </li>
                    </ol>
                  </div>
                )}
                {state.keys.openrouter.valid && state.credits?.configured && state.credits.balance != null && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-3 py-2">
                      <span className="text-xs text-green-600 dark:text-green-400">
                        OpenRouter balance
                      </span>
                      <span className="text-sm font-semibold tabular-nums text-green-600 dark:text-green-400">
                        ${state.credits.balance.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between px-1">
                      <a
                        href="https://openrouter.ai/credits"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[10px] text-brand hover:underline"
                      >
                        Add Credits &rarr;
                      </a>
                      <span className="text-[10px] text-muted-foreground">
                        Credits purchased through OpenRouter, not Cerid
                      </span>
                    </div>
                  </div>
                )}
                {/* Usage rate explainer */}
                {state.keys.openrouter.valid && (
                  <div className="rounded-lg border bg-muted/20 px-3 py-2 text-[10px] text-muted-foreground">
                    Costs vary by model. A typical query costs $0.001-0.01. Verification adds ~$0.001 per 10 claims.
                    Expert mode uses premium models at higher rates.{" "}
                    <a href="https://openrouter.ai/models" target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">
                      See pricing
                    </a>
                  </div>
                )}

                <div className="border-t pt-3">
                  <p className="mb-3 text-[11px] text-muted-foreground">
                    Optional — add direct provider keys for lower latency or specific model access:
                  </p>
                  <div className="space-y-3">
                    <ApiKeyInput
                      provider="openai"
                      label="OpenAI API Key"
                      preconfigured={state.keys.openai.key === "(configured)"}
                      placeholder="sk-proj-..."
                      helpUrl="https://platform.openai.com/api-keys"
                      onKeyValidated={handleKeyValidated("openai")}
                    />
                    <ApiKeyInput
                      provider="anthropic"
                      label="Anthropic API Key"
                      preconfigured={state.keys.anthropic.key === "(configured)"}
                      placeholder="sk-ant-api03-..."
                      helpUrl="https://console.anthropic.com/settings/keys"
                      onKeyValidated={handleKeyValidated("anthropic")}
                    />
                    <ApiKeyInput
                      provider="xai"
                      label="xAI (Grok) API Key"
                      preconfigured={state.keys.xai.key === "(configured)"}
                      placeholder="xai-..."
                      helpUrl="https://console.x.ai/api-keys"
                      onKeyValidated={handleKeyValidated("xai")}
                    />
                    <CustomProviderInput onValidated={() => {}} />
                  </div>
                </div>

                {/* Provider warnings */}
                {hasInteractedWithKeys && assessment.warnings.length > 0 && (
                  <ProviderWarnings warnings={assessment.warnings} />
                )}
              </div>
            </>
          )}

          {/* Step 2: Knowledge Base Config */}
          {!showResumePrompt && state.step === 2 && (
            <KBConfigStep
              config={state.kbConfig}
              onChange={(config) => dispatch({ type: "SET_KB_CONFIG", config })}
              lightweightRecommended={state.systemCheck?.lightweight_recommended ?? false}
              ramGb={state.systemCheck?.ram_gb ?? 0}
            />
          )}

          {/* Step 3: Ollama (Optional) */}
          {!showResumePrompt && state.step === 3 && (
            <OllamaStep
              ollamaDetected={state.ollama.detected}
              ollamaModels={state.systemCheck?.ollama_models ?? []}
              state={state.ollama}
              onChange={(s) => dispatch({ type: "SET_OLLAMA", state: s })}
            />
          )}

          {/* Step 4: Review & Apply */}
          {!showResumePrompt && state.step === 4 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <CheckCircle2 className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">Review &amp; Apply</h3>
              <div className="space-y-3">
                <p className="text-center text-sm text-muted-foreground">
                  The following will be configured:
                </p>

                {/* Providers */}
                <div className="space-y-1.5">
                  {Object.entries(state.keys).map(([provider, { valid }]) => (
                    <div key={provider} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
                      <span className="text-sm font-medium capitalize">{provider}</span>
                      {valid ? (
                        <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                          <Check className="h-3 w-3" />
                          Ready
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">Not configured</span>
                      )}
                    </div>
                  ))}
                </div>

                {/* Storage & Archive Summary */}
                {!state.skippedSteps.has(2) && (
                  <div className="rounded-lg border bg-card px-3 py-2">
                    <p className="text-xs font-medium text-muted-foreground">Storage & Archive</p>
                    <p className="mt-0.5 text-xs">
                      <span className="font-mono">{state.kbConfig.archivePath}</span>
                      {state.kbConfig.lightweightMode && " · Lightweight"}
                      {state.kbConfig.watchFolder && " · Auto-watch"}
                    </p>
                  </div>
                )}

                {/* Ollama Summary */}
                {!state.skippedSteps.has(3) && state.ollama.detected && (
                  <div className="rounded-lg border bg-card px-3 py-2">
                    <p className="text-xs font-medium text-muted-foreground">Ollama</p>
                    <p className="mt-0.5 text-xs">
                      {state.ollama.enabled ? "Enabled" : "Disabled"}
                      {state.ollama.model && ` · ${state.ollama.model}`}
                    </p>
                  </div>
                )}

                {/* Capability Summary */}
                <CapabilitySummary assessment={assessment} />

                {!state.applied && (
                  <Button
                    className="w-full bg-brand text-brand-foreground hover:bg-brand/90"
                    onClick={handleApply}
                    disabled={state.applying || !canProceedFromKeys}
                  >
                    {state.applying ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Applying Configuration...
                      </>
                    ) : (
                      "Apply Configuration"
                    )}
                  </Button>
                )}

                {state.applied && (
                  <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 text-center text-sm text-green-600 dark:text-green-400">
                    Configuration applied successfully
                  </div>
                )}

                {state.applyError && (
                  <p className="text-center text-xs text-destructive">{state.applyError}</p>
                )}
              </div>
            </>
          )}

          {/* Step 5: Service Health */}
          {!showResumePrompt && state.step === 5 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Activity className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">Service Health</h3>
              <HealthDashboard
                polling
                interval={2000}
                onAllHealthy={handleAllHealthy}
                lightweightMode={state.kbConfig.lightweightMode}
              />
              {state.healthTimedOut && !state.allHealthy && (
                <p className="mt-3 text-center text-xs text-muted-foreground">
                  Some services are still starting. You can continue — they&apos;ll catch up.
                </p>
              )}
            </>
          )}

          {/* Step 6: First Document */}
          {!showResumePrompt && state.step === 6 && (
            <FirstDocumentStep
              state={state.firstDoc}
              onChange={(s) => dispatch({ type: "SET_FIRST_DOC", state: s })}
            />
          )}

          {/* Step 7: Mode Selection */}
          {!showResumePrompt && state.step === 7 && (
            <ModeSelectionStep
              selectedMode={state.selectedMode}
              onSelectMode={(mode) => dispatch({ type: "SET_MODE", mode })}
              configSummary={{
                providerCount,
                providerNames,
                domainCount,
                ollamaEnabled: state.ollama.enabled,
                ollamaModel: state.ollama.model,
                documentCount: state.firstDoc.ingested ? 1 : 0,
              }}
            />
          )}
        </div>

        {/* Footer */}
        {!showResumePrompt && (
          <div className="shrink-0 border-t px-6 pb-5 pt-3 space-y-2">
            <div className="flex items-center justify-end gap-2">
              {/* Back button (not on welcome or health) */}
              {state.step > 0 && state.step !== 5 && (
                <Button variant="ghost" size="sm" onClick={goBack}>
                  <ChevronLeft className="mr-1 h-3 w-3" />
                  Back
                </Button>
              )}

              {/* Skip button */}
              {SKIPPABLE_STEPS.has(state.step) && (
                <Button variant="ghost" size="sm" onClick={handleSkip}>
                  <SkipForward className="mr-1 h-3 w-3" />
                  Skip
                </Button>
              )}

              {/* Step 0: Get Started */}
              {state.step === 0 && (
                <Button size="sm" onClick={goNext}>
                  Get Started
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}

              {/* Step 1: Next (requires OpenRouter key) */}
              {state.step === 1 && (
                <Button size="sm" onClick={goNext} disabled={!canProceedFromKeys}>
                  Next
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}

              {/* Steps 2, 3: Next */}
              {(state.step === 2 || state.step === 3) && (
                <Button size="sm" onClick={goNext}>
                  Next
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}

              {/* Step 4: Next (after applied) */}
              {state.step === 4 && state.applied && (
                <Button size="sm" onClick={() => dispatch({ type: "SET_STEP", step: 5 })}>
                  Next
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}

              {/* Step 5: Next (after healthy or timed out) */}
              {state.step === 5 && (state.allHealthy || state.healthTimedOut) && (
                <Button size="sm" onClick={goNext}>
                  {state.allHealthy ? "Next" : "Continue Anyway"}
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}

              {/* Step 6: Next (after queried or can skip) */}
              {state.step === 6 && (
                <Button
                  size="sm"
                  onClick={goNext}
                  disabled={!state.firstDoc.ingested && !state.firstDoc.skipped}
                >
                  Next
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}

              {/* Step 7: Finish */}
              {state.step === 7 && (
                <Button
                  size="sm"
                  onClick={handleFinish}
                  className="bg-brand text-brand-foreground hover:bg-brand/90"
                >
                  Open Cerid AI
                  <ChevronRight className="ml-1 h-3 w-3" />
                </Button>
              )}
            </div>

            <StepIndicator
              steps={STEP_DEFS}
              currentStep={state.step}
              skippedSteps={state.skippedSteps}
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Inline sub-components for provider intelligence
// ---------------------------------------------------------------------------

const SEVERITY_STYLES: Record<string, string> = {
  error: "border-destructive/30 bg-destructive/5 text-destructive",
  warning: "border-yellow-500/30 bg-yellow-500/5 text-yellow-600 dark:text-yellow-400",
  info: "border-blue-500/30 bg-blue-500/5 text-blue-600 dark:text-blue-400",
}

function ProviderWarnings({ warnings }: { warnings: ProviderWarning[] }) {
  return (
    <div className="space-y-2">
      {warnings.map((w, i) => (
        <div key={i} className={cn("flex items-start gap-2 rounded-lg border p-2.5", SEVERITY_STYLES[w.severity])}>
          {w.severity === "error" ? (
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          ) : w.severity === "warning" ? (
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          ) : (
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          )}
          <p className="text-xs leading-relaxed">{w.message}</p>
        </div>
      ))}
    </div>
  )
}


function CapabilitySummary({ assessment }: { assessment: CapabilityAssessment }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <p className="mb-2 text-xs font-medium text-muted-foreground">System Capabilities</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        {assessment.capabilities.map((cap) => (
          <div key={cap.label} className="flex items-center gap-1.5">
            <div className={cn("h-1.5 w-1.5 rounded-full shrink-0", CAPABILITY_STATUS_DOT[cap.status])} />
            <span className="text-[11px] text-muted-foreground">{cap.label}</span>
          </div>
        ))}
      </div>
      {assessment.capabilities.some((c) => c.reason && c.status !== "available") && (
        <div className="mt-2 space-y-0.5 border-t pt-1.5">
          {assessment.capabilities
            .filter((c) => c.reason && c.status !== "available")
            .map((c) => (
              <p key={c.label} className="text-[10px] text-muted-foreground">
                {c.label}: {c.reason}
              </p>
            ))}
        </div>
      )}
      <p className="mt-1.5 text-[10px] text-muted-foreground/70">
        {COST_PROFILE_LABELS[assessment.costProfile]}
      </p>
    </div>
  )
}
