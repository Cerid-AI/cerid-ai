// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F373 — GET /providers/ollama/status carries `default_model_installed`, and
 * nothing in the app read it. The pane rendered a green "Connected (2 models)"
 * while the model the pipeline is configured to call was not among the two,
 * which is precisely the state /health reports as model "unset".
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import ModelsCategory from "@/components/settings/categories/models"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

const mockSettings = {
  feature_tier: "community",
  feature_flags: {},
  domains: [],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "",
  machine_id: "test",
  version: "1.0.0",
  internal_llm_provider: "quenchforge",
  internal_llm_model: "llama3.1-8b",
  embeddings_provider: "quenchforge",
  rerank_provider: "quenchforge",
} as unknown as ServerSettings

const defaultProps: SettingsCategoryPageProps = {
  settings: mockSettings,
  patch: vi.fn().mockResolvedValue({ ok: true }),
  onRefresh: vi.fn(),
}

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

function ok(data: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

function stubOllamaStatus(status: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/providers/ollama/status")) return ok(status)
      if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
      return ok({})
    }),
  )
}

// The live payload from the audit baseline.
const REACHABLE_MODEL_MISSING = {
  enabled: true,
  url: "http://localhost:11434",
  reachable: true,
  models: ["nomic-embed-text-v1.5", "qwen2.5-7b-instruct-q4_k_m"],
  default_model: "llama3.2:3b",
  default_model_installed: false,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Models settings — local backend readiness", () => {
  it("does not certify the backend green when the configured model is not served", async () => {
    stubOllamaStatus(REACHABLE_MODEL_MISSING)
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    const badge = await screen.findByTestId("local-backend-status-badge")
    expect(badge).toHaveTextContent(/not installed/i)
    expect(badge.className).not.toMatch(/green/)
  })

  it("names the model that is missing", async () => {
    stubOllamaStatus(REACHABLE_MODEL_MISSING)
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByTestId("local-backend-model-warning")).toHaveTextContent(
      /llama3\.2:3b/,
    )
  })

  it("warns separately when the configured pipeline model is absent from the served list", async () => {
    stubOllamaStatus({
      ...REACHABLE_MODEL_MISSING,
      default_model: "qwen2.5-7b-instruct-q4_k_m",
      default_model_installed: true,
    })
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    // settings.internal_llm_model is llama3.1-8b, which the daemon is not
    // serving — the default-model probe cannot see that.
    expect(await screen.findByTestId("local-backend-model-warning")).toHaveTextContent(
      /llama3\.1-8b/,
    )
  })

  it("stays green when the daemon serves the configured model — the control", async () => {
    stubOllamaStatus({
      enabled: true,
      url: "http://localhost:11434",
      reachable: true,
      models: ["llama3.1-8b", "nomic-embed-text-v1.5"],
      default_model: "llama3.1-8b",
      default_model_installed: true,
    })
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    const badge = await screen.findByTestId("local-backend-status-badge")
    expect(badge).toHaveTextContent(/Connected \(2 models\)/i)
    expect(badge.className).toMatch(/green/)
    expect(screen.queryByTestId("local-backend-model-warning")).not.toBeInTheDocument()
  })
})
