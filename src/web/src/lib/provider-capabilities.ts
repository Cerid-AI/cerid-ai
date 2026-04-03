// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// ---------------------------------------------------------------------------
// Provider capability assessment — pure functions, zero React dependencies.
// Takes a provider config snapshot and returns warnings + capability statuses.
// ---------------------------------------------------------------------------

export interface ProviderConfig {
  hasOpenRouter: boolean
  hasOpenAI: boolean
  hasAnthropic: boolean
  hasXAI: boolean
  ollamaEnabled: boolean
  ollamaDetected: boolean
}

export type Severity = "error" | "warning" | "info"
export type CapabilityStatus = "available" | "degraded" | "unavailable"

export interface FixAction {
  /** Short label for the action button/link */
  label: string
  /** Navigation target: "settings" | "settings:providers" | "settings:ollama" | "health" | URL */
  target: string
  /** Terminal/shell command the user can run (for manual recovery) */
  command?: string
}

export interface Warning {
  severity: Severity
  message: string
  fix?: FixAction
}

export interface Capability {
  label: string
  status: CapabilityStatus
  reason?: string
  fix?: FixAction
}

export type CostProfile = "free-pipeline" | "paid-pipeline" | "no-cloud" | "full"

export type DegradationTier = "full" | "lite" | "direct" | "cached" | "offline"

export interface CapabilityAssessment {
  warnings: Warning[]
  capabilities: Capability[]
  costProfile: CostProfile
}

// ---------------------------------------------------------------------------
// Runtime assessment — combines static provider config with live health
// ---------------------------------------------------------------------------

export interface RuntimeConfig extends ProviderConfig {
  degradationTier: DegradationTier | null
  canRetrieve: boolean
  canVerify: boolean
  canGenerate: boolean
}

export interface RuntimeAssessment extends CapabilityAssessment {
  degradationTier: DegradationTier | null
}

// ---------------------------------------------------------------------------
// Display constants — shared across wizard and settings
// ---------------------------------------------------------------------------

export const CAPABILITY_STATUS_DOT: Record<CapabilityStatus, string> = {
  available: "bg-green-500",
  degraded: "bg-yellow-500",
  unavailable: "bg-muted-foreground/30",
}

export const COST_PROFILE_LABELS: Record<CostProfile, string> = {
  "free-pipeline": "Pipeline: Free (Ollama)",
  "paid-pipeline": "Pipeline: Paid API calls",
  "no-cloud": "Cloud: Not configured",
  full: "Full cloud + local pipeline",
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hasAnyCloudProvider(c: ProviderConfig): boolean {
  return c.hasOpenRouter || c.hasOpenAI || c.hasAnthropic || c.hasXAI
}

function hasOllama(c: ProviderConfig): boolean {
  return c.ollamaEnabled || c.ollamaDetected
}

function countDirectProviders(c: ProviderConfig): number {
  return [c.hasOpenAI, c.hasAnthropic, c.hasXAI].filter(Boolean).length
}

/** Multiple distinct model families available for cross-verification. */
function canCrossVerify(c: ProviderConfig): boolean {
  // OpenRouter gives access to all families through one key
  if (c.hasOpenRouter) return true
  // Multiple direct providers from different families
  return countDirectProviders(c) >= 2
}

function hasWebSearch(c: ProviderConfig): boolean {
  // Web search verification requires Grok :online models (via OpenRouter or xAI direct)
  return c.hasOpenRouter || c.hasXAI
}

// ---------------------------------------------------------------------------
// Core assessment
// ---------------------------------------------------------------------------

export function assessCapabilities(config: ProviderConfig): CapabilityAssessment {
  const warnings: Warning[] = []
  const capabilities: Capability[] = []
  const anyCloud = hasAnyCloudProvider(config)
  const ollama = hasOllama(config)

  // ---- Edge case: nothing at all ----
  if (!anyCloud && !ollama) {
    const fix: FixAction = { label: "Configure Providers", target: "settings:providers" }
    warnings.push({
      severity: "error",
      message: "No AI providers configured. Add at least one API key or set up Ollama to get started.",
      fix,
    })
    return {
      warnings,
      capabilities: [
        { label: "Chat", status: "unavailable", reason: "No providers configured", fix },
        { label: "KB Retrieval", status: "unavailable", reason: "No providers configured", fix },
        { label: "Verification", status: "unavailable", reason: "No providers configured", fix },
        { label: "Cross-Model Verification", status: "unavailable", reason: "No providers configured", fix },
        { label: "Web Search", status: "unavailable", reason: "No providers configured", fix },
        { label: "Pipeline Tasks", status: "unavailable", reason: "No providers configured", fix },
      ],
      costProfile: "no-cloud",
    }
  }

  // ---- Ollama only (local-only mode) ----
  if (!anyCloud && ollama) {
    const cloudFix: FixAction = { label: "Add Provider", target: "settings:providers" }
    warnings.push({
      severity: "info",
      message:
        "Local-only mode — basic chat and KB search are available via Ollama. " +
        "Adding a cloud provider (OpenRouter recommended) unlocks verification, " +
        "web search, and access to frontier models.",
      fix: cloudFix,
    })
    return {
      warnings,
      capabilities: [
        { label: "Chat", status: "degraded", reason: "Local models only — limited quality", fix: cloudFix },
        { label: "KB Retrieval", status: "available" },
        { label: "Verification", status: "unavailable", reason: "Requires cloud provider", fix: cloudFix },
        { label: "Cross-Model Verification", status: "unavailable", reason: "Requires cloud provider", fix: cloudFix },
        { label: "Web Search", status: "unavailable", reason: "Requires cloud provider", fix: cloudFix },
        { label: "Pipeline Tasks", status: "available" },
      ],
      costProfile: "no-cloud",
    }
  }

  // ---- Cloud providers available ----

  // Chat
  capabilities.push({ label: "Chat", status: "available" })

  // KB Retrieval — always available when any provider exists
  capabilities.push({ label: "KB Retrieval", status: "available" })

  // Verification
  if (config.hasOpenRouter || countDirectProviders(config) >= 1) {
    capabilities.push({ label: "Verification", status: "available" })
  } else {
    capabilities.push({
      label: "Verification",
      status: "unavailable",
      reason: "Requires cloud provider",
      fix: { label: "Add Provider", target: "settings:providers" },
    })
  }

  // Cross-model verification
  if (canCrossVerify(config)) {
    capabilities.push({ label: "Cross-Model Verification", status: "available" })
  } else {
    capabilities.push({
      label: "Cross-Model Verification",
      status: "degraded",
      reason: "Single model family — responses checked but not cross-verified",
      fix: { label: "Add Provider", target: "settings:providers" },
    })
  }

  // Web search verification
  if (hasWebSearch(config)) {
    capabilities.push({ label: "Web Search", status: "available" })
  } else {
    capabilities.push({
      label: "Web Search",
      status: "unavailable",
      reason: "Requires OpenRouter or xAI key",
      fix: { label: "Add Provider", target: "settings:providers" },
    })
  }

  // Pipeline tasks
  capabilities.push({
    label: "Pipeline Tasks",
    status: "available",
    reason: ollama ? undefined : "Using paid API calls",
    fix: ollama ? undefined : { label: "Set Up Ollama", target: "settings:ollama" },
  })

  // ---- Warnings ----

  // Single direct provider without OpenRouter
  if (!config.hasOpenRouter && countDirectProviders(config) === 1) {
    warnings.push({
      severity: "warning",
      message:
        "Single provider configured — verification can't cross-check across model families. " +
        "Adding OpenRouter or another provider improves verification accuracy.",
      fix: { label: "Add Provider", target: "settings:providers" },
    })
    warnings.push({
      severity: "warning",
      message: "Limited model selection. OpenRouter provides access to hundreds of models through one key.",
      fix: { label: "Add OpenRouter", target: "settings:providers" },
    })
  }

  // Multiple direct providers but no OpenRouter
  if (!config.hasOpenRouter && countDirectProviders(config) >= 2) {
    warnings.push({
      severity: "info",
      message: "OpenRouter provides access to a wider model catalog through a single key, if you'd like broader selection.",
      fix: { label: "Add OpenRouter", target: "settings:providers" },
    })
  }

  // No Ollama with cloud providers
  if (!ollama && anyCloud) {
    warnings.push({
      severity: "info",
      message: "Ollama can run pipeline tasks (verification, routing, extraction) locally for free, reducing API costs.",
      fix: { label: "Set Up Ollama", target: "settings:ollama" },
    })
  }

  // Cost profile
  let costProfile: CostProfile
  if (ollama && anyCloud) {
    costProfile = "free-pipeline"
  } else if (anyCloud && !ollama) {
    costProfile = "paid-pipeline"
  } else {
    costProfile = "no-cloud"
  }

  return { warnings, capabilities, costProfile }
}

// ---------------------------------------------------------------------------
// Adapters
// ---------------------------------------------------------------------------

/** Adapt wizard state shape to ProviderConfig. */
export function fromWizardState(
  keys: Record<string, { key: string; valid: boolean }>,
  ollama: { detected: boolean; enabled: boolean },
): ProviderConfig {
  return {
    hasOpenRouter: keys.openrouter?.valid ?? false,
    hasOpenAI: keys.openai?.valid ?? false,
    hasAnthropic: keys.anthropic?.valid ?? false,
    hasXAI: keys.xai?.valid ?? false,
    ollamaEnabled: ollama.enabled,
    ollamaDetected: ollama.detected,
  }
}

/** Adapt server settings + setup status to ProviderConfig. */
export function fromServerSettings(
  configuredProviders: string[],
  ollamaEnabled: boolean,
): ProviderConfig {
  const has = (p: string) => configuredProviders.includes(p)
  return {
    hasOpenRouter: has("openrouter"),
    hasOpenAI: has("openai"),
    hasAnthropic: has("anthropic"),
    hasXAI: has("xai"),
    ollamaEnabled,
    ollamaDetected: ollamaEnabled, // from settings, detected ≈ enabled
  }
}

/** Adapt HealthStatusResponse + setup status into a RuntimeConfig. */
export function fromHealthStatus(
  health: {
    degradation_tier?: string | null
    can_retrieve?: boolean
    can_verify?: boolean
    can_generate?: boolean
  },
  configuredProviders: string[],
  ollamaEnabled: boolean,
): RuntimeConfig {
  const base = fromServerSettings(configuredProviders, ollamaEnabled)
  return {
    ...base,
    degradationTier: (health.degradation_tier as DegradationTier) ?? null,
    canRetrieve: health.can_retrieve ?? true,
    canVerify: health.can_verify ?? true,
    canGenerate: health.can_generate ?? true,
  }
}

// ---------------------------------------------------------------------------
// Runtime assessment — overlays live health onto static provider assessment
// ---------------------------------------------------------------------------

const TIER_WARNINGS: Partial<Record<DegradationTier, Warning>> = {
  lite: {
    severity: "warning",
    message: "Running in lite mode — reranking and graph features are temporarily unavailable.",
    fix: {
      label: "View Health",
      target: "health",
      command: "docker restart ai-companion-chroma",
    },
  },
  direct: {
    severity: "warning",
    message: "Retrieval services down — using AI knowledge only, no KB context available.",
    fix: {
      label: "View Health",
      target: "health",
      command: "docker compose -f stacks/infrastructure/docker-compose.yml --env-file .env up -d",
    },
  },
  cached: {
    severity: "error",
    message: "All AI providers unreachable — only cached responses are available.",
    fix: {
      label: "Check Settings",
      target: "settings:providers",
      command: "curl -s https://api.openrouter.ai/api/v1/auth/key -H 'Authorization: Bearer $OPENROUTER_API_KEY'",
    },
  },
  offline: {
    severity: "error",
    message: "System offline — services are not responding.",
    fix: {
      label: "Restart Services",
      target: "health",
      command: "./scripts/start-cerid.sh",
    },
  },
}

export function assessRuntime(config: RuntimeConfig): RuntimeAssessment {
  // Start with static provider-based assessment
  const base = assessCapabilities(config)
  const capabilities = base.capabilities.map((c) => ({ ...c }))
  const warnings = [...base.warnings]

  // Overlay runtime degradation onto capabilities
  if (!config.canRetrieve) {
    const kb = capabilities.find((c) => c.label === "KB Retrieval")
    if (kb && kb.status !== "unavailable") {
      kb.status = "unavailable"
      kb.reason = "Retrieval services temporarily unavailable"
      kb.fix = {
        label: "View Health",
        target: "health",
        command: "docker restart ai-companion-chroma ai-companion-neo4j",
      }
    }
  }

  if (!config.canVerify) {
    for (const label of ["Verification", "Cross-Model Verification"]) {
      const cap = capabilities.find((c) => c.label === label)
      if (cap && cap.status !== "unavailable") {
        cap.status = "unavailable"
        cap.reason = "Verification services temporarily unavailable"
        cap.fix = {
          label: "Check Settings",
          target: "settings:providers",
          command: "docker logs ai-companion-mcp --tail 20",
        }
      }
    }
  }

  if (!config.canGenerate) {
    const chat = capabilities.find((c) => c.label === "Chat")
    if (chat && chat.status !== "unavailable") {
      chat.status = "unavailable"
      chat.reason = "AI generation temporarily unavailable"
      chat.fix = {
        label: "Check Settings",
        target: "settings:providers",
        command: "curl -s https://api.openrouter.ai/api/v1/auth/key -H 'Authorization: Bearer $OPENROUTER_API_KEY'",
      }
    }
  }

  // Add tier-specific warning
  if (config.degradationTier && config.degradationTier !== "full") {
    const tierWarning = TIER_WARNINGS[config.degradationTier]
    if (tierWarning) {
      // Prepend tier warning so it appears first
      warnings.unshift(tierWarning)
    }
  }

  return {
    warnings,
    capabilities,
    costProfile: base.costProfile,
    degradationTier: config.degradationTier,
  }
}
