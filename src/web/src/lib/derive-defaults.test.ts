// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { deriveDefaultModel, pickCheapTierDefault } from "@/lib/derive-defaults"
import type { ModelRegistry } from "@/lib/api/routing"

const REGISTRY: ModelRegistry = {
  free: { "llama-3.3": "openrouter/meta-llama/llama-3.3-70b-instruct" },
  // Cheapest-first; the first slot is the default chat model.
  cheap: {
    "gpt-4o-mini": "openrouter/openai/gpt-4o-mini",
    "gemini-flash": "openrouter/google/gemini-3.1-flash-lite",
  },
  capable: { "claude-sonnet": "openrouter/anthropic/claude-sonnet-4.6" },
  research: { "grok-online": "openrouter/x-ai/grok-4.3:online" },
  expert: { "grok-4": "openrouter/x-ai/grok-4.20:online" },
}

describe("deriveDefaultModel — registry resolution", () => {
  it("picks the cheap tier's first (cheapest) slot when OpenRouter is configured", () => {
    const providers = [{ id: "openrouter", configured: true }]
    expect(deriveDefaultModel(providers, REGISTRY)).toBe("openrouter/openai/gpt-4o-mini")
  })

  it("tracks a catalog-refreshed cheap id rather than a pinned literal", () => {
    // Simulate the weekly overlay swapping the resolved cheap id.
    const refreshed: ModelRegistry = {
      ...REGISTRY,
      cheap: { "gpt-4o-mini": "openrouter/openai/gpt-5-mini" },
    }
    const providers = [{ id: "openrouter", configured: true }]
    expect(deriveDefaultModel(providers, refreshed)).toBe("openrouter/openai/gpt-5-mini")
  })

  it("falls back to the static PROVIDER_DEFAULTS table when the registry is absent", () => {
    // Endpoint-unavailable path: routing fetch failed, registry is undefined.
    const providers = [{ id: "openrouter", configured: true }]
    expect(deriveDefaultModel(providers)).toBe("openrouter/openai/gpt-4o-mini")
  })

  it("uses the static table for a non-OpenRouter provider even when a registry is present", () => {
    // The registry's ids are all openrouter/-prefixed; a direct-key OpenAI
    // setup must not be handed an openrouter/... default.
    const providers = [{ id: "openai", configured: true }]
    expect(deriveDefaultModel(providers, REGISTRY)).toBe("openai/gpt-4o-mini")
  })

  it("returns null when nothing is configured (caller renders an add-provider hint)", () => {
    expect(deriveDefaultModel([], REGISTRY)).toBeNull()
  })
})

describe("pickCheapTierDefault", () => {
  it("returns the first cheap-tier id", () => {
    expect(pickCheapTierDefault(REGISTRY)).toBe("openrouter/openai/gpt-4o-mini")
  })

  it("returns null for a missing/empty registry", () => {
    expect(pickCheapTierDefault(null)).toBeNull()
    expect(pickCheapTierDefault(undefined)).toBeNull()
    expect(pickCheapTierDefault({ ...REGISTRY, cheap: {} })).toBeNull()
  })
})
