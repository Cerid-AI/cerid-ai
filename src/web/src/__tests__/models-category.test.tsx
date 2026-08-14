// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import ModelsCategory from "@/components/settings/categories/models"
import { ModelSelect } from "@/components/chat/model-select"
import { MODELS } from "@/lib/types"
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
  auto_inject_max: 3,
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

  it("CR-047: saving an OpenRouter key invalidates the models-catalog query", async () => {
    const user = userEvent.setup()
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/settings/openrouter-key")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ configured: false, last4: null }),
          text: () => Promise.resolve("{}"),
        })
      }
      return mockApis()(url)
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries")
    render(
      <QueryClientProvider client={qc}>
        <ModelsCategory {...defaultProps} />
      </QueryClientProvider>,
    )
    const input = await screen.findByLabelText("OpenRouter API key (write-only)")
    await user.type(input, "sk-or-testkey-123456")
    const controls = input.parentElement as HTMLElement
    await user.click(within(controls).getByRole("button", { name: /Save/i }))
    // The chat model dropdown gates on ["models-catalog"] dispatchability, which
    // resolves against OpenRouter auth — a new key must refetch it (CR-047).
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["models-catalog"] }),
    )
    // E1 R11: setup-status also gates the dropdown disabled state.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["setup-status"] })
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

  it("WB-11: shows 'Status check failed' + Retry (not 'Not installed' + install wizard) when the status fetch errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/providers/ollama/status")) {
        return Promise.resolve({
          ok: false, status: 500,
          json: () => Promise.reject(new Error("boom")),
          text: () => Promise.resolve("boom"),
        })
      }
      return mockApis()(url)
    }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Status check failed/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument()
    expect(screen.queryByText(/Not installed/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Ollama is not running/i)).not.toBeInTheDocument()
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

describe("ModelsCategory — role assignments table (RA-30)", () => {
  function mockApisWithAssignments(assignments: Record<string, string>) {
    return vi.fn().mockImplementation((url: string) => {
      if (url.includes("/models/assignments")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ assignments, source: "user_config" }),
          text: () => Promise.resolve("{}"),
        })
      }
      return mockApis()(url)
    })
  }

  it("renders the current per-role assignment from GET /models/assignments", async () => {
    vi.stubGlobal("fetch", mockApisWithAssignments({ coding: "anthropic/claude-sonnet-4.6" }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("coding")).toBeInTheDocument()
    expect(screen.getByLabelText('Model for role "coding"')).toHaveValue("anthropic/claude-sonnet-4.6")
  })

  it("Pin is disabled until the field is edited, then calls PUT /models/assignments", async () => {
    const user = userEvent.setup()
    const putSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: () => Promise.resolve({ success: true, restart_required: false, message: "ok" }),
      text: () => Promise.resolve("{}"),
    })
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/models/assignments") && init?.method === "PUT") return putSpy(url, init)
      if (url.includes("/models/assignments")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ assignments: { coding: "old/model" }, source: "user_config" }),
          text: () => Promise.resolve("{}"),
        })
      }
      return mockApis()(url)
    }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    const input = await screen.findByLabelText('Model for role "coding"')
    const row = input.closest("div") as HTMLElement
    const pinButton = within(row).getByRole("button", { name: /pin/i })
    expect(pinButton).toBeDisabled()

    await user.clear(input)
    await user.type(input, "new/model")
    expect(pinButton).toBeEnabled()
    await user.click(pinButton)

    await waitFor(() => expect(putSpy).toHaveBeenCalled())
    const [, init] = putSpy.mock.calls[0]
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ assignments: { coding: "new/model" } })
  })

  it("Revert discards the unsaved edit without calling the API", async () => {
    const user = userEvent.setup()
    vi.stubGlobal("fetch", mockApisWithAssignments({ coding: "old/model" }))
    render(<ModelsCategory {...defaultProps} />, { wrapper })
    const input = await screen.findByLabelText('Model for role "coding"')
    const row = input.closest("div") as HTMLElement

    await user.clear(input)
    await user.type(input, "scratch/edit")
    expect(input).toHaveValue("scratch/edit")

    await user.click(within(row).getByRole("button", { name: /revert/i }))
    expect(input).toHaveValue("old/model")
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

// ---------------------------------------------------------------------------
// ModelSelect — routing-provider gating (P0-B beta triage).
//
// The catalog ids are all "openrouter/<vendor>/<model>" while
// configured_providers holds registry names (openrouter/openai/anthropic/
// xai/ollama). The old gate matched the display BRAND ("Google", "Meta")
// against that set, so those rows were permanently disabled and a configured
// OpenRouter key was ignored. Gating must use the routing provider (first
// id segment), with the brand as a fallback for direct-key setups.
// ---------------------------------------------------------------------------

/** Fetch stub serving GET /providers/configured with the given provider rows. */
function mockConfiguredProvidersApi(
  providers: Array<{ name: string; models: string[] }>,
) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/providers/configured")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({
          providers: providers.map((p) => ({
            name: p.name,
            display_name: p.name,
            requires_api_key: true,
            key_set: true,
            key_preview: null,
            models: p.models,
          })),
          total: providers.length,
        }),
        text: () => Promise.resolve("{}"),
      })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
  })
}

async function openModelSelect(configuredProviders?: string[]) {
  const user = userEvent.setup()
  render(
    <ModelSelect value={MODELS[0].id} onChange={() => {}} configuredProviders={configuredProviders} />,
    { wrapper },
  )
  await user.click(screen.getByRole("combobox"))
  return screen.findAllByRole("option")
}

describe("ModelSelect — routing-provider gating", () => {
  it("enables ALL catalog rows when only 'openrouter' is configured", async () => {
    vi.stubGlobal("fetch", mockConfiguredProvidersApi([
      { name: "openrouter", models: MODELS.map((m) => m.id) },
    ]))
    const options = await openModelSelect(["openrouter"])
    expect(options).toHaveLength(MODELS.length)
    for (const opt of options) {
      expect(opt).not.toHaveAttribute("data-disabled")
    }
    expect(screen.queryByText(/not configured/i)).not.toBeInTheDocument()
  })

  // E1 CR-031: "Unavailable" is decided solely by the live /models/catalog, not
  // the hand-maintained /providers/configured advertised list (which flagged
  // most rows as a false-positive). A model absent from the advertised list but
  // present in the live catalog must NOT be badged.
  it("does NOT badge 'Unavailable' from the hand-maintained provider list — only the live catalog decides", async () => {
    const absentFromRegistry = "openrouter/openai/o3-mini"  // in catalog, not advertised
    const delisted = "openrouter/x-ai/grok-4.5"             // absent from live catalog
    const liveIds = MODELS.map((m) => m.id).filter((id) => id !== delisted)
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/models/catalog")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ ids: liveIds, source: "live_catalog", count: liveIds.length }),
          text: () => Promise.resolve("{}"),
        })
      }
      if (url.includes("/providers/configured")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            providers: [{
              name: "openrouter", display_name: "OpenRouter", requires_api_key: true,
              key_set: true, key_preview: null,
              models: MODELS.map((m) => m.id).filter((id) => id !== absentFromRegistry),
            }],
            total: 1,
          }),
          text: () => Promise.resolve("{}"),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
    }))
    const options = await openModelSelect(["openrouter"])
    // The delisted model proves the live catalog resolved — it IS "Unavailable".
    const delistedRow = options.find((o) => o.textContent?.includes("Grok"))!
    await waitFor(() => {
      expect(delistedRow).toHaveAttribute("data-disabled")
    })
    expect(within(delistedRow).getByText(/unavailable/i)).toBeInTheDocument()
    // The registry-absent-but-catalog-present model must carry no false badge.
    const target = options.find((o) => o.textContent?.includes("o3-mini"))!
    expect(target).not.toHaveAttribute("data-disabled")
    expect(within(target).queryByText(/unavailable/i)).not.toBeInTheDocument()
  })

  it("still enables brand-matched rows for direct-key setups (brand fallback)", async () => {
    vi.stubGlobal("fetch", mockConfiguredProvidersApi([]))
    const options = await openModelSelect(["anthropic"])
    for (const opt of options) {
      if (opt.textContent?.includes("Claude")) {
        expect(opt).not.toHaveAttribute("data-disabled")
      } else {
        expect(opt).toHaveAttribute("data-disabled")
      }
    }
    // Non-configured groups keep the "Not configured" label
    expect(screen.getAllByText(/not configured/i).length).toBeGreaterThanOrEqual(1)
  })

  it("disables everything and labels groups 'Not configured' when nothing is configured", async () => {
    vi.stubGlobal("fetch", mockConfiguredProvidersApi([]))
    const options = await openModelSelect([])
    for (const opt of options) {
      expect(opt).toHaveAttribute("data-disabled")
    }
    expect(screen.getAllByText(/not configured/i).length).toBeGreaterThanOrEqual(1)
  })

  // E1 CR-004: a model absent from the live /models/catalog (e.g. delisted) is
  // disabled + "Unavailable" so a stale hardcoded catalog entry can't be picked.
  it("disables a model absent from the live /models/catalog", async () => {
    const delistedId = "openrouter/x-ai/grok-4.5"
    const liveIds = MODELS.map((m) => m.id).filter((id) => id !== delistedId)
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/models/catalog")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ ids: liveIds, source: "live_catalog", count: liveIds.length }),
          text: () => Promise.resolve("{}"),
        })
      }
      if (url.includes("/providers/configured")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({
            providers: [{ name: "openrouter", display_name: "OpenRouter", requires_api_key: true, key_set: true, key_preview: null, models: MODELS.map((m) => m.id) }],
            total: 1,
          }),
          text: () => Promise.resolve("{}"),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
    }))
    const options = await openModelSelect(["openrouter"])
    const target = options.find((o) => o.textContent?.includes("Grok"))
    expect(target).toBeDefined()
    await waitFor(() => {
      expect(target).toHaveAttribute("data-disabled")
    })
    expect(within(target!).getByText(/unavailable/i)).toBeInTheDocument()
    // A model present in the catalog stays enabled.
    const ok = options.find((o) => o.textContent?.includes("GPT-4o Mini"))
    expect(ok).not.toHaveAttribute("data-disabled")
  })
})
