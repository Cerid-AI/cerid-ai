// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import {
  assessCapabilities,
  assessRuntime,
  fromWizardState,
  fromServerSettings,
  fromHealthStatus,
  type ProviderConfig,
  type RuntimeConfig,
} from "@/lib/provider-capabilities"

const EMPTY: ProviderConfig = {
  hasOpenRouter: false,
  hasOpenAI: false,
  hasAnthropic: false,
  hasXAI: false,
  ollamaEnabled: false,
  ollamaDetected: false,
}

function withProviders(overrides: Partial<ProviderConfig>): ProviderConfig {
  return { ...EMPTY, ...overrides }
}

function capStatus(label: string, result: ReturnType<typeof assessCapabilities>) {
  return result.capabilities.find((c) => c.label === label)?.status
}

function capReason(label: string, result: ReturnType<typeof assessCapabilities>) {
  return result.capabilities.find((c) => c.label === label)?.reason
}

function capFix(label: string, result: ReturnType<typeof assessCapabilities>) {
  return result.capabilities.find((c) => c.label === label)?.fix
}

describe("assessCapabilities", () => {
  // ---- No providers at all ----

  it("returns error when no providers and no Ollama", () => {
    const result = assessCapabilities(EMPTY)
    expect(result.warnings).toHaveLength(1)
    expect(result.warnings[0].severity).toBe("error")
    expect(result.warnings[0].message).toMatch(/No AI providers configured/)
    expect(result.capabilities.every((c) => c.status === "unavailable")).toBe(true)
    expect(result.costProfile).toBe("no-cloud")
  })

  // ---- Ollama only (local-only mode) ----

  it("returns info for Ollama-only config", () => {
    const result = assessCapabilities(withProviders({ ollamaEnabled: true }))
    expect(result.warnings).toHaveLength(1)
    expect(result.warnings[0].severity).toBe("info")
    expect(result.warnings[0].message).toMatch(/Local-only mode/)
    expect(capStatus("Chat", result)).toBe("degraded")
    expect(capStatus("KB Retrieval", result)).toBe("available")
    expect(capStatus("Verification", result)).toBe("unavailable")
    expect(capStatus("Web Search", result)).toBe("unavailable")
    expect(capStatus("Pipeline Tasks", result)).toBe("available")
    expect(result.costProfile).toBe("no-cloud")
  })

  it("treats ollamaDetected same as ollamaEnabled for local-only", () => {
    const result = assessCapabilities(withProviders({ ollamaDetected: true }))
    expect(result.warnings[0].message).toMatch(/Local-only mode/)
    expect(capStatus("Chat", result)).toBe("degraded")
  })

  // ---- OpenRouter only (happy path) ----

  it("shows all available for OpenRouter only", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true }))
    expect(capStatus("Chat", result)).toBe("available")
    expect(capStatus("KB Retrieval", result)).toBe("available")
    expect(capStatus("Verification", result)).toBe("available")
    expect(capStatus("Cross-Model Verification", result)).toBe("available")
    expect(capStatus("Web Search", result)).toBe("available")
    expect(capStatus("Pipeline Tasks", result)).toBe("available")
    expect(result.costProfile).toBe("paid-pipeline")
  })

  it("shows info about Ollama cost savings when OpenRouter only", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true }))
    const ollamaInfo = result.warnings.find((w) => w.message.includes("Ollama"))
    expect(ollamaInfo).toBeDefined()
    expect(ollamaInfo?.severity).toBe("info")
  })

  // ---- OpenRouter + Ollama (best config) ----

  it("shows no warnings for OpenRouter + Ollama", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true, ollamaEnabled: true }))
    expect(result.warnings).toHaveLength(0)
    expect(result.capabilities.every((c) => c.status === "available")).toBe(true)
    expect(result.costProfile).toBe("free-pipeline")
  })

  // ---- Single direct provider (no OpenRouter) ----

  it("warns about degraded verification for single direct provider", () => {
    const result = assessCapabilities(withProviders({ hasOpenAI: true }))
    expect(capStatus("Chat", result)).toBe("available")
    expect(capStatus("Cross-Model Verification", result)).toBe("degraded")
    expect(capReason("Cross-Model Verification", result)).toMatch(/single model family/i)
    const singleWarn = result.warnings.find((w) => w.message.includes("Single provider"))
    expect(singleWarn).toBeDefined()
    expect(singleWarn?.severity).toBe("warning")
  })

  it("marks web search unavailable for non-OpenRouter, non-xAI provider", () => {
    const result = assessCapabilities(withProviders({ hasAnthropic: true }))
    expect(capStatus("Web Search", result)).toBe("unavailable")
    expect(capReason("Web Search", result)).toMatch(/OpenRouter or xAI/)
  })

  it("marks web search available when xAI configured without OpenRouter", () => {
    const result = assessCapabilities(withProviders({ hasXAI: true }))
    expect(capStatus("Web Search", result)).toBe("available")
  })

  // ---- Multiple direct providers (no OpenRouter) ----

  it("allows cross-verification with multiple direct providers", () => {
    const result = assessCapabilities(withProviders({ hasOpenAI: true, hasAnthropic: true }))
    expect(capStatus("Cross-Model Verification", result)).toBe("available")
  })

  it("shows info about OpenRouter for broader catalog", () => {
    const result = assessCapabilities(withProviders({ hasOpenAI: true, hasAnthropic: true }))
    const info = result.warnings.find((w) => w.message.includes("OpenRouter"))
    expect(info).toBeDefined()
    expect(info?.severity).toBe("info")
  })

  // ---- Cost profiles ----

  it("returns free-pipeline when Ollama + any cloud", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true, ollamaEnabled: true }))
    expect(result.costProfile).toBe("free-pipeline")
  })

  it("returns paid-pipeline when cloud only", () => {
    const result = assessCapabilities(withProviders({ hasOpenAI: true }))
    expect(result.costProfile).toBe("paid-pipeline")
  })

  it("returns no-cloud when Ollama only", () => {
    const result = assessCapabilities(withProviders({ ollamaEnabled: true }))
    expect(result.costProfile).toBe("no-cloud")
  })

  // ---- Pipeline tasks reason ----

  it("shows cost reason for pipeline tasks when no Ollama", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true }))
    expect(capReason("Pipeline Tasks", result)).toBe("Using paid API calls")
  })

  it("no cost reason for pipeline tasks when Ollama active", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true, ollamaEnabled: true }))
    expect(capReason("Pipeline Tasks", result)).toBeUndefined()
  })

  // ---- Fix actions ----

  it("includes fix action on unavailable capabilities when no providers", () => {
    const result = assessCapabilities(EMPTY)
    expect(capFix("Chat", result)?.target).toBe("settings:providers")
    expect(capFix("Verification", result)?.target).toBe("settings:providers")
  })

  it("includes fix action for Ollama-only degraded chat", () => {
    const result = assessCapabilities(withProviders({ ollamaEnabled: true }))
    expect(capFix("Chat", result)?.label).toBe("Add Provider")
    expect(capFix("Verification", result)?.target).toBe("settings:providers")
  })

  it("includes fix action for web search when unavailable", () => {
    const result = assessCapabilities(withProviders({ hasOpenAI: true }))
    expect(capFix("Web Search", result)?.target).toBe("settings:providers")
  })

  it("includes Ollama fix for pipeline tasks when using paid APIs", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true }))
    expect(capFix("Pipeline Tasks", result)?.target).toBe("settings:ollama")
  })

  it("no fix action for pipeline tasks when Ollama active", () => {
    const result = assessCapabilities(withProviders({ hasOpenRouter: true, ollamaEnabled: true }))
    expect(capFix("Pipeline Tasks", result)).toBeUndefined()
  })

  it("includes fix actions on warnings", () => {
    const result = assessCapabilities(withProviders({ hasOpenAI: true }))
    const singleWarn = result.warnings.find((w) => w.message.includes("Single provider"))
    expect(singleWarn?.fix?.target).toBe("settings:providers")
  })
})

describe("fromWizardState", () => {
  it("maps validated keys to provider flags", () => {
    const keys = {
      openrouter: { key: "sk-or-xxx", valid: true },
      openai: { key: "", valid: false },
      anthropic: { key: "sk-ant-xxx", valid: true },
      xai: { key: "", valid: false },
    }
    const ollama = { detected: true, enabled: false }
    const config = fromWizardState(keys, ollama)
    expect(config.hasOpenRouter).toBe(true)
    expect(config.hasOpenAI).toBe(false)
    expect(config.hasAnthropic).toBe(true)
    expect(config.hasXAI).toBe(false)
    expect(config.ollamaDetected).toBe(true)
    expect(config.ollamaEnabled).toBe(false)
  })

  it("handles missing provider keys gracefully", () => {
    const keys = { openrouter: { key: "", valid: false } }
    const ollama = { detected: false, enabled: false }
    const config = fromWizardState(keys, ollama)
    expect(config.hasOpenRouter).toBe(false)
    expect(config.hasOpenAI).toBe(false)
    expect(config.hasAnthropic).toBe(false)
    expect(config.hasXAI).toBe(false)
  })
})

describe("fromServerSettings", () => {
  it("maps configured providers list", () => {
    const config = fromServerSettings(["openrouter", "xai"], true)
    expect(config.hasOpenRouter).toBe(true)
    expect(config.hasOpenAI).toBe(false)
    expect(config.hasXAI).toBe(true)
    expect(config.ollamaEnabled).toBe(true)
  })

  it("handles empty provider list", () => {
    const config = fromServerSettings([], false)
    expect(config.hasOpenRouter).toBe(false)
    expect(config.hasOpenAI).toBe(false)
    expect(config.hasAnthropic).toBe(false)
    expect(config.hasXAI).toBe(false)
    expect(config.ollamaEnabled).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Runtime assessment
// ---------------------------------------------------------------------------

const HEALTHY_RUNTIME: RuntimeConfig = {
  hasOpenRouter: true,
  hasOpenAI: false,
  hasAnthropic: false,
  hasXAI: false,
  ollamaEnabled: true,
  ollamaDetected: true,
  degradationTier: "full",
  canRetrieve: true,
  canVerify: true,
  canGenerate: true,
}

function withRuntime(overrides: Partial<RuntimeConfig>): RuntimeConfig {
  return { ...HEALTHY_RUNTIME, ...overrides }
}

function rtCapStatus(label: string, result: ReturnType<typeof assessRuntime>) {
  return result.capabilities.find((c) => c.label === label)?.status
}

describe("assessRuntime", () => {
  it("returns all available when FULL tier and all caps true", () => {
    const result = assessRuntime(HEALTHY_RUNTIME)
    expect(result.degradationTier).toBe("full")
    expect(result.capabilities.every((c) => c.status === "available")).toBe(true)
    expect(result.warnings).toHaveLength(0)
  })

  it("overrides verification when canVerify is false", () => {
    const result = assessRuntime(withRuntime({ canVerify: false }))
    expect(rtCapStatus("Verification", result)).toBe("unavailable")
    expect(rtCapStatus("Cross-Model Verification", result)).toBe("unavailable")
    expect(rtCapStatus("Chat", result)).toBe("available")
  })

  it("overrides KB Retrieval when canRetrieve is false", () => {
    const result = assessRuntime(withRuntime({ canRetrieve: false }))
    expect(rtCapStatus("KB Retrieval", result)).toBe("unavailable")
    expect(rtCapStatus("Chat", result)).toBe("available")
  })

  it("overrides Chat when canGenerate is false", () => {
    const result = assessRuntime(withRuntime({ canGenerate: false }))
    expect(rtCapStatus("Chat", result)).toBe("unavailable")
    expect(rtCapStatus("KB Retrieval", result)).toBe("available")
  })

  it("adds warning for LITE tier", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "lite" }))
    const tierWarn = result.warnings.find((w) => w.message.includes("lite mode"))
    expect(tierWarn).toBeDefined()
    expect(tierWarn?.severity).toBe("warning")
    expect(result.degradationTier).toBe("lite")
  })

  it("adds warning for DIRECT tier", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "direct" }))
    const tierWarn = result.warnings.find((w) => w.message.includes("Retrieval services down"))
    expect(tierWarn).toBeDefined()
    expect(tierWarn?.severity).toBe("warning")
  })

  it("adds error for CACHED tier", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "cached" }))
    const tierWarn = result.warnings.find((w) => w.message.includes("unreachable"))
    expect(tierWarn).toBeDefined()
    expect(tierWarn?.severity).toBe("error")
  })

  it("adds error for OFFLINE tier", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "offline" }))
    const tierWarn = result.warnings.find((w) => w.message.includes("offline"))
    expect(tierWarn).toBeDefined()
    expect(tierWarn?.severity).toBe("error")
  })

  it("does not add tier warning for FULL", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "full" }))
    expect(result.warnings).toHaveLength(0)
  })

  it("combines runtime overrides with provider warnings", () => {
    const result = assessRuntime(withRuntime({
      hasOpenRouter: false,
      hasOpenAI: true,
      ollamaEnabled: false,
      ollamaDetected: false,
      canVerify: false,
      degradationTier: "lite",
    }))
    // Should have tier warning + provider warnings
    expect(result.warnings.length).toBeGreaterThanOrEqual(2)
    // Verification should be unavailable (runtime override trumps "degraded" from single provider)
    expect(rtCapStatus("Verification", result)).toBe("unavailable")
  })

  // ---- Fix actions in runtime assessment ----

  it("includes docker command in tier warning fix", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "lite" }))
    const tierWarn = result.warnings.find((w) => w.message.includes("lite mode"))
    expect(tierWarn?.fix?.command).toMatch(/docker/)
  })

  it("includes startup script command for offline tier", () => {
    const result = assessRuntime(withRuntime({ degradationTier: "offline" }))
    const tierWarn = result.warnings.find((w) => w.message.includes("offline"))
    expect(tierWarn?.fix?.command).toMatch(/start-cerid/)
  })

  it("includes fix action for runtime-degraded KB retrieval", () => {
    const result = assessRuntime(withRuntime({ canRetrieve: false }))
    const kb = result.capabilities.find((c) => c.label === "KB Retrieval")
    expect(kb?.fix?.command).toMatch(/docker restart/)
    expect(kb?.fix?.target).toBe("health")
  })

  it("includes fix action for runtime-degraded verification", () => {
    const result = assessRuntime(withRuntime({ canVerify: false }))
    const ver = result.capabilities.find((c) => c.label === "Verification")
    expect(ver?.fix?.target).toBe("settings:providers")
  })

  it("includes fix action for runtime-degraded chat", () => {
    const result = assessRuntime(withRuntime({ canGenerate: false }))
    const chat = result.capabilities.find((c) => c.label === "Chat")
    expect(chat?.fix?.target).toBe("settings:providers")
  })
})

describe("fromHealthStatus", () => {
  it("maps health response to RuntimeConfig", () => {
    const config = fromHealthStatus(
      { degradation_tier: "lite", can_retrieve: true, can_verify: false, can_generate: true },
      ["openrouter"],
      true,
    )
    expect(config.degradationTier).toBe("lite")
    expect(config.canRetrieve).toBe(true)
    expect(config.canVerify).toBe(false)
    expect(config.canGenerate).toBe(true)
    expect(config.hasOpenRouter).toBe(true)
    expect(config.ollamaEnabled).toBe(true)
  })

  it("defaults to healthy when health fields are missing", () => {
    const config = fromHealthStatus({}, [], false)
    expect(config.degradationTier).toBeNull()
    expect(config.canRetrieve).toBe(true)
    expect(config.canVerify).toBe(true)
    expect(config.canGenerate).toBe(true)
  })
})
