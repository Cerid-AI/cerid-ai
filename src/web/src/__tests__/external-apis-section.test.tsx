// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import { ExternalAPIsSection } from "@/components/settings/external-apis-section"
import { ExternalAPIRow } from "@/components/settings/external-api-row"
import type { ExternalAPISummary } from "@/lib/types/external-apis"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api/external-apis", () => ({
  fetchExternalAPIs: vi.fn(),
  fetchExternalAPIHealth: vi.fn(),
  toggleExternalAPI: vi.fn(),
}))

import {
  fetchExternalAPIs,
  fetchExternalAPIHealth,
  toggleExternalAPI,
} from "@/lib/api/external-apis"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeAdapter(overrides: Partial<ExternalAPISummary> = {}): ExternalAPISummary {
  return {
    slug: "wikipedia",
    display_name: "Wikipedia",
    enabled: true,
    requires_key: false,
    key_configured: true,
    ...overrides,
  }
}

const MOCK_ADAPTERS: ExternalAPISummary[] = [
  makeAdapter({ slug: "wikipedia",    display_name: "Wikipedia",     enabled: true,  requires_key: false, key_configured: true }),
  makeAdapter({ slug: "wikidata",     display_name: "Wikidata",      enabled: true,  requires_key: false, key_configured: true }),
  makeAdapter({ slug: "openlibrary",  display_name: "Open Library",  enabled: false, requires_key: false, key_configured: true }),
  makeAdapter({ slug: "stackexchange",display_name: "Stack Exchange",enabled: true,  requires_key: false, key_configured: true }),
  makeAdapter({ slug: "arxiv",        display_name: "arXiv",         enabled: true,  requires_key: false, key_configured: true }),
  makeAdapter({ slug: "github",       display_name: "GitHub",        enabled: false, requires_key: true,  key_configured: false }),
  makeAdapter({ slug: "packages",     display_name: "Packages",      enabled: true,  requires_key: false, key_configured: true }),
  makeAdapter({ slug: "osm",          display_name: "OpenStreetMap", enabled: true,  requires_key: false, key_configured: true }),
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

function renderSection(open = true) {
  const onToggle = vi.fn()
  const { container } = render(
    <ExternalAPIsSection open={open} onToggle={onToggle} />,
    { wrapper },
  )
  return { container, onToggle }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchExternalAPIs).mockResolvedValue(MOCK_ADAPTERS)
  vi.mocked(fetchExternalAPIHealth).mockResolvedValue({ status: "ok" })
  vi.mocked(toggleExternalAPI).mockResolvedValue({ ok: true, enabled: true })
})

describe("ExternalAPIsSection", () => {
  // ------------------------------------------------------------------
  // 4-state matrix
  // ------------------------------------------------------------------

  it("shows loading state initially", () => {
    // fetchExternalAPIs never resolves during this test
    vi.mocked(fetchExternalAPIs).mockReturnValue(new Promise(() => {}))
    renderSection()
    expect(screen.getByRole("status", { name: /loading external apis/i })).toBeInTheDocument()
  })

  it("shows error state when fetch fails", async () => {
    vi.mocked(fetchExternalAPIs).mockRejectedValue(new Error("network error"))
    renderSection()
    // retry: 1 means the hook retries once before settling; allow extra time.
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument()
      expect(screen.getByText(/network error/i)).toBeInTheDocument()
    }, { timeout: 5000 })
  })

  it("shows empty state when adapter list is empty", async () => {
    vi.mocked(fetchExternalAPIs).mockResolvedValue([])
    renderSection()
    await waitFor(() => {
      expect(screen.getByText(/no adapters registered/i)).toBeInTheDocument()
    })
  })

  it("renders 8 mocked adapters in settled state", async () => {
    renderSection()
    await waitFor(() => {
      expect(screen.getByText("Wikipedia")).toBeInTheDocument()
      expect(screen.getByText("Wikidata")).toBeInTheDocument()
      expect(screen.getByText("Open Library")).toBeInTheDocument()
      expect(screen.getByText("Stack Exchange")).toBeInTheDocument()
      expect(screen.getByText("arXiv")).toBeInTheDocument()
      expect(screen.getByText("GitHub")).toBeInTheDocument()
      expect(screen.getByText("Packages")).toBeInTheDocument()
      expect(screen.getByText("OpenStreetMap")).toBeInTheDocument()
    })
  })

  it("does not render adapter list when section is closed", async () => {
    // open=false → card is not rendered
    vi.mocked(fetchExternalAPIs).mockResolvedValue(MOCK_ADAPTERS)
    renderSection(false)
    // adapters should not be in the DOM at all
    await waitFor(() => {
      expect(screen.queryByText("Wikipedia")).not.toBeInTheDocument()
    })
  })

  it("calls onToggle when heading is clicked", async () => {
    const { onToggle } = renderSection()
    // SectionHeading renders a button with aria-expanded
    const heading = screen.getByRole("button", { name: /external apis/i })
    fireEvent.click(heading)
    expect(onToggle).toHaveBeenCalledOnce()
  })

  // ------------------------------------------------------------------
  // Status chip variants
  // ------------------------------------------------------------------

  it("shows 'Enabled' chip for enabled keyless adapters", async () => {
    renderSection()
    await waitFor(() => {
      // Multiple enabled chips — check at least one
      const chips = screen.getAllByText("Enabled")
      expect(chips.length).toBeGreaterThan(0)
    })
  })

  it("shows 'Disabled' chip for disabled adapters without key requirement", async () => {
    renderSection()
    await waitFor(() => {
      expect(screen.getByText("Disabled")).toBeInTheDocument()
    })
  })

  it("shows 'Needs key' chip when requires_key && !key_configured", async () => {
    renderSection()
    await waitFor(() => {
      expect(screen.getByText("Needs key")).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // Toggle interaction
  // ------------------------------------------------------------------

  it("fires toggle mutation when switch is clicked on an enabled adapter", async () => {
    renderSection()
    await waitFor(() => screen.getByText("Wikipedia"))

    const wikiSwitch = screen.getByRole("switch", { name: /disable wikipedia/i })
    fireEvent.click(wikiSwitch)

    await waitFor(() => {
      expect(toggleExternalAPI).toHaveBeenCalledWith("wikipedia", false)
    })
  })

  it("'Needs key' state disables the toggle", async () => {
    renderSection()
    await waitFor(() => screen.getByText("GitHub"))

    // GitHub: requires_key=true, key_configured=false → toggle disabled
    const githubSwitch = screen.getByRole("switch", { name: /enable github/i })
    expect(githubSwitch).toBeDisabled()
  })

  // ------------------------------------------------------------------
  // Health check
  // ------------------------------------------------------------------

  it("health check button shows inline result after click", async () => {
    vi.mocked(fetchExternalAPIHealth).mockResolvedValue({ status: "ok" })
    renderSection()
    await waitFor(() => screen.getByText("Wikipedia"))

    const healthBtn = screen.getAllByRole("button", { name: /check health for wikipedia/i })[0]
    fireEvent.click(healthBtn)

    await waitFor(() => {
      expect(screen.getByText(/healthy/i)).toBeInTheDocument()
    })
  })

  it("health check shows error detail on failure", async () => {
    vi.mocked(fetchExternalAPIHealth).mockResolvedValue({
      status: "error",
      detail: "connection timeout",
    })
    renderSection()
    await waitFor(() => screen.getByText("Wikipedia"))

    const healthBtn = screen.getAllByRole("button", { name: /check health for wikipedia/i })[0]
    fireEvent.click(healthBtn)

    await waitFor(() => {
      expect(screen.getByText(/connection timeout/i)).toBeInTheDocument()
    })
  })

  // ------------------------------------------------------------------
  // Accessibility
  // ------------------------------------------------------------------

  it("is axe-clean in settled state", async () => {
    const { container } = renderSection()
    await waitFor(() => screen.getByText("Wikipedia"))
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// ExternalAPIRow unit tests
// ---------------------------------------------------------------------------

describe("ExternalAPIRow", () => {
  it("renders adapter name and slug", () => {
    const adapter = makeAdapter({ slug: "arxiv", display_name: "arXiv" })
    render(<ExternalAPIRow adapter={adapter} onToggle={vi.fn()} />)
    expect(screen.getByText("arXiv")).toBeInTheDocument()
    expect(screen.getByText("arxiv")).toBeInTheDocument()
  })

  it("shows 'Key set' indicator when requires_key && key_configured", () => {
    const adapter = makeAdapter({ requires_key: true, key_configured: true })
    render(<ExternalAPIRow adapter={adapter} onToggle={vi.fn()} />)
    expect(screen.getByLabelText("API key configured")).toBeInTheDocument()
  })

  it("toggle has aria-label for enabled adapter", () => {
    const adapter = makeAdapter({ enabled: true })
    render(<ExternalAPIRow adapter={adapter} onToggle={vi.fn()} />)
    expect(screen.getByRole("switch", { name: /disable wikipedia/i })).toBeInTheDocument()
  })

  it("toggle has aria-label for disabled adapter", () => {
    const adapter = makeAdapter({ enabled: false })
    render(<ExternalAPIRow adapter={adapter} onToggle={vi.fn()} />)
    expect(screen.getByRole("switch", { name: /enable wikipedia/i })).toBeInTheDocument()
  })

  it("health check button has aria-label", () => {
    const adapter = makeAdapter()
    render(<ExternalAPIRow adapter={adapter} onToggle={vi.fn()} />)
    expect(
      screen.getByRole("button", { name: /check health for wikipedia/i }),
    ).toBeInTheDocument()
  })
})
