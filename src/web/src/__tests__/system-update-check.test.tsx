// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the "Check for updates" button in the System settings category.
 *
 * Covers:
 * - Web path: checkForUpdates() mocked → renders result inline
 * - Desktop path: window.cerid.app.checkUpdate mocked → calls bridge
 * - Error path: graceful "Could not check" message
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// Mock all modules that the System category pulls in
vi.mock("@/lib/api", () => ({
  fetchSystemCheck: vi.fn().mockResolvedValue({ os: "Linux", cpu: "x86_64", cpu_cores: 4, ram_gb: 16, gpu: null, gpu_acceleration: null }),
  fetchStorageMetrics: vi.fn().mockResolvedValue({ chromadb: { disk_mb: 10, collections: 1, chunks: 100 }, neo4j: { disk_mb: 5, nodes: 50, relationships: 20 }, redis: { memory_mb: 2, keys: 10, peak_mb: 3 }, bm25: { disk_mb: 1, index_count: 1 }, total_mb: 18, limit_mb: 1000, usage_pct: 1.8, status: "ok" }),
  triggerSyncExport: vi.fn(),
  triggerSyncImport: vi.fn(),
}))

vi.mock("@/lib/api/updates", () => ({
  checkForUpdates: vi.fn(),
}))

vi.mock("@/lib/settings-registry", () => ({
  getDef: (key: string) => ({
    key,
    label: key,
    description: "",
    category: "system",
    group: "server",
    writer: { kind: "none" },
  }),
}))

vi.mock("@/components/settings/settings-primitives", () => ({
  SettingRow: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AdvancedDisclosure: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ConfirmActionButton: ({ children, onConfirm }: { children: React.ReactNode; onConfirm: () => void }) => (
    <button type="button" onClick={onConfirm}>{children}</button>
  ),
  ReadOnlyEnvHint: ({ envVar }: { envVar: string }) => <span>{envVar}</span>,
}))

vi.mock("@/components/settings/connection-section", () => ({
  ConnectionSection: () => <div data-testid="connection-section" />,
}))

vi.mock("@/lib/log-swallowed", () => ({
  logSwallowedError: vi.fn(),
}))

import SystemCategory from "@/components/settings/categories/system"
import { checkForUpdates } from "@/lib/api/updates"
import type { ServerSettings } from "@/lib/types"

const mockSettings = {
  feature_tier: "community",
  feature_flags: {},
  enable_hallucination_check: false,
  enable_feedback_loop: false,
  enable_memory_extraction: false,
  storage_mode: "extract_only",
  machine_id: "test-machine",
  version: "1.0.0",
} as unknown as ServerSettings

const noop = () => Promise.resolve()
const noopPatch = () => Promise.resolve({ ok: true as const })

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  // Ensure window.cerid.app.checkUpdate is absent by default (web path)
  if ((window as unknown as { cerid?: unknown }).cerid) {
    delete (window as unknown as { cerid?: unknown }).cerid
  }
})

afterEach(() => {
  if ((window as unknown as { cerid?: unknown }).cerid) {
    delete (window as unknown as { cerid?: unknown }).cerid
  }
})

describe("UpdateCheckButton — web path", () => {
  it("renders the check button", () => {
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    expect(screen.getByRole("button", { name: /check for updates/i })).toBeInTheDocument()
  })

  it("shows 'Up to date' when no update available", async () => {
    vi.mocked(checkForUpdates).mockResolvedValue({
      running: "1.0.0",
      latest: "1.0.0",
      update_available: false,
      release_url: null,
      error: null,
    })
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    await userEvent.click(screen.getByRole("button", { name: /check for updates/i }))
    await waitFor(() => expect(screen.getByText("Up to date")).toBeInTheDocument())
  })

  it("shows version + release link when update is available", async () => {
    vi.mocked(checkForUpdates).mockResolvedValue({
      running: "1.0.0",
      latest: "2.0.0",
      update_available: true,
      release_url: "https://github.com/Cerid-AI/cerid-ai/releases/tag/v2.0.0",
      error: null,
    })
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    await userEvent.click(screen.getByRole("button", { name: /check for updates/i }))
    await waitFor(() => {
      expect(screen.getByText(/Update available/i)).toBeInTheDocument()
      expect(screen.getByText(/2\.0\.0/)).toBeInTheDocument()
      const link = screen.getByRole("link", { name: /release notes/i })
      expect(link).toHaveAttribute("href", "https://github.com/Cerid-AI/cerid-ai/releases/tag/v2.0.0")
    })
  })

  it("shows error message when check fails", async () => {
    vi.mocked(checkForUpdates).mockRejectedValue(new Error("network error"))
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    await userEvent.click(screen.getByRole("button", { name: /check for updates/i }))
    await waitFor(() => expect(screen.getByText(/Could not check/i)).toBeInTheDocument())
  })

  it("shows error when server returns error field", async () => {
    vi.mocked(checkForUpdates).mockResolvedValue({
      running: "1.0.0",
      latest: null,
      update_available: false,
      release_url: null,
      error: "Could not retrieve release information",
    })
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    await userEvent.click(screen.getByRole("button", { name: /check for updates/i }))
    await waitFor(() => expect(screen.getByText(/Could not check/i)).toBeInTheDocument())
  })
})

describe("UpdateCheckButton — desktop path", () => {
  it("calls window.cerid.app.checkUpdate on desktop and shows up-to-date", async () => {
    const mockCheckUpdate = vi.fn().mockResolvedValue({ success: true })
    ;(window as unknown as { cerid: { app: { checkUpdate: typeof mockCheckUpdate } } }).cerid = {
      app: { checkUpdate: mockCheckUpdate },
    }
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    await userEvent.click(screen.getByRole("button", { name: /check for updates/i }))
    await waitFor(() => {
      expect(mockCheckUpdate).toHaveBeenCalledOnce()
      expect(screen.getByText("Up to date")).toBeInTheDocument()
    })
    // checkForUpdates web API should NOT have been called
    expect(checkForUpdates).not.toHaveBeenCalled()
  })
})

describe("UpdateCheckButton — force bypass", () => {
  it("calls checkForUpdates with force=true on web path", async () => {
    vi.mocked(checkForUpdates).mockResolvedValue({
      running: "1.0.0",
      latest: "1.0.0",
      update_available: false,
      release_url: null,
      error: null,
    })
    render(<SystemCategory settings={mockSettings} patch={noopPatch} onRefresh={noop} />, { wrapper })
    await userEvent.click(screen.getByRole("button", { name: /check for updates/i }))
    await waitFor(() => expect(screen.getByText("Up to date")).toBeInTheDocument())
    expect(checkForUpdates).toHaveBeenCalledWith(true)
  })
})
