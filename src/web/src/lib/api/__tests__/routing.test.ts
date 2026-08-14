// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fetchRoutingInfo } from "@/lib/api/routing"

beforeEach(() => vi.clearAllMocks())

describe("fetchRoutingInfo", () => {
  it("returns the parsed tier registry on success", async () => {
    const payload = {
      ollama_available: false,
      ollama_models: [],
      model_registry: {
        free: { "llama-3.3": "openrouter/meta-llama/llama-3.3-70b-instruct" },
        cheap: {
          "gpt-4o-mini": "openrouter/openai/gpt-4o-mini",
          "gemini-flash": "openrouter/google/gemini-3.1-flash-lite",
        },
        capable: { "claude-sonnet": "openrouter/anthropic/claude-sonnet-4.6" },
        research: { "grok-online": "openrouter/x-ai/grok-4.3:online" },
        expert: { "grok-4": "openrouter/x-ai/grok-4.20:online" },
      },
      default_internal_model: "openrouter/meta-llama/llama-3.3-70b-instruct",
      smart_routing_enabled: true,
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload }),
    )
    const result = await fetchRoutingInfo()
    expect(result.model_registry.cheap["gpt-4o-mini"]).toBe("openrouter/openai/gpt-4o-mini")
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/providers/routing")
  })

  it("throws (not an empty fallback) when the response is not ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ detail: "service unavailable" }),
      }),
    )
    await expect(fetchRoutingInfo()).rejects.toThrow()
  })
})
