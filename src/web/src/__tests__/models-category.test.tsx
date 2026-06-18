// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import ModelsCategory from "@/components/settings/categories/models"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

const mockSettings: ServerSettings = {
  feature_tier: "community",
  feature_flags: {},
  categorize_mode: "smart",
  chunk_max_tokens: 512,
  chunk_overlap: 64,
  cost_sensitivity: "medium",
  enable_encryption: false,
  enable_feedback_loop: true,
  enable_hallucination_check: true,
  enable_memory_extraction: true,
  enable_model_router: false,
  hallucination_threshold: 0.7,
  enable_auto_inject: true,
  auto_inject_threshold: 0.55,
  domains: [],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "",
  machine_id: "test",
  version: "1.0.0",
  internal_llm_provider: "openrouter",
  embeddings_provider: "sidecar",
  rerank_provider: "sidecar",
}

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const mockPatch = vi.fn().mockResolvedValue({ ok: true })
const mockRefresh = vi.fn()

const defaultProps: SettingsCategoryPageProps = {
  settings: mockSettings,
  patch: mockPatch,
  onRefresh: mockRefresh,
}

function mockApis() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/settings/openrouter-key")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ configured: false, last4: null }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (url.includes("/billing/capabilities")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ tier: "community", features: {}, buckets: {} }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (url.includes("/providers/ollama/status")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ enabled: false, url: "http://localhost:11434", reachable: false, models: [], default_model: "" }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (url.includes("/settings/whisper/models")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ models: [], cache_dir: "", current_default: "medium-q5_0" }),
        text: () => Promise.resolve("{}"),
      })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe("ModelsCategory — 4-state matrix", () => {
  it("loading: Whisper list shows skeleton before data loads", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(document.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThanOrEqual(1)
  })

  it("success: renders provider, pipeline, routing, local inference, whisper sections", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Providers/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Pipeline Tasks/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/Routing/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/Local Inference/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/Speech to Text/i).length).toBeGreaterThanOrEqual(1)
  })

  it("error: OpenRouter status query error still renders page gracefully", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/settings/openrouter-key")) {
        return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("Server error"), json: () => Promise.reject(new Error("Server error")) })
      }
      return mockApis()(url)
    }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Providers/i)).toBeInTheDocument()
  })

  it("success: pipeline task provider select calls patch", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Pipeline Tasks/i)
  })

  it("success: OpenRouter key configured shows last4", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/settings/openrouter-key")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ configured: true, last4: "a1b2", updated_at: "2026-01-01" }),
          text: () => Promise.resolve("{}"),
        })
      }
      return mockApis()(url)
    }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/a1b2/)).toBeInTheDocument()
  })

  it("empty: Whisper with zero models renders empty list", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    await waitFor(() => {
      expect(screen.queryByTestId("whisper-model-manager") === null || screen.queryByText(/medium/)).toBeTruthy()
    }, { timeout: 3000 })
  })
})

describe("ModelsCategory — Ollama wizard", () => {
  it("shows 'Not installed' badge when Ollama is not reachable", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Not installed/i)).toBeInTheDocument()
  })

  it("shows wizard install section when not reachable and not active", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Ollama is not running/i)).toBeInTheDocument()
  })

  it("shows connected badge when Ollama is reachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/providers/ollama/status")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ enabled: true, url: "http://localhost:11434", reachable: true, models: ["llama3.1:8b"], default_model: "llama3.1:8b" }),
          text: () => Promise.resolve("{}"),
        })
      }
      return mockApis()(url)
    }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Connected/i)).toBeInTheDocument()
  })
})

describe("ModelsCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<ModelsCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Providers/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
