// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useReducer, useCallback, useEffect, useRef, useState, useMemo } from "react"
import { logSwallowedError } from "@/lib/log-swallowed"
import { setSettingsMode } from "@/lib/settings-mode"
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
import { LocalLLMStep } from "@/components/setup/local-llm-step"
import { FirstDocumentStep, type FirstDocState } from "@/components/setup/first-document-step"
import { BuildKnowledgeStep } from "@/components/setup/build-knowledge-step"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"
import { BackendRecommendationStep } from "@/components/setup/backend-recommendation-step"
import { QuenchforgeInstallStep } from "@/components/setup/quenchforge-install-step"
import { StepIndicator, type StepDef } from "@/components/setup/step-indicator"
import { fetchProviderCredits, fetchSetupStatus } from "@/lib/api"
import { applySetupConfiguration, completeOnboarding } from "@/lib/api/setup"
import { assessCapabilities, fromWizardState, CAPABILITY_STATUS_DOT, COST_PROFILE_LABELS } from "@/lib/provider-capabilities"
import type { CapabilityAssessment, Warning as ProviderWarning } from "@/lib/provider-capabilities"
import { cn } from "@/lib/utils"
import type { ProviderCredits, RecommendedLocalBackend, SystemCheckResponse } from "@/lib/types"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOTAL_STEPS = 9
// 0-indexed steps that show a Skip button. Mapping:
//   2 → Storage & Archive
//   3 → Local LLM
//   6 → Build Knowledge
//   7 → Try It Out
const SKIPPABLE_STEPS = new Set([2, 3, 6, 7])
const STORAGE_KEY = "cerid-setup-progress"
/**
 * Persisted-state schema version. v4 (2026-06-22) removed the Telemetry
 * Consent step; index 7 is now Mode (was 8). Stores from older versions
 * have a different step layout, so loading them unchanged would land the
 * user on a now-nonexistent step. We drop them and restart rather than
 * transform — saves are 24-hour-ephemeral anyway (see `loadProgress`).
 *
 * v1 → v2: added Backend Recommendation, Quenchforge Install surfaces.
 * v2 → v3: split Step 8 into Telemetry + Mode, TOTAL_STEPS 8→9.
 * v3 → v4: removed Telemetry Consent step, TOTAL_STEPS 9→8.
 * v4 → v5: added Build Knowledge step (index 6), TOTAL_STEPS 8→9.
 */
const STORAGE_SCHEMA_VERSION = 5

// Display-only sentinels the masked API-key input renders when an
// env-loaded key is already configured. Sending them on the wire would
// cause the backend to literally write "(from .env)" over the real key.
const PLACEHOLDER_KEYS = new Set(["(from .env)", "(configured)", "__env__"])

// Display labels for providers whose brand casing isn't title-case.
// React's `capitalize` would render "Openrouter" / "Openai" / "Xai".
const PROVIDER_LABELS: Record<string, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  anthropic: "Anthropic",
  xai: "xAI",
}

const STEP_DEFS: StepDef[] = [
  { label: "Welcome", shortLabel: "Welcome" },
  { label: "API Keys", shortLabel: "Keys" },
  { label: "Storage & Archive", shortLabel: "Storage" },
  { label: "Local LLM", shortLabel: "Local LLM" },
  { label: "Review & Apply", shortLabel: "Apply" },
  { label: "Service Health", shortLabel: "Health" },
  { label: "Build Knowledge", shortLabel: "Knowledge" },
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
  firstDoc: FirstDocState
  /** Pack ids installed via the Build Knowledge step (SW5). */
  installedPackIds: string[]
  selectedMode: "simple" | "advanced"
  customProvider: { name: string; baseUrl: string; apiKey: string; modelId: string; valid: boolean } | null
  /** User's chosen local-inference backend. null = follow recommendation. */
  selectedBackend: RecommendedLocalBackend | null
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
  | { type: "SET_BUILD_KNOWLEDGE"; installedPackIds: string[]; firstDoc: WizardState["firstDoc"] }
  | { type: "SET_MODE"; mode: "simple" | "advanced" }
  | { type: "SET_CUSTOM_PROVIDER"; provider: WizardState["customProvider"] }
  | { type: "SET_BACKEND"; backend: RecommendedLocalBackend }

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
      watchFolder: false,
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
      documentCount: 0,
    },
    installedPackIds: [],
    selectedMode: "simple",
    customProvider: null,
    selectedBackend: null,
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
          enabled: result.ollama_detected,  // auto-enable when Ollama is available
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
    case "SET_BUILD_KNOWLEDGE":
      return { ...state, installedPackIds: action.installedPackIds, firstDoc: action.firstDoc }
    case "SET_MODE":
      return { ...state, selectedMode: action.mode }
    case "SET_CUSTOM_PROVIDER":
      return { ...state, customProvider: action.provider }
    case "SET_BACKEND":
      return { ...state, selectedBackend: action.backend }
    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

interface PersistedProgress {
  version: number
  step: number
  skippedSteps: number[]
  kbConfig: WizardState["kbConfig"]
  ollama: WizardState["ollama"]
  selectedMode: WizardState["selectedMode"]
  selectedBackend: WizardState["selectedBackend"]
  applied: boolean
  ts: number
}

function saveProgress(state: WizardState) {
  try {
    const data: PersistedProgress = {
      version: STORAGE_SCHEMA_VERSION,
      step: state.step,
      skippedSteps: [...state.skippedSteps],
      kbConfig: state.kbConfig,
      ollama: state.ollama,
      selectedMode: state.selectedMode,
      selectedBackend: state.selectedBackend,
      applied: state.applied,
      ts: Date.now(),
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  } catch (err) { logSwallowedError(err, "localStorage.setItem", { key: STORAGE_KEY }) }
}

function loadProgress(): PersistedProgress | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    // v1 → v2 migration: persisted state pre-Backend-Recommendation has no
    // `version` field or version=1. Drop it rather than transform; saves are
    // 24h-ephemeral and a clean restart is less risky than backfilling new
    // fields with assumed defaults.
    if (data.version !== STORAGE_SCHEMA_VERSION) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    // Expire after 24 hours
    if (Date.now() - data.ts > 86_400_000) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return data as PersistedProgress
  } catch {
    return null
  }
}

function clearProgress() {
  try { localStorage.removeItem(STORAGE_KEY) } catch (err) { logSwallowedError(err, "localStorage.removeItem", { key: STORAGE_KEY }) }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SetupWizardProps {
  open: boolean
  /** When true, shows a "Skip Setup" link — used when the backend is already configured but the user hasn't completed onboarding locally. */
  canSkip?: boolean
  onComplete: () => void
}

export function SetupWizard({ open, canSkip, onComplete }: SetupWizardProps) {
  const [state, dispatch] = useReducer(wizardReducer, undefined, createInitialState)
  const [showResumePrompt, setShowResumePrompt] = useState(false)
  const [resumeStep, setResumeStep] = useState(0)
  // Already-configured guard (beta triage 2026-07-12 P0-B4): applying the
  // wizard on a configured backend must be an explicit, confirmed overwrite.
  const [backendConfigured, setBackendConfigured] = useState(false)
  const [confirmOverwrite, setConfirmOverwrite] = useState(false)
  const healthTimerRef = useRef<ReturnType<typeof setTimeout>>(null)

  // Check for saved progress on mount — but only if backend hasn't been reset
  useEffect(() => {
    fetchSetupStatus()
      .then((status) => {
        // If setup_required is true AND we have saved progress with applied=true,
        // the system was reset — clear stale wizard state
        const saved = loadProgress()
        if (saved && saved.applied && status.setup_required) {
          localStorage.removeItem(STORAGE_KEY)
          return // fresh start
        }
        if (saved && saved.step > 0) {
          setResumeStep(saved.step)
          setShowResumePrompt(true)
        }
      })
      .catch(() => {
        // If backend unreachable, still allow resume from local state
        const saved = loadProgress()
        if (saved && saved.step > 0) {
          setResumeStep(saved.step)
          setShowResumePrompt(true)
        }
      })
  }, [])

  // Detect pre-configured keys from backend (e.g. already in .env)
  useEffect(() => {
    fetchSetupStatus()
      .then((status) => {
        setBackendConfigured(!!status.configured)
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

  // A detected+enabled local backend (Ollama/Quenchforge) is a valid
  // alternative to an OpenRouter key — see task 1.3a.
  const canProceedFromKeys = state.keys.openrouter.valid || state.ollama.enabled

  // Capability assessment — recomputed when keys or ollama state changes
  const assessment = useMemo(
    () => assessCapabilities(fromWizardState(state.keys, state.ollama)),
    [state.keys, state.ollama],
  )

  // Only show warnings after user has entered at least one key
  const hasInteractedWithKeys = Object.values(state.keys).some((k) => k.key.length > 0 || k.valid)

  const handleApply = useCallback(async (opts?: { force?: boolean }) => {
    // Re-running the wizard on a configured instance must not silently
    // rewrite live env config — require an explicit overwrite confirmation
    // and only then send force=true (the backend 409s without it).
    if (backendConfigured && !opts?.force) {
      setConfirmOverwrite(true)
      return
    }
    setConfirmOverwrite(false)
    dispatch({ type: "SET_APPLYING", applying: true })
    dispatch({ type: "SET_APPLY_ERROR", error: null })
    try {
      const config: Record<string, string> = {}
      for (const [provider, { key, valid }] of Object.entries(state.keys)) {
        // Skip the masked-input display sentinels — sending these would
        // cause the backend to literally write "(from .env)" over the
        // real env-loaded key. The backend has a sentinel guard too
        // (defence-in-depth) but we never want this string on the wire.
        if (valid && key && !PLACEHOLDER_KEYS.has(key)) config[provider] = key
      }
      const result = await applySetupConfiguration({
        keys: config,
        archive_path: state.kbConfig.archivePath,
        domains: state.kbConfig.domains,
        lightweight_mode: state.kbConfig.lightweightMode,
        watch_folder: state.kbConfig.watchFolder,
        ollama_enabled: state.ollama.enabled,
        ollama_model: state.ollama.model ?? undefined,
      }, { force: opts?.force ?? false })
      if (result.success) {
        // M-A.7: drop the 800ms `setTimeout` gate — the new step's wrapper
        // animates in on key change so the visual transition is the feedback.
        dispatch({ type: "SET_APPLIED" })
        dispatch({ type: "SET_STEP", step: 5 })
      } else if (result.conflict) {
        // Backend's 409 already-configured guard — surface its message.
        dispatch({
          type: "SET_APPLY_ERROR",
          error: result.error ?? "This instance is already configured — pass force to reconfigure.",
        })
      } else {
        dispatch({ type: "SET_APPLY_ERROR", error: "Configuration failed — check backend logs" })
      }
    } catch {
      dispatch({ type: "SET_APPLY_ERROR", error: "Connection failed — is the backend running?" })
    } finally {
      dispatch({ type: "SET_APPLYING", applying: false })
    }
  }, [state.keys, state.kbConfig, state.ollama, backendConfigured])

  const handleAllHealthy = useCallback(() => {
    dispatch({ type: "SET_ALL_HEALTHY" })
  }, [])

  const handleFinish = useCallback(() => {
    setSettingsMode(state.selectedMode)
    clearProgress()
    try {
      localStorage.setItem("cerid-onboarding-complete", "true")
    } catch (err) {
      logSwallowedError(err, "localStorage.setItem", { key: "cerid-onboarding-complete" })
    }
    // Server-side flag is the source of truth (fresh browsers must not
    // re-enter the wizard on a configured instance) — persist best-effort;
    // the localStorage cache above covers an unreachable backend.
    completeOnboarding().catch((err) => logSwallowedError(err, "setup.onboarding-complete"))
    onComplete()
  }, [onComplete, state.selectedMode])

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
  const providerNames = validProviders.map(([name]) => PROVIDER_LABELS[name] ?? name.charAt(0).toUpperCase() + name.slice(1))
  const domainCount = state.kbConfig.domains.length

  // Chat-model label for the Mode summary. The previous wizard mislabelled
  // the rerank slot model (`bge-reranker-v2-m3`) as "Local LLM", which is a
  // category error — rerankers aren't chat LLMs. Pick a sensible chat-slot
  // alias per backend; for Ollama, surface the user's downloaded chat model.
  const modeSummaryChatModel: string | null = (() => {
    if (state.selectedBackend === "quenchforge") return "llama3.1-8b"
    if (state.selectedBackend === "cloud") return null
    // Ollama (or null/legacy): use the explicit model the user pulled, but
    // never the reranker — Quenchforge users on a v2-persisted state can
    // have `state.ollama.model` set to `bge-reranker-v2-m3` from the prior
    // wizard's mislabelled flow.
    if (state.ollama.model && !state.ollama.model.toLowerCase().includes("reranker")) {
      return state.ollama.model
    }
    return null
  })()

  // Document count for the Mode summary. Single upload sets count=1; sample
  // pack install sets count=pack.artifact_count. Both flow through
  // firstDoc.documentCount so the summary doesn't show stale "0 documents"
  // after a successful pack install (F-04-07).
  const modeSummaryDocCount: number = state.firstDoc.documentCount

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent
        className="max-w-xl gap-0 overflow-hidden p-0 [&>button]:hidden flex flex-col max-h-[85vh] bg-circuit"
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

          {/* M-A.7: step content wrapped with a key={state.step} container so
              each transition animates in (replaces the previous 800ms setTimeout
              gate). The wrapper only mounts when the resume prompt isn't showing. */}
          {!showResumePrompt && (
            <div key={state.step} className="animate-in fade-in zoom-in-95 duration-300">
          {/* Step 0: Welcome.

              Layout (post-Cluster-E): the Inference Backend selector is the
              most consequential decision on this page, so it sits at the top
              the moment system-check completes. The intro/value-prop copy
              follows below as secondary context. Previously the selector was
              the LAST card on the page, below 4 value-prop bullets and the
              system-check card — users routinely scrolled past it. */}
          {state.step === 0 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 glow-teal">
                  <Sparkles className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-3 text-center text-lg font-semibold">
                Welcome to Cerid <span className="text-brand-gradient">AI</span>
              </h3>

              <SystemCheckCard onCheckComplete={handleSystemCheckComplete} />

              {/* Backend recommendation immediately follows the system-check
                  card; the recommendation is hardware-driven, so we need the
                  check result first. Quenchforge Install appears only when the
                  user picks quenchforge and the local backend isn't already
                  responding. */}
              {state.systemCheck && (
                <div className="mt-4 space-y-4">
                  <BackendRecommendationStep
                    systemCheck={state.systemCheck}
                    selected={state.selectedBackend}
                    onSelect={(backend) => dispatch({ type: "SET_BACKEND", backend })}
                  />
                  {state.selectedBackend === "quenchforge" && !state.systemCheck.ollama_detected && (
                    <div className="border-t pt-4">
                      <QuenchforgeInstallStep
                        systemCheck={state.systemCheck}
                        onSystemCheckRefresh={(result) =>
                          dispatch({ type: "SET_SYSTEM_CHECK", result })
                        }
                      />
                    </div>
                  )}
                </div>
              )}

              {/* Secondary context: what Cerid is and what's next. Kept below
                  the actionable decision above — users who already know Cerid
                  can pick a backend without scrolling. */}
              <div className="mt-6 space-y-3 border-t pt-4">
                <p className="text-center text-sm text-muted-foreground">
                  Your personal AI knowledge companion. Cerid connects your documents to
                  powerful language models with RAG-powered retrieval, intelligent agents,
                  and built-in verification &mdash; all running locally on your machine.
                </p>
                <div className="mx-auto flex max-w-sm flex-col gap-2 text-left text-xs text-muted-foreground">
                  <div className="flex items-start gap-2">
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand" aria-hidden="true" />
                    <span>Chat with AI grounded in your own documents and notes</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand" aria-hidden="true" />
                    <span>Multi-domain knowledge base with smart query routing</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand" aria-hidden="true" />
                    <span>Verify every AI response against your source documents</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-brand" aria-hidden="true" />
                    <span>
                      Privacy-first &mdash; your knowledge stores stay on your machine;
                      inference goes to the provider you choose
                    </span>
                  </div>
                </div>
                <p className="text-center text-xs text-muted-foreground/80">
                  Next: connecting an LLM provider, configuring your knowledge base, and
                  ingesting your first document.
                </p>
              </div>
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
              {/* <form> wrapper required by Chrome's a11y check —
                  password fields outside a form trip "Password field is
                  not contained in a form" + screen-reader navigation. */}
              <form className="space-y-4" onSubmit={(e) => e.preventDefault()}>
                <p className="text-center text-xs text-muted-foreground">
                  {state.ollama.enabled ? (
                    <>
                      A local inference backend was detected and is enabled — you can
                      continue without an API key. Without OpenRouter, smart categorization
                      and automatic model updates are unavailable, and chat quality is
                      limited to your local model.
                    </>
                  ) : (
                    <>
                      OpenRouter is required — it&apos;s a unified gateway that connects Cerid to
                      hundreds of AI models (GPT-4o, Claude, Gemini, Llama, and more) through a
                      single API key. OpenAI and Anthropic keys are optional for direct access.
                    </>
                  )}
                </p>
                <ApiKeyInput
                  provider="openrouter"
                  label="OpenRouter API Key"
                  required={!state.ollama.enabled}
                  preconfigured={state.keys.openrouter.key === "(configured)" || state.keys.openrouter.key === "(from .env)"}
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
                        className="text-label-xs text-brand hover:underline"
                      >
                        Add Credits &rarr;
                      </a>
                      <span className="text-label-xs text-muted-foreground">
                        Credits purchased through OpenRouter, not Cerid
                      </span>
                    </div>
                  </div>
                )}
                {/* Usage rate explainer */}
                {state.keys.openrouter.valid && (
                  <div className="rounded-lg border bg-muted/20 px-3 py-2 text-label-xs text-muted-foreground">
                    Costs vary by model. A typical query costs $0.001-0.01. Verification adds ~$0.001 per 10 claims.
                    Expert mode uses premium models at higher rates.{" "}
                    <a href="https://openrouter.ai/models" target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">
                      See pricing
                    </a>
                  </div>
                )}

                <div className="border-t pt-3">
                  <p className="mb-3 text-label-sm text-muted-foreground">
                    Optional — add direct provider keys for lower latency or specific model access:
                  </p>
                  <div className="space-y-3">
                    <ApiKeyInput
                      provider="openai"
                      label="OpenAI API Key"
                      preconfigured={state.keys.openai.key === "(configured)" || state.keys.openai.key === "(from .env)"}
                      placeholder="sk-proj-..."
                      helpUrl="https://platform.openai.com/api-keys"
                      onKeyValidated={handleKeyValidated("openai")}
                    />
                    <ApiKeyInput
                      provider="anthropic"
                      label="Anthropic API Key"
                      preconfigured={state.keys.anthropic.key === "(configured)" || state.keys.anthropic.key === "(from .env)"}
                      placeholder="sk-ant-api03-..."
                      helpUrl="https://console.anthropic.com/settings/keys"
                      onKeyValidated={handleKeyValidated("anthropic")}
                    />
                    <ApiKeyInput
                      provider="xai"
                      label="xAI (Grok) API Key"
                      preconfigured={state.keys.xai.key === "(configured)" || state.keys.xai.key === "(from .env)"}
                      placeholder="xai-..."
                      helpUrl="https://console.x.ai/api-keys"
                      onKeyValidated={handleKeyValidated("xai")}
                    />
                    <CustomProviderInput onValidated={(cp) => dispatch({ type: "SET_CUSTOM_PROVIDER", provider: cp })} />
                  </div>
                </div>

                {/* Provider warnings */}
                {hasInteractedWithKeys && assessment.warnings.length > 0 && (
                  <ProviderWarnings warnings={assessment.warnings} />
                )}
              </form>
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

          {/* Step 3: Local LLM (backend-aware: Ollama / Quenchforge / Cloud-skip) */}
          {!showResumePrompt && state.step === 3 && (
            <LocalLLMStep
              inferenceBackend={state.selectedBackend}
              ollamaDetected={state.ollama.detected}
              ollamaModels={state.systemCheck?.ollama_models ?? []}
              state={state.ollama}
              onChange={(s) => dispatch({ type: "SET_OLLAMA", state: s })}
              hardwareGpu={state.systemCheck?.gpu ?? null}
              hardwareGpuAcceleration={state.systemCheck?.gpu_acceleration ?? null}
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
                      <span className="text-sm font-medium">{PROVIDER_LABELS[provider] ?? provider}</span>
                      {valid ? (
                        <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                          <Check className="h-3 w-3" />
                          Ready
                        </span>
                      ) : (
                        <span className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">Not configured</span>
                          {provider !== "openrouter" && (
                            <span className="text-label-xs text-muted-foreground/80">Optional</span>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-5 px-1.5 text-label-xs text-brand"
                            onClick={() => dispatch({ type: "SET_STEP", step: 1 })}
                          >
                            Fix →
                          </Button>
                        </span>
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

                {/* Inference Backend (from Welcome step — F-04-05) */}
                {state.selectedBackend && (
                  <div className="rounded-lg border bg-card px-3 py-2">
                    <p className="text-xs font-medium text-muted-foreground">Inference Backend</p>
                    <p className="mt-0.5 text-xs">
                      {state.selectedBackend === "quenchforge" && "Quenchforge (local, GPU-accelerated)"}
                      {state.selectedBackend === "ollama" && "Ollama (local)"}
                      {state.selectedBackend === "cloud" && "Cloud providers only"}
                    </p>
                  </div>
                )}

                {/* Local LLM Summary (skip the bge-reranker mislabel: hide
                    the line when only the rerank slot model is set). */}
                {!state.skippedSteps.has(3) && state.ollama.detected && (
                  <div className="rounded-lg border bg-card px-3 py-2">
                    <p className="text-xs font-medium text-muted-foreground">
                      {state.selectedBackend === "quenchforge" ? "Quenchforge" : "Ollama"}
                    </p>
                    <p className="mt-0.5 text-xs">
                      {state.ollama.enabled ? "Enabled" : "Disabled"}
                      {state.ollama.model && !state.ollama.model.toLowerCase().includes("reranker") && (
                        ` · ${state.ollama.model}`
                      )}
                    </p>
                  </div>
                )}

                {/* Capability Summary */}
                <CapabilitySummary
                  assessment={assessment}
                  inferenceBackend={state.selectedBackend}
                />

                {!state.applied && !confirmOverwrite && (
                  <Button
                    className="w-full bg-brand text-brand-foreground hover:bg-brand/90"
                    onClick={() => handleApply()}
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

                {/* Overwrite confirmation — shown instead of the Apply button
                    when the backend is already configured (P0-B4 guard). */}
                {!state.applied && confirmOverwrite && (
                  <div className="space-y-2 rounded-lg border border-yellow-500/40 bg-yellow-500/5 p-3">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600 dark:text-yellow-400" aria-hidden="true" />
                      <p className="text-xs leading-relaxed text-yellow-700 dark:text-yellow-400">
                        This instance is already configured — overwrite settings?
                        Applying will rewrite the stored configuration with the
                        values above.
                      </p>
                    </div>
                    <div className="flex justify-end gap-2">
                      <Button variant="outline" size="sm" onClick={() => setConfirmOverwrite(false)}>
                        Cancel
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={state.applying}
                        onClick={() => handleApply({ force: true })}
                      >
                        Overwrite Settings
                      </Button>
                    </div>
                  </div>
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

          {/* Step 6: Build Knowledge */}
          {!showResumePrompt && state.step === 6 && (
            <BuildKnowledgeStep
              state={{ installedPackIds: state.installedPackIds, firstDoc: state.firstDoc }}
              onChange={(s) =>
                dispatch({ type: "SET_BUILD_KNOWLEDGE", installedPackIds: s.installedPackIds, firstDoc: s.firstDoc })
              }
            />
          )}

          {/* Step 7: Try It Out */}
          {!showResumePrompt && state.step === 7 && (
            <FirstDocumentStep
              state={state.firstDoc}
              onChange={(s) => dispatch({ type: "SET_FIRST_DOC", state: s })}
            />
          )}

          {/* Step 8: Mode Selection */}
          {!showResumePrompt && state.step === 8 && (
            <ModeSelectionStep
              selectedMode={state.selectedMode}
              onSelectMode={(mode) => dispatch({ type: "SET_MODE", mode })}
              configSummary={{
                providerCount,
                providerNames,
                domainCount,
                ollamaEnabled: state.ollama.enabled,
                ollamaModel: modeSummaryChatModel,
                documentCount: modeSummaryDocCount,
                inferenceBackend: state.selectedBackend,
              }}
              hardware={state.systemCheck ? {
                ram_gb: state.systemCheck.ram_gb,
                cpu: state.systemCheck.cpu,
                gpu: state.systemCheck.gpu,
                gpu_acceleration: state.systemCheck.gpu_acceleration,
              } : null}
            />
          )}
            </div>
          )}
        </div>

        {/* Footer — single standardized rhythm: Back? + Skip? + (Next|Action)
            Each step declares its primary action via STEP_ACTIONS below. This
            replaces the previous 5+ unique footer patterns that forced users
            to relearn the affordance at each transition (F-04 footer rhythm). */}
        {!showResumePrompt && (() => {
          // Per-step primary action: label + onClick + disabled predicate.
          // The Back button (always-present except on Welcome) and Skip button
          // (gated by SKIPPABLE_STEPS) are rendered uniformly.
          const action = (() => {
            switch (state.step) {
              case 0:
                return { label: "Get Started", onClick: goNext, disabled: false, primary: false }
              case 1:
                return { label: "Next", onClick: goNext, disabled: !canProceedFromKeys, primary: false }
              case 2:
              case 3:
                return { label: "Next", onClick: goNext, disabled: false, primary: false }
              case 4:
                return {
                  label: "Next",
                  onClick: () => dispatch({ type: "SET_STEP", step: 5 }),
                  disabled: !state.applied,
                  primary: false,
                }
              case 5:
                return {
                  label: state.allHealthy ? "Next" : "Continue Anyway",
                  onClick: goNext,
                  disabled: !state.allHealthy && !state.healthTimedOut,
                  primary: false,
                }
              case 6:
                // Build Knowledge — always continuable (skippable step)
                return { label: "Next", onClick: goNext, disabled: false, primary: false }
              case 7:
                return {
                  label: "Next",
                  onClick: goNext,
                  disabled: !state.firstDoc.ingested && !state.firstDoc.skipped,
                  primary: false,
                }
              case 8:
                return {
                  label: "Open Cerid AI",
                  onClick: handleFinish,
                  disabled: false,
                  primary: true,
                }
              default:
                return { label: "Next", onClick: goNext, disabled: false, primary: false }
            }
          })()

          // Back button suppressed on Welcome (no prior step) and on Service
          // Health (the health probe is async — back-stepping the apply phase
          // is a sharp edge we'd rather not expose).
          const showBack = state.step > 0 && state.step !== 5

          return (
          <div className="shrink-0 border-t px-6 pb-5 pt-3 space-y-2">
            <div className="flex items-center justify-end gap-2">
              {showBack && (
                <Button variant="ghost" size="sm" onClick={goBack}>
                  <ChevronLeft className="mr-1 h-3 w-3" />
                  Back
                </Button>
              )}

              {SKIPPABLE_STEPS.has(state.step) && (
                <Button variant="ghost" size="sm" onClick={handleSkip}>
                  <SkipForward className="mr-1 h-3 w-3" />
                  Skip
                </Button>
              )}

              <Button
                size="sm"
                onClick={action.onClick}
                disabled={action.disabled}
                className={action.primary ? "bg-brand text-brand-foreground hover:bg-brand/90" : undefined}
              >
                {action.label}
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            </div>

            <StepIndicator
              steps={STEP_DEFS}
              currentStep={state.step}
              skippedSteps={state.skippedSteps}
            />

            {canSkip && (
              <div className="mt-1 text-center">
                <button
                  type="button"
                  onClick={handleFinish}
                  className="text-label-xs text-muted-foreground/80 hover:text-muted-foreground transition-colors"
                >
                  Skip setup — I&apos;ve already configured Cerid
                </button>
              </div>
            )}
          </div>
          )
        })()}
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


function CapabilitySummary({
  assessment,
  inferenceBackend,
}: {
  assessment: CapabilityAssessment
  inferenceBackend: RecommendedLocalBackend | null
}) {
  // Override the static "Pipeline: Free (Ollama)" string when the user
  // selected Quenchforge on Step 1 — using "Ollama" misrepresents which
  // local backend is actually running pipeline tasks (F-04-05).
  const costLabel = (() => {
    if (assessment.costProfile === "free-pipeline" && inferenceBackend === "quenchforge") {
      return "Pipeline: Free (Quenchforge)"
    }
    if (assessment.costProfile === "free-pipeline" && inferenceBackend === "cloud") {
      // costProfile said `free-pipeline` because the live provider snapshot
      // had ollama_detected true (quenchforge listens on :11434 too), but
      // the user explicitly chose Cloud — respect their choice in the label.
      return COST_PROFILE_LABELS["paid-pipeline"]
    }
    return COST_PROFILE_LABELS[assessment.costProfile]
  })()

  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <p className="mb-2 text-xs font-medium text-muted-foreground">System Capabilities</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        {assessment.capabilities.map((cap) => (
          <div key={cap.label} className="flex items-center gap-1.5">
            <div className={cn("h-1.5 w-1.5 rounded-full shrink-0", CAPABILITY_STATUS_DOT[cap.status])} />
            <span className="text-label-sm text-muted-foreground">{cap.label}</span>
          </div>
        ))}
      </div>
      {assessment.capabilities.some((c) => c.reason && c.status !== "available") && (
        <div className="mt-2 space-y-0.5 border-t pt-1.5">
          {assessment.capabilities
            .filter((c) => c.reason && c.status !== "available")
            .map((c) => (
              <p key={c.label} className="text-label-xs text-muted-foreground">
                {c.label}: {c.reason}
              </p>
            ))}
        </div>
      )}
      <p className="mt-1.5 text-label-xs text-muted-foreground/80">
        {costLabel}
      </p>
    </div>
  )
}
