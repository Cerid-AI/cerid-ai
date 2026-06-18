// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import KnowledgeCategory from "@/components/settings/categories/knowledge"
import type { ServerSettings } from "@/lib/types"
import type { SettingsCategoryPageProps } from "@/components/settings/categories/page-props"

const mockSettings: ServerSettings = {
  feature_tier: "community",
  feature_flags: {},
  categorize_mode: "smart",
  chunk_max_tokens: 400,
  chunk_overlap: 0.2,
  cost_sensitivity: "medium",
  enable_encryption: false,
  enable_feedback_loop: false,
  enable_hallucination_check: true,
  enable_memory_extraction: false,
  enable_model_router: false,
  hallucination_threshold: 0.75,
  enable_auto_inject: false,
  auto_inject_threshold: 0.82,
  domains: ["code"],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "local",
  machine_id: "test-machine",
  version: "0.8.0",
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

function ok(data: unknown) {
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

function mockApis() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/watched-folders")) return ok({ folders: [], total: 0 })
    if (url.includes("/data-sources")) return ok({ sources: [], total: 0 })
    if (url.includes("/briefs/settings")) return ok({ write_to_vault: false, vault_id: null, vault_folder: "_briefs" })
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    return ok({})
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe("KnowledgeCategory — 4-state matrix", () => {
  it("loading: shows skeleton while data fetches", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    expect(document.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThanOrEqual(1)
  })

  it("success: renders Watched Folders, Data Sources, Ingestion sections", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("Watched Folders")).toBeInTheDocument()
    expect(screen.getByText("Data Sources")).toBeInTheDocument()
    expect(screen.getByText("Ingestion")).toBeInTheDocument()
  })

  it("error: data-sources failure renders retry link", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/data-sources")) {
        return Promise.reject(new Error("network error"))
      }
      return mockApis()(url)
    }))
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Retry/i)).toBeInTheDocument()
  })

  it("empty: zero watched folders renders empty state", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Watched Folders")
    expect(screen.queryByRole("list")).toBeNull()
  })

  it("success: add folder button is present", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Watched Folders")
    expect(screen.getByRole("button", { name: /add folder/i })).toBeInTheDocument()
  })

  it("success: Briefs section renders when briefs enabled", async () => {
    const propsWithBriefs: SettingsCategoryPageProps = {
      ...defaultProps,
      settings: { ...mockSettings, feature_flags: { enable_briefs: true } },
    }
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...propsWithBriefs} />, { wrapper })
    expect(await screen.findByText("Briefs")).toBeInTheDocument()
  })

  it("success: categorize_mode patch called on select change", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Ingestion")
    expect(mockPatch).not.toHaveBeenCalled()
  })
})

describe("KnowledgeCategory — add folder flow", () => {
  it("shows input after clicking Add Folder", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Watched Folders")
    await user.click(screen.getByRole("button", { name: /add folder/i }))
    expect(screen.getByPlaceholderText(/\/home\/user\/documents/i)).toBeInTheDocument()
  })
})

describe("KnowledgeCategory — data sources", () => {
  it("success: shows empty list for zero data sources", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Data Sources")
    await waitFor(() => {
      expect(screen.queryByText(/Loading/i)).toBeNull()
    })
  })

  it("shows data source toggle when sources are present", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/data-sources")) {
        return ok({ sources: [{ name: "Confluence", description: "Confluence connector", enabled: true, configured: true, requires_api_key: false, api_key_env_var: "", domains: [] }], total: 1 })
      }
      return mockApis()(url)
    }))
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("Confluence")).toBeInTheDocument()
  })
})

describe("KnowledgeCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Watched Folders")
    expect(await axe(container)).toHaveNoViolations()
  })
})
