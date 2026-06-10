// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import SettingsPane from "@/components/settings/settings-pane"

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

const mockSettings = {
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
  feature_tier: "community",
  feature_flags: {},
  domains: ["code", "finance"],
  taxonomy: {},
  storage_mode: "extract_only",
  sync_backend: "local",
  machine_id: "test-machine",
  version: "0.8.0",
}

const mockKBStats = {
  total_artifacts: 42,
  total_chunks: 150,
  domains: {
    code: { artifacts: 30, chunks: 100, avg_quality: 0.75, synopsis_candidates: 5 },
    finance: { artifacts: 12, chunks: 50, avg_quality: 0.60, synopsis_candidates: 3 },
  },
}

function ok(data: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  })
}

function mockMultiFetch(settingsData: unknown, kbStatsData: unknown) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/admin/kb/stats")) return ok(kbStatsData)
    if (url.includes("/providers/ollama/status")) return ok({ enabled: false, url: "http://localhost:11434", reachable: false, models: [], default_model: "llama3.2:3b", default_model_installed: false })
    if (url.includes("/providers/ollama/recommendations")) return ok({ recommended: [] })
    if (url.includes("/health/status")) return ok({ services: {}, overall: "healthy", data: {} })
    if (url.includes("/data-sources")) return ok([])
    if (url.includes("/admin/watched-folders") || url.includes("/watched-folders")) return ok({ folders: [], total: 0 })
    if (url.includes("/providers/models/updates")) return ok({ updates: [], checked_at: null })
    if (url.includes("/providers/credits")) return ok({ balance: null, limit: null, used: null })
    if (url.includes("/system/storage")) return ok({
      chromadb: { disk_mb: 10, collections: 2, chunks: 150 },
      neo4j: { disk_mb: 5, nodes: 100, relationships: 50 },
      redis: { memory_mb: 2, keys: 50, peak_mb: 3 },
      bm25: { disk_mb: 1, index_count: 2 },
      total_mb: 18, limit_mb: 1000, usage_pct: 1.8, status: "healthy",
    })
    if (url.includes("/system/check")) return ok({ ram_gb: 16, docker_running: true, env_exists: true, env_keys_present: [], ollama_detected: false, ollama_url: null, ollama_models: [], lightweight_recommended: false, archive_path_exists: true, default_archive_path: "/data", os: "macOS", cpu: "Apple M1", cpu_cores: 8, gpu: "Apple M1 GPU", gpu_acceleration: "Metal" })
    if (url.includes("/sync/status")) return ok({ last_export: null, last_import: null, status: "idle" })
    if (url.includes("/plugins")) return ok({ plugins: [], total: 0 })
    if (url.includes("/mcp-servers")) return ok({ servers: [], total: 0, total_tools: 0 })
    if (url.includes("/external-apis")) return ok({ adapters: [], total: 0 })
    if (url.includes("/settings/pro-automations")) return ok({ automations: [] })
    if (url.includes("/billing/capabilities")) return ok({ tier: "community", features: {}, buckets: {} })
    if (url.includes("/briefs/settings")) return ok({ write_to_vault: false, vault_id: null, vault_folder: "_briefs" })
    return ok(settingsData)
  })
}

/** Switch to a sidebar category using userEvent */
async function clickTab(name: string) {
  const user = userEvent.setup()
  const nav = screen.getByRole("navigation", { name: "Settings categories" })
  await user.click(within(nav).getByRole("button", { name: new RegExp(name, "i") }))
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("KB Maintenance Section (system category)", () => {
  it("renders KB Maintenance section heading", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")
    expect(await screen.findByText("KB Maintenance")).toBeInTheDocument()
  })

  it("displays total artifact and chunk counts", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")
    await screen.findByText("KB Maintenance")
    expect(await screen.findByText("42")).toBeInTheDocument()
    expect(await screen.findByText("150")).toBeInTheDocument()
  })

  it("shows per-domain stats", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")
    await screen.findByText("KB Maintenance")
    expect(await screen.findByText("code")).toBeInTheDocument()
    expect(await screen.findByText("finance")).toBeInTheDocument()
    // Artifact / chunk count format "artifacts / chunks"
    expect(await screen.findByText("30 / 100")).toBeInTheDocument()
    expect(await screen.findByText("12 / 50")).toBeInTheDocument()
  })

  it("renders management action buttons", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")
    await screen.findByText("KB Maintenance")
    expect(await screen.findByText("Rebuild Indexes")).toBeInTheDocument()
    expect(screen.getByText("Rescore All")).toBeInTheDocument()
    expect(screen.getByText("Regenerate Summaries")).toBeInTheDocument()
    expect(screen.getByText("Refresh Stats")).toBeInTheDocument()
  })

  it("opens confirm dialog on rebuild click", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    const user = userEvent.setup()
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")

    const rebuildBtn = await screen.findByText("Rebuild Indexes")
    await user.click(rebuildBtn)

    // ConfirmActionButton opens an AlertDialog
    expect(await screen.findByText("Rebuild indexes?")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Rebuild Indexes/i })).toBeInTheDocument()
  })

  it("calls rebuild endpoint after confirming dialog", async () => {
    const fetchMock = mockMultiFetch(mockSettings, mockKBStats)
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")

    const rebuildBtn = await screen.findByText("Rebuild Indexes")
    await user.click(rebuildBtn)

    // Update mock for the POST
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/admin/kb/rebuild-index") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ domains_rebuilt: 5, message: "Rebuilt BM25 indexes for 5 domains" }),
          text: () => Promise.resolve("{}"),
        })
      }
      if (url.includes("/admin/kb/stats")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockKBStats), text: () => Promise.resolve("{}") })
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockSettings), text: () => Promise.resolve(JSON.stringify(mockSettings)) })
    })

    // Click the confirm action in the AlertDialog
    const dialogConfirmBtn = screen.getByRole("button", { name: /Rebuild Indexes/i })
    await user.click(dialogConfirmBtn)

    await waitFor(() => {
      expect(screen.getByText(/Rebuilt BM25 indexes for 5 domains/)).toBeInTheDocument()
    })
  })

  it("shows type-to-confirm dialog for clear domain", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    const user = userEvent.setup()
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")

    await screen.findByText("KB Maintenance")
    // Pick the action button (no aria-label attribute), not the help-popover trigger ("About Clear domain")
    const clearBtns = await screen.findAllByRole("button", { name: /clear domain/i })
    const clearBtn = clearBtns.find((b) => !b.getAttribute("aria-label"))!
    await user.click(clearBtn)

    expect(await screen.findByText("Clear domain — permanently delete all data?")).toBeInTheDocument()
  })

  it("renders server version and machine ID in server info section", async () => {
    vi.stubGlobal("fetch", mockMultiFetch(mockSettings, mockKBStats))
    render(<SettingsPane />, { wrapper })
    await screen.findByRole("navigation", { name: "Settings categories" })
    await clickTab("System")
    expect(await screen.findByText("0.8.0")).toBeInTheDocument()
    expect(await screen.findByText("test-machine")).toBeInTheDocument()
  })
})
