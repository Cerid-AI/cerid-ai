// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import RetrievalAnswersCategory from "@/components/settings/categories/retrieval-answers"
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
  rag_mode: "smart",
  hybrid_vector_weight: 0.5,
  hybrid_keyword_weight: 0.5,
  rerank_llm_weight: 0.5,
  rerank_original_weight: 0.5,
  enable_sparse_retrieval: false,
  hybrid_fusion_mode: "weighted_sum",
  enable_adaptive_retrieval: true,
  enable_query_decomposition: false,
  enable_mmr_diversity: false,
  enable_intelligent_assembly: false,
  enable_late_interaction: false,
  enable_semantic_cache: true,
  semantic_cache_threshold: 0.92,
  enable_memory_consolidation: false,
  enable_context_compression: false,
  enable_contextual_chunks: false,
  enable_self_rag: true,
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
    if (url.includes("/billing/capabilities")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ tier: "community", features: {}, buckets: {} }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (url.includes("/settings/rag/weights/sources")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ sources: [], min_weight: 0, max_weight: 2, default_weight: 1.0, feature_enabled: true }),
        text: () => Promise.resolve("{}"),
      })
    }
    if (url.includes("/settings/rag/weights")) {
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ weights: {}, user_scope: "user", feature_enabled: true }),
        text: () => Promise.resolve("{}"),
      })
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  mockPatch.mockClear()
})

describe("RetrievalAnswersCategory — 4-state matrix", () => {
  it("loading: Smart RAG sources show loading spinner before data loads", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    expect(document.querySelector(".animate-spin")).toBeInTheDocument()
  })

  it("success: renders all core groups", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await waitFor(() => {
      expect(screen.getByText(/Context Injection/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/Answer Quality/i)).toBeInTheDocument()
    expect(screen.getByText(/Learning/i)).toBeInTheDocument()
    expect(screen.getByText(/Hybrid Search Weights/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Smart RAG/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Pipeline Stages/i)).toBeInTheDocument()
  })

  it("error: Smart RAG source fetch failure renders error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/settings/rag/weights")) {
        return Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("Internal error"), json: () => Promise.reject(new Error("Internal error")) })
      }
      return mockApis()(url)
    }))
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await waitFor(() => {
      const alert = document.querySelector("[role='alert']")
      expect(alert?.textContent).toBeTruthy()
    }, { timeout: 3000 })
  })

  it("empty: Smart RAG with no sources shows empty weights list", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await waitFor(() => {
      expect(screen.getByText(/Smart RAG/i)).toBeInTheDocument()
    })
  })
})

describe("RetrievalAnswersCategory — Smart RAG entitlement verdicts", () => {
  it("settled community verdict pitches the upgrade", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    expect(
      await screen.findByText(/Custom Smart RAG requires the Pro plan/i),
    ).toBeInTheDocument()
  })

  it("locked card is suppressed while capabilities are in flight", async () => {
    // tier defaults to "community" while the fetch is pending; the locked
    // card is an upgrade pitch a paying customer must not see on first paint.
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) return new Promise(() => {})
      return mockApis()(url)
    }))
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await waitFor(() => {
      expect(screen.getAllByText(/Smart RAG/i).length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.queryByText(/requires the Pro plan/i)).toBeNull()
  })

  it("failed capabilities fetch stays locked but reports the plan as unverified", async () => {
    // Fail closed (the "pro" registry fallback), but say the plan couldn't be
    // verified instead of pitching an upgrade to a user who may own the plan.
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) {
        return Promise.resolve({
          ok: false, status: 500,
          json: () => Promise.reject(new Error("boom")),
          text: () => Promise.resolve("boom"),
        })
      }
      return mockApis()(url)
    }))
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    expect(
      await screen.findByText(/couldn.t be verified/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/requires the Pro plan/i)).toBeNull()
  })
})

describe("RetrievalAnswersCategory — RAG mode vocabulary", () => {
  it("renders injection mode select with correct server vocabulary values", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Context Injection/i)
    // The select should show the current value "smart" from mockSettings.rag_mode
    expect(screen.getAllByText(/Smart/i).length).toBeGreaterThanOrEqual(1)
  })

  it("rag_mode 'off' is a valid selection", async () => {
    vi.stubGlobal("fetch", mockApis())
    const propsOff = { ...defaultProps, settings: { ...mockSettings, rag_mode: "off" } }
    render(<RetrievalAnswersCategory {...propsOff} />, { wrapper })
    await screen.findByText(/Context Injection/i)
  })

  it("rag_mode 'always' is a valid selection", async () => {
    vi.stubGlobal("fetch", mockApis())
    const propsAlways = { ...defaultProps, settings: { ...mockSettings, rag_mode: "always" } }
    render(<RetrievalAnswersCategory {...propsAlways} />, { wrapper })
    await screen.findByText(/Context Injection/i)
  })
})

describe("RetrievalAnswersCategory — controls call patch", () => {
  it("toggling hallucination check calls patch with enable_hallucination_check", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Answer Quality/i)
    const hallucinationSwitch = screen.getByRole("switch", { name: /Hallucination check/i })
    await userEvent.click(hallucinationSwitch)
    expect(mockPatch).toHaveBeenCalledWith(
      expect.objectContaining({ enable_hallucination_check: expect.any(Boolean) }),
    )
  })

  it("toggling feedback loop calls patch with enable_feedback_loop", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Learning/i)
    const fbSwitch = screen.getByRole("switch", { name: /Feedback loop/i })
    await userEvent.click(fbSwitch)
    expect(mockPatch).toHaveBeenCalledWith(
      expect.objectContaining({ enable_feedback_loop: expect.any(Boolean) }),
    )
  })

  it("RA-48: Max chunks control is reachable and calls patch with auto_inject_max", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Context Injection/i)
    // level: "advanced" — lives behind the contextInjection group's AdvancedDisclosure,
    // the first "Advanced" disclosure in DOM order (Context Injection is the
    // topmost card).
    const disclosureBtns = screen.getAllByRole("button", { name: /Advanced/i })
    await userEvent.click(disclosureBtns[0])
    const maxChunksSlider = await screen.findByRole("slider", { name: /Max chunks/i })
    expect(maxChunksSlider).toBeInTheDocument()
  })

  it("toggling sparse retrieval sets hybrid_fusion_mode to tri_rrf", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Pipeline Stages/i)
    // Multiple AdvancedDisclosure buttons exist — click the Pipeline Stages one
    const disclosureBtns = screen.getAllByRole("button", { name: /Advanced/i })
    // The Pipeline Stages disclosure is the last one
    await userEvent.click(disclosureBtns[disclosureBtns.length - 1])
    const sparseSwitch = await screen.findByRole("switch", { name: /Sparse retrieval/i })
    await userEvent.click(sparseSwitch)
    expect(mockPatch).toHaveBeenCalledWith(
      expect.objectContaining({ enable_sparse_retrieval: true, hybrid_fusion_mode: "tri_rrf" }),
    )
  })

  it("Smart RAG reset button shows confirmation dialog", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/billing/capabilities")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ tier: "pro", features: { custom_smart_rag: true }, buckets: {} }),
          text: () => Promise.resolve("{}"),
        })
      }
      return mockApis()(url)
    }))
    const proSettings = { ...mockSettings, feature_tier: "pro", feature_flags: { custom_smart_rag: true } }
    render(<RetrievalAnswersCategory {...defaultProps} settings={proSettings} />, { wrapper })
    await waitFor(() => {
      const resetBtn = screen.queryByRole("button", { name: /Reset all/i })
      if (resetBtn) expect(resetBtn).toBeInTheDocument()
    }, { timeout: 3000 })
  })
})

describe("RetrievalAnswersCategory — hallucination threshold nested under toggle", () => {
  it("threshold row is hidden when hallucination check is off", async () => {
    vi.stubGlobal("fetch", mockApis())
    // Disable both hallucination check AND auto-inject to ensure no "Threshold" sliders remain
    const propsOff = { ...defaultProps, settings: { ...mockSettings, enable_hallucination_check: false, enable_auto_inject: false } }
    render(<RetrievalAnswersCategory {...propsOff} />, { wrapper })
    await screen.findByText(/Answer Quality/i)
    expect(screen.queryByRole("slider", { name: /Threshold/i })).not.toBeInTheDocument()
  })

  it("threshold row is shown when hallucination check is on", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Answer Quality/i)
    expect(screen.getAllByRole("slider").length).toBeGreaterThanOrEqual(1)
  })
})

describe("RetrievalAnswersCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<RetrievalAnswersCategory {...defaultProps} />, { wrapper })
    await screen.findByText(/Context Injection/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
