// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

const mockGoTo = vi.fn()
vi.mock("@/contexts/navigation-context", async (orig) => ({
  ...(await orig<typeof import("@/contexts/navigation-context")>()),
  useNavigation: () => ({
    activePane: "settings",
    goTo: mockGoTo,
    composeChat: vi.fn(),
    consumeChatSeed: () => null,
    navVersion: 0,
  }),
}))

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
  auto_inject_max: 3,
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

const mockKBStats = {
  total_artifacts: 20,
  total_chunks: 80,
  domains: {
    code: { artifacts: 20, chunks: 80, avg_quality: 0.78, synopsis_candidates: 2 },
  },
}

const mockEmbeddingVersions = {
  domains: {
    code: { total: 80, versions: { "v2": 60, "v1": 20 }, current_version: "v2", mixed: true },
  },
}

function mockApis() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/watched-folders")) return ok({ folders: [], total: 0 })
    if (url.includes("/data-sources")) return ok({ sources: [], total: 0 })
    if (url.includes("/briefs/settings")) return ok({ write_to_vault: false, vault_id: null, vault_folder: "_briefs" })
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    if (url.includes("/admin/kb/stats")) return ok(mockKBStats)
    if (url.includes("/admin/kb/embedding-versions")) return ok(mockEmbeddingVersions)
    if (url.includes("/admin/kb/reembed")) {
      return ok({ status: "enqueued", job_id: "job-123", domain: null, message: "Enqueued re-embed job job-123 for all domains." })
    }
    if (url.includes("/admin/collections/repair")) {
      return ok({
        status: "dry_run", collection_name: "domain_code", domain: "code",
        actual_dim: 384, expected_dim: 768, artifacts_found: 20, rebuilt_documents: 0,
        backup_path: null, dry_run: true,
        message: "Dry run: would back up collection 'domain_code', delete it, recreate with dim=768, and re-ingest 20 artifact(s).",
      })
    }
    return ok({})
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  mockGoTo.mockClear()
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

  it("error: watched-folders failure renders retry link", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/watched-folders")) {
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

  it("WB-12: toggling a brief setting after the settings fetch failed surfaces an error instead of a silent no-op", async () => {
    const propsWithBriefs: SettingsCategoryPageProps = {
      ...defaultProps,
      settings: { ...mockSettings, feature_flags: { enable_briefs: true } },
    }
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/briefs/settings")) return Promise.reject(new Error("network error"))
      return mockApis()(url)
    }))
    const user = userEvent.setup()
    render(<KnowledgeCategory {...propsWithBriefs} />, { wrapper })
    await screen.findByText("Briefs")
    await user.click(await screen.findByRole("switch", { name: /write briefs to vault/i }))
    expect(await screen.findByText(/couldn't be loaded, so this change wasn't saved/i)).toBeInTheDocument()
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

describe("KnowledgeCategory — data sources pointer (P0-C.4)", () => {
  it("no longer fetches /data-sources or renders inline toggles", async () => {
    const fetchMock = mockApis()
    vi.stubGlobal("fetch", fetchMock)
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Data Sources")
    await waitFor(() => {
      expect(screen.queryByText(/Loading/i)).toBeNull()
    })
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/data-sources"))).toBe(false)
  })

  it("renders a pointer to the unified Knowledge Providers section", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Data Sources")
    expect(screen.getByRole("button", { name: /open knowledge providers/i })).toBeInTheDocument()
    expect(screen.getByText(/Extensions/)).toBeInTheDocument()
  })

  it("pointer navigates to Settings → Extensions via useNavigation", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Data Sources")
    await user.click(screen.getByRole("button", { name: /open knowledge providers/i }))
    expect(mockGoTo).toHaveBeenCalledWith("settings", expect.objectContaining({ category: "extensions" }))
  })
})

describe("KnowledgeCategory — KB maintenance (relocated from System, ST12)", () => {
  it("shows KB Maintenance section with total counts", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("KB Maintenance")).toBeInTheDocument()
    expect(await screen.findByText("20")).toBeInTheDocument()
    expect(await screen.findByText("80")).toBeInTheDocument()
  })

  it("Rebuild Indexes button opens confirm dialog", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    const btn = await screen.findByText("Rebuild Indexes")
    await user.click(btn)
    expect(await screen.findByText("Rebuild indexes?")).toBeInTheDocument()
  })

  it("Clear domain button opens type-to-confirm dialog", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("KB Maintenance")
    const clearBtns = await screen.findAllByRole("button", { name: /clear domain/i })
    const clearBtn = clearBtns.find((b) => !b.getAttribute("aria-label"))!
    await user.click(clearBtn)
    expect(await screen.findByText(/Clear domain.*permanently delete/i)).toBeInTheDocument()
  })
})

describe("KnowledgeCategory — embedding diagnostics + repair (RA-38)", () => {
  it("Check embedding versions fetches and renders the per-domain distribution", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("KB Maintenance")
    await user.click(screen.getByRole("button", { name: /check embedding versions/i }))
    expect(await screen.findByText(/80 chunks.*mixed/i)).toBeInTheDocument()
  })

  it("Re-embed corpus opens a confirm dialog and enqueues the job", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("KB Maintenance")
    await user.click(screen.getByRole("button", { name: /^re-embed corpus$/i }))
    expect(await screen.findByText("Enqueue re-embed job?")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /enqueue re-embed$/i }))
    expect(await screen.findByText(/Enqueued re-embed job job-123/i)).toBeInTheDocument()
  })

  it("Repair collection preview runs a dry run and reports the plan without applying it", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<KnowledgeCategory {...defaultProps} />, { wrapper })
    await screen.findByText("KB Maintenance")
    await user.type(screen.getByLabelText(/collection to repair/i), "domain_code")
    await user.click(screen.getByRole("button", { name: /^preview repair$/i }))
    expect(await screen.findByText("Preview collection repair?")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /^preview$/i }))
    expect(await screen.findByText(/Dry run: would back up collection/i)).toBeInTheDocument()
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
