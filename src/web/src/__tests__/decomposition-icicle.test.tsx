// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// DecompositionIcicle — 4-state matrix tests + jest-axe + Cycle 4 behavior.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"
import { DecompositionIcicle } from "@/components/subjects/atlas/decomposition/DecompositionIcicle"
import type { DecompositionPayload } from "@/lib/graph/cycle4-contracts"

// ---------------------------------------------------------------------------
// Mock the API layer
// ---------------------------------------------------------------------------

const mockFetchDecomposition = vi.fn()
const mockFetchCommunityEntities = vi.fn()

vi.mock("@/lib/api/decomposition", () => ({
  fetchDecomposition: (...args: unknown[]) => mockFetchDecomposition(...args),
  fetchCommunityEntities: (...args: unknown[]) => mockFetchCommunityEntities(...args),
}))

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const MINIMAL_PAYLOAD: DecompositionPayload = {
  domains: [
    {
      id: "research",
      label: "Research",
      entity_count: 10,
      unclustered: { count: 0 },
      communities: [
        {
          id: "l1-1",
          mode_domain: "research",
          purity: 0.95,
          size: 5,
          label: "Machine Learning",
          top_hubs: [{ id: "e1", name: "Entity One", degree: 10 }],
          children: [
            {
              id: "l0-1",
              mode_domain: "research",
              purity: 0.98,
              size: 3,
              label: "Deep Learning",
              top_hubs: [{ id: "e1", name: "Entity One", degree: 10 }],
            },
          ],
        },
      ],
    },
  ],
  parent_map: { "l0-1": "l1-1" },
  uncategorized_count: 0,
  no_communities_computed: false,
  computed_at: "2026-06-11T03:00:00Z",
  cached: true,
}

const NO_COMMUNITIES_PAYLOAD: DecompositionPayload = {
  domains: [
    {
      id: "research",
      label: "Research",
      entity_count: 5,
      unclustered: { count: 5 },
      communities: [],
    },
  ],
  parent_map: {},
  uncategorized_count: 0,
  no_communities_computed: true,
  computed_at: null,
  cached: false,
}

const EMPTY_KB_PAYLOAD: DecompositionPayload = {
  domains: [],
  parent_map: {},
  uncategorized_count: 0,
  no_communities_computed: false,
  computed_at: null,
  cached: false,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — loading state", () => {
  it("shows skeleton rows while fetching", () => {
    mockFetchDecomposition.mockImplementation(() => new Promise(() => {}))
    const { container } = render(<DecompositionIcicle />, { wrapper: createWrapper() })
    // Skeleton elements should be visible
    // At least something loading-like is rendered
    expect(container.querySelector("[aria-busy='true']")).toBeTruthy()
  })

  it("loading state is axe-clean", async () => {
    mockFetchDecomposition.mockImplementation(() => new Promise(() => {}))
    const { container } = render(<DecompositionIcicle />, { wrapper: createWrapper() })
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — error state", () => {
  it("shows destructive alert on fetch error", async () => {
    mockFetchDecomposition.mockRejectedValue(new Error("Network failure"))
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Network failure/i)).toBeInTheDocument()
    })
  })

  it("error state is axe-clean", async () => {
    mockFetchDecomposition.mockRejectedValue(new Error("oops"))
    const { container } = render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByText(/oops/i))
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// 3. Empty KB state (no entities)
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — empty KB state", () => {
  it("shows empty state when knowledge base has no entities", async () => {
    mockFetchDecomposition.mockResolvedValue(EMPTY_KB_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText(/Knowledge backbone not derived yet/i)).toBeInTheDocument()
    })
  })

  it("empty KB state is axe-clean", async () => {
    mockFetchDecomposition.mockResolvedValue(EMPTY_KB_PAYLOAD)
    const { container } = render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByText(/Knowledge backbone/i))
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// 4. Success state — full tree
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — success state", () => {
  it("renders domain rows from payload", async () => {
    mockFetchDecomposition.mockResolvedValue(MINIMAL_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("domain-row-research")).toBeInTheDocument()
    })
    expect(screen.getByText(/Research/)).toBeInTheDocument()
  })

  it("success state is axe-clean", async () => {
    mockFetchDecomposition.mockResolvedValue(MINIMAL_PAYLOAD)
    const { container } = render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("domain-row-research"))
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// 5. A3 degradation: no_communities_computed
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — A3 no-communities degradation", () => {
  it("shows honest notice when communities have not been computed", async () => {
    mockFetchDecomposition.mockResolvedValue(NO_COMMUNITIES_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(
        screen.getByText(/Clusters appear after the nightly analysis runs/i),
      ).toBeInTheDocument()
    })
  })

  it("does not show community tier rows when no_communities_computed", async () => {
    mockFetchDecomposition.mockResolvedValue(NO_COMMUNITIES_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("domain-row-research"))
    // Expand the domain
    fireEvent.click(screen.getByTestId("domain-row-research"))
    // Should NOT find L1 community rows
    expect(screen.queryByTestId(/^l1-row-/)).toBeNull()
    // Should find the nightly analysis notice inside the expanded domain
    expect(screen.getAllByText(/Clusters appear after the nightly analysis runs/i)).toHaveLength(2)
  })

  it("degraded state is axe-clean", async () => {
    mockFetchDecomposition.mockResolvedValue(NO_COMMUNITIES_PAYLOAD)
    const { container } = render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByText(/Clusters appear/i))
    expect(await axe(container)).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// 6. Tier drill-down
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — tier drill-down", () => {
  it("expands domain on click to show L1 communities", async () => {
    mockFetchDecomposition.mockResolvedValue(MINIMAL_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("domain-row-research"))

    fireEvent.click(screen.getByTestId("domain-row-research"))
    await waitFor(() => {
      expect(screen.getByTestId("l1-row-l1-1")).toBeInTheDocument()
    })
  })

  it("Esc collapses one tier", async () => {
    mockFetchDecomposition.mockResolvedValue(MINIMAL_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("domain-row-research"))

    // Expand domain
    fireEvent.click(screen.getByTestId("domain-row-research"))
    await waitFor(() => screen.getByTestId("l1-row-l1-1"))

    // Press Esc on the icicle container
    const tree = screen.getByRole("region", { name: "Knowledge decomposition" })
    fireEvent.keyDown(tree, { key: "Escape" })

    // L1 row should be gone
    await waitFor(() => {
      expect(screen.queryByTestId("l1-row-l1-1")).toBeNull()
    })
  })

  it("Shift+Esc collapses to T0", async () => {
    mockFetchDecomposition.mockResolvedValue(MINIMAL_PAYLOAD)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("domain-row-research"))

    // Expand to L1
    fireEvent.click(screen.getByTestId("domain-row-research"))
    await waitFor(() => screen.getByTestId("l1-row-l1-1"))
    fireEvent.click(screen.getByTestId("l1-row-l1-1"))
    await waitFor(() => screen.getByTestId("l0-row-l0-1"))

    // Press Shift+Esc
    const tree = screen.getByRole("region", { name: "Knowledge decomposition" })
    fireEvent.keyDown(tree, { key: "Escape", shiftKey: true })

    // Both L1 and L0 rows should be gone
    await waitFor(() => {
      expect(screen.queryByTestId("l1-row-l1-1")).toBeNull()
      expect(screen.queryByTestId("l0-row-l0-1")).toBeNull()
    })
  })
})

// ---------------------------------------------------------------------------
// 7. A6 fallback label — numeric/garbage labels demoted
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — A6 fallback labels", () => {
  it("demotes numeric labels to the deterministic fallback", async () => {
    const payloadWithNumericLabel: DecompositionPayload = {
      ...MINIMAL_PAYLOAD,
      domains: [
        {
          ...MINIMAL_PAYLOAD.domains[0],
          communities: [
            {
              id: "l1-num",
              mode_domain: "research",
              purity: 0.9,
              size: 3,
              label: "0.7143",
              top_hubs: [{ id: "e1", name: "Alpha Entity", degree: 5 }],
              children: [],
            },
          ],
        },
      ],
    }
    mockFetchDecomposition.mockResolvedValue(payloadWithNumericLabel)
    render(<DecompositionIcicle />, { wrapper: createWrapper() })
    await waitFor(() => screen.getByTestId("domain-row-research"))
    fireEvent.click(screen.getByTestId("domain-row-research"))
    await waitFor(() => {
      // Should NOT show the raw numeric label
      expect(screen.queryByText("0.7143")).toBeNull()
      // Should show the fallback label with entity names
      expect(screen.getByText(/Community of 3/i)).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// 8. onInspect callback
// ---------------------------------------------------------------------------

describe("DecompositionIcicle — onInspect contract", () => {
  it("calls onOpenNeighborhood when neighborhood button clicked on entity", async () => {
    mockFetchDecomposition.mockResolvedValue(MINIMAL_PAYLOAD)
    mockFetchCommunityEntities.mockResolvedValue({
      community_id: "l0-1",
      entities: [{ id: "ent-abc", name: "Test Entity", type: "Person", trust_state: "unknown", path: ["research", "l1-1", "l0-1"] }],
    })
    const onOpenNeighborhood = vi.fn()
    render(
      <DecompositionIcicle onOpenNeighborhood={onOpenNeighborhood} />,
      { wrapper: createWrapper() },
    )
    await waitFor(() => screen.getByTestId("domain-row-research"))
    fireEvent.click(screen.getByTestId("domain-row-research"))
    await waitFor(() => screen.getByTestId("l1-row-l1-1"))
    fireEvent.click(screen.getByTestId("l1-row-l1-1"))
    await waitFor(() => screen.getByTestId("l0-row-l0-1"))
    fireEvent.click(screen.getByTestId("l0-row-l0-1"))
    await waitFor(() => screen.getByText("Test Entity"))

    const neighborhoodBtn = screen.getByLabelText("Open Test Entity neighborhood")
    fireEvent.click(neighborhoodBtn)
    expect(onOpenNeighborhood).toHaveBeenCalledWith("ent-abc")
  })
})
