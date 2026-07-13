// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockFetchRegistry = vi.fn()
const mockStartPackInstall = vi.fn()
const mockQueryKB = vi.fn()

vi.mock("@/lib/api/knowledge-packs", () => ({
  fetchKnowledgePackRegistry: (...args: unknown[]) => mockFetchRegistry(...args),
}))

// The wizard surfaces install through the async contract helper
// (202 job / already_installed / legacy sync 200) in lib/api/setup.
vi.mock("@/lib/api/setup", () => ({
  startPackInstall: (...args: unknown[]) => mockStartPackInstall(...args),
}))

vi.mock("@/lib/api/kb", () => ({
  queryKB: (...args: unknown[]) => mockQueryKB(...args),
}))

vi.mock("@/lib/log-swallowed", () => ({
  logSwallowedError: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const MOCK_REGISTRY_RESPONSE = {
  schema_version: 1,
  packs_by_domain: {
    finance: [
      {
        id: "irs-publications-curated",
        name: "IRS Publications (Curated)",
        version: "1.0.0",
        description: "Selected IRS publications for tax guidance. Plain-text, CC0.",
        domain: "finance",
        sub_category: "tax",
        tags: ["tax", "irs"],
        license: "CC0-1.0",
        size_bytes: 17418,
        artifact_count: 12,
        download_url: "https://example.com/irs.tar.gz",
        sha256: "abc123",
        provenance: { status: "built" },
      },
      {
        id: "cfpb-ask",
        name: "CFPB Ask CFPB",
        version: "1.0.0",
        description: "Consumer Financial Protection Bureau Q&A content.",
        domain: "finance",
        sub_category: "consumer",
        tags: ["finance", "consumer"],
        license: "CC0-1.0",
        size_bytes: 484091,
        artifact_count: 1200,
        download_url: "https://example.com/cfpb.tar.gz",
        sha256: "def456",
        provenance: { status: "built" },
      },
    ],
    coding: [
      {
        id: "python-stdlib-docs",
        name: "Python Standard Library Documentation",
        version: "1.0.0",
        description: "Authoritative Python stdlib reference.",
        domain: "coding",
        sub_category: "python",
        tags: ["python"],
        license: "PSF-2.0",
        size_bytes: 167128,
        artifact_count: 208,
        download_url: "https://example.com/pystd.tar.gz",
        sha256: "ghi789",
        provenance: { status: "built" },
      },
    ],
    projects: [
      {
        id: "18f-methods-guides",
        name: "18F Methods Guides",
        version: "1.0.0",
        description: "US digital services design-research playbook.",
        domain: "projects",
        sub_category: "design",
        tags: ["design", "ux"],
        license: "CC0-1.0",
        size_bytes: 26576,
        artifact_count: 43,
        download_url: "https://example.com/18f.tar.gz",
        sha256: "jkl012",
        provenance: { status: "built" },
      },
    ],
  },
}

// Legacy synchronous backend: startPackInstall resolves "installed" straight away.
const MOCK_INSTALL_START = { status: "installed", jobId: null }

// ---------------------------------------------------------------------------
// Helper: wrap with a fresh QueryClient per test
// ---------------------------------------------------------------------------

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return {
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
    client,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

import { SamplePackTab } from "@/components/setup/sample-pack-tab"

const onComplete = vi.fn<(packId: string, articleCount: number) => void>()

beforeEach(() => {
  vi.restoreAllMocks()
  onComplete.mockClear()
  mockFetchRegistry.mockResolvedValue(MOCK_REGISTRY_RESPONSE)
  mockStartPackInstall.mockResolvedValue(MOCK_INSTALL_START)
  mockQueryKB.mockResolvedValue({
    results: [{ content: "Python pathlib lets you work with filesystem paths." }],
    total_results: 1,
    confidence: 0.9,
  })
})

describe("SamplePackTab", () => {
  it("shows a loading skeleton while the catalog is fetching", () => {
    mockFetchRegistry.mockReturnValue(new Promise(() => {})) // never resolves
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    // Skeletons are rendered as div elements; confirm loading label
    expect(screen.getByRole("status")).toBeInTheDocument()
  })

  it("renders featured pack cards after catalog loads", async () => {
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => {
      expect(screen.getByText("Python Standard Library Documentation")).toBeInTheDocument()
    })
    expect(screen.getByText("IRS Publications (Curated)")).toBeInTheDocument()
    expect(screen.getByText("18F Methods Guides")).toBeInTheDocument()
    expect(screen.getByText("CFPB Ask CFPB")).toBeInTheDocument()
  })

  it("shows an Install button for each featured pack", async () => {
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /^Install/i }).length).toBeGreaterThanOrEqual(1)
    })
  })

  it("calls startPackInstall when Install is clicked", async () => {
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))

    const installBtn = screen.getByRole("button", {
      name: /Install Python Standard Library Documentation/i,
    })
    fireEvent.click(installBtn)
    await waitFor(() => expect(mockStartPackInstall).toHaveBeenCalledWith("python-stdlib-docs"))
  })

  it("transitions to demo queries panel after successful install", async () => {
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))

    fireEvent.click(
      screen.getByRole("button", { name: /Install Python Standard Library Documentation/i }),
    )

    await waitFor(() => {
      // DemoQueriesPanel header
      expect(screen.getByText(/installed successfully/i)).toBeInTheDocument()
    })
  })

  it("treats an already_installed response as success", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "already_installed", jobId: null })
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))

    fireEvent.click(
      screen.getByRole("button", { name: /Install Python Standard Library Documentation/i }),
    )

    await waitFor(() => {
      expect(screen.getByText(/installed successfully/i)).toBeInTheDocument()
    })
  })

  it("renders an Alert on install error", async () => {
    mockStartPackInstall.mockRejectedValue(new Error("Network error"))
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))

    fireEvent.click(
      screen.getByRole("button", { name: /Install Python Standard Library Documentation/i }),
    )

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
      expect(screen.getByText(/Network error/i)).toBeInTheDocument()
    })
  })

  // ---- Card states from registry install flags (beta triage P0-A #3) ----

  it("renders a disabled 'Installed ✓' card for a pack the registry reports installed", async () => {
    const [irs, cfpb] = MOCK_REGISTRY_RESPONSE.packs_by_domain.finance
    const registry = {
      ...MOCK_REGISTRY_RESPONSE,
      packs_by_domain: {
        ...MOCK_REGISTRY_RESPONSE.packs_by_domain,
        finance: [{ ...irs, installed: true, installing: false }, cfpb],
      },
    }
    mockFetchRegistry.mockResolvedValue(registry)

    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("IRS Publications (Curated)"))

    const installedBtn = screen.getByRole("button", {
      name: /IRS Publications \(Curated\) already installed/i,
    })
    expect(installedBtn).toBeDisabled()
    expect(installedBtn).toHaveTextContent("Installed ✓")
    // No Install affordance remains for that pack
    expect(
      screen.queryByRole("button", { name: /Install IRS Publications \(Curated\)/i }),
    ).not.toBeInTheDocument()
  })

  it("shows an installing state for a pack the registry reports installing", async () => {
    const [python] = MOCK_REGISTRY_RESPONSE.packs_by_domain.coding
    const registry = {
      ...MOCK_REGISTRY_RESPONSE,
      packs_by_domain: {
        ...MOCK_REGISTRY_RESPONSE.packs_by_domain,
        coding: [{ ...python, installed: false, installing: true }],
      },
    }
    mockFetchRegistry.mockResolvedValue(registry)

    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))

    const busyBtn = screen.getByRole("button", {
      name: /Install Python Standard Library Documentation/i,
    })
    expect(busyBtn).toBeDisabled()
    expect(busyBtn).toHaveTextContent(/Installing/i)
  })

  it("treats registry entries without install flags as not installed (older backend)", async () => {
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))
    // All four featured packs stay installable
    expect(screen.getAllByRole("button", { name: /^Install/i }).length).toBe(4)
  })

  it("renders an Alert on catalog fetch error", async () => {
    mockFetchRegistry.mockRejectedValue(new Error("Backend unreachable"))
    renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
    })
  })

  it("is axe-clean after catalog loads", async () => {
    const { container } = renderWithQuery(<SamplePackTab onComplete={onComplete} />)
    await waitFor(() => screen.getByText("Python Standard Library Documentation"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
