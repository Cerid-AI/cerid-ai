// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import SystemCategory from "@/components/settings/categories/system"
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
  version: "0.9.1",
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

const mockSystemCheck = {
  ram_gb: 32, docker_running: true, env_exists: true, env_keys_present: [],
  ollama_detected: false, ollama_url: null, ollama_models: [],
  lightweight_recommended: false, archive_path_exists: true,
  default_archive_path: "/data", os: "macOS", cpu: "Apple M1",
  cpu_cores: 8, gpu: "Apple M1 GPU", gpu_acceleration: "Metal",
}

const mockStorage = {
  chromadb: { disk_mb: 10, collections: 2, chunks: 150 },
  neo4j: { disk_mb: 5, nodes: 100, relationships: 50 },
  redis: { memory_mb: 2, keys: 50, peak_mb: 3 },
  bm25: { disk_mb: 1, index_count: 2 },
  total_mb: 18, limit_mb: 1000, usage_pct: 1.8, status: "healthy",
}

const mockKBStats = {
  total_artifacts: 20,
  total_chunks: 80,
  domains: {
    code: { artifacts: 20, chunks: 80, avg_quality: 0.78, synopsis_candidates: 2 },
  },
}

function mockApis() {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/setup/system-check")) return ok(mockSystemCheck)
    if (url.includes("/system/storage")) return ok(mockStorage)
    if (url.includes("/sync/status")) return ok({ last_export: null, last_import: null, status: "idle" })
    if (url.includes("/admin/kb/stats")) return ok(mockKBStats)
    return ok({})
  })
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe("SystemCategory — 4-state matrix", () => {
  it("loading: skeleton shown while system check and storage fetch", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})))
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(document.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThanOrEqual(1)
  })

  it("success: Server Info, Platform, Storage sections render", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("Server Info")).toBeInTheDocument()
    expect(screen.getByText("Platform")).toBeInTheDocument()
    expect(screen.getByText("Storage")).toBeInTheDocument()
  })

  it("error: system/check failure shows error text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.includes("/setup/system-check")) {
        return Promise.reject(new Error("System check failed"))
      }
      return mockApis()(url)
    }))
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText(/Platform capabilities unavailable/i)).toBeInTheDocument()
  })

  it("success: storage bar shows ChromaDB segment", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("Storage")).toBeInTheDocument()
    // ChromaDB label appears in the legend; multiple matches acceptable
    expect((await screen.findAllByText("ChromaDB")).length).toBeGreaterThanOrEqual(1)
  })
})

describe("SystemCategory — server info", () => {
  it("displays version from settings", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("0.9.1")).toBeInTheDocument()
  })

  it("displays machine ID from settings", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("test-machine")).toBeInTheDocument()
  })
})

describe("SystemCategory — platform capabilities", () => {
  it("shows CPU and GPU from system check", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Platform")
    // "Apple M1" appears in both CPU and GPU spans; getAllByText handles duplicates
    expect((await screen.findAllByText("Apple M1")).length).toBeGreaterThanOrEqual(1)
  })
})

describe("SystemCategory — sync", () => {
  it("renders Sync section with export button", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("Sync")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Export KB/i })).toBeInTheDocument()
  })
})

describe("SystemCategory — KB maintenance", () => {
  it("shows KB Maintenance section with total counts", async () => {
    vi.stubGlobal("fetch", mockApis())
    render(<SystemCategory {...defaultProps} />, { wrapper })
    expect(await screen.findByText("KB Maintenance")).toBeInTheDocument()
    expect(await screen.findByText("20")).toBeInTheDocument()
    expect(await screen.findByText("80")).toBeInTheDocument()
  })

  it("Rebuild Indexes button opens confirm dialog", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<SystemCategory {...defaultProps} />, { wrapper })
    const btn = await screen.findByText("Rebuild Indexes")
    await user.click(btn)
    expect(await screen.findByText("Rebuild indexes?")).toBeInTheDocument()
  })

  it("Clear domain button opens type-to-confirm dialog", async () => {
    vi.stubGlobal("fetch", mockApis())
    const user = userEvent.setup()
    render(<SystemCategory {...defaultProps} />, { wrapper })
    await screen.findByText("KB Maintenance")
    const clearBtns = await screen.findAllByRole("button", { name: /clear domain/i })
    const clearBtn = clearBtns.find((b) => !b.getAttribute("aria-label"))!
    await user.click(clearBtn)
    expect(await screen.findByText(/Clear domain.*permanently delete/i)).toBeInTheDocument()
  })
})

describe("SystemCategory — accessibility", () => {
  it("is axe-clean", async () => {
    vi.stubGlobal("fetch", mockApis())
    const { container } = render(<SystemCategory {...defaultProps} />, { wrapper })
    await screen.findByText("Server Info")
    expect(await axe(container)).toHaveNoViolations()
  })
})
