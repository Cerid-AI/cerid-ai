// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-briefs", () => ({
  useBriefs: vi.fn(),
}))

const composeChatMock = vi.fn()
const goToMock = vi.fn()
vi.mock("@/contexts/navigation-context", () => ({
  useNavigation: () => ({
    goTo: goToMock,
    composeChat: composeChatMock,
    activePane: "briefs",
    navVersion: 0,
  }),
}))

import { useBriefs } from "@/hooks/use-briefs"
import type { Brief, BriefKind } from "@/lib/types/brief"

const mockUseBriefs = useBriefs as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeBrief(overrides: Partial<Brief> = {}): Brief {
  return {
    id: "brief-1",
    kind: "daily",
    generated_at: new Date("2026-07-05T06:00:00Z").toISOString(),
    sections: [{ title: "CONNECTIONS", body: "Alpha met with Beta this week." }],
    claims: [
      { text: "Alpha shipped v2", band: "verified", source_ids: ["artifact-1"] },
      { text: "Beta raised funding", band: "unverified", source_ids: [] },
    ],
    ...overrides,
  }
}

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

let BriefsPane: React.ComponentType

beforeEach(async () => {
  vi.clearAllMocks()
  mockUseBriefs.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() })
  const mod = await import("@/components/briefs/briefs-pane")
  BriefsPane = mod.default
})

// ---------------------------------------------------------------------------
// 4-state matrix (D.2)
// ---------------------------------------------------------------------------

describe("BriefsPane — 4-state matrix", () => {
  it("loading: shows skeletons", () => {
    mockUseBriefs.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() })
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })
    expect(container.querySelectorAll("[class*=animate-pulse]").length).toBeGreaterThan(0)
  })

  it("error: shows PaneError with a working retry", async () => {
    const refetchFn = vi.fn()
    mockUseBriefs.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: refetchFn })
    const user = userEvent.setup()
    render(<BriefsPane />, { wrapper: createWrapper() })

    expect(await screen.findByText("Failed to load briefs")).toBeInTheDocument()
    const retryBtn = screen.getByRole("button", { name: /retry/i })
    await user.click(retryBtn)
    expect(refetchFn).toHaveBeenCalledOnce()
  })

  it("empty: shows the first-daily-brief empty state copy", async () => {
    mockUseBriefs.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() })
    render(<BriefsPane />, { wrapper: createWrapper() })
    expect(await screen.findByText("Your first daily brief arrives at 06:00")).toBeInTheDocument()
  })

  it("success: renders sections and a VerifiedResponse badge per claim after selecting a brief", async () => {
    const brief = makeBrief()
    mockUseBriefs.mockReturnValue({ data: [brief], isLoading: false, isError: false, refetch: vi.fn() })
    const user = userEvent.setup()
    render(<BriefsPane />, { wrapper: createWrapper() })

    const card = await screen.findByRole("button", { name: /open daily brief/i })
    await user.click(card)

    expect(await screen.findByText("CONNECTIONS")).toBeInTheDocument()
    expect(screen.getByText(/Alpha met with Beta this week/)).toBeInTheDocument()

    const claimList = screen.getByRole("list", { name: /claim verification results/i })
    expect(within(claimList).getAllByRole("listitem")).toHaveLength(2)
  })
})

// ---------------------------------------------------------------------------
// Adapter proof — band → badge colour (proves briefClaimToFE / deriveBand wiring)
// ---------------------------------------------------------------------------

describe("BriefsPane — claim band adapter", () => {
  it("a verified-band claim with a source_id renders the green verified badge; an unverified claim renders red", async () => {
    const brief = makeBrief()
    mockUseBriefs.mockReturnValue({ data: [brief], isLoading: false, isError: false, refetch: vi.fn() })
    const user = userEvent.setup()
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })

    const card = await screen.findByRole("button", { name: /open daily brief/i })
    await user.click(card)

    await waitFor(() => {
      expect(container.querySelector('[data-verification-band="verified"]')).toBeInTheDocument()
      expect(container.querySelector('[data-verification-band="unverified"]')).toBeInTheDocument()
    })
  })

  it("a verified-band claim with NO source_id honestly degrades to partial, never fakes a source", async () => {
    const brief = makeBrief({
      claims: [{ text: "No provenance claim", band: "verified", source_ids: [] }],
    })
    mockUseBriefs.mockReturnValue({ data: [brief], isLoading: false, isError: false, refetch: vi.fn() })
    const user = userEvent.setup()
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })

    const card = await screen.findByRole("button", { name: /open daily brief/i })
    await user.click(card)

    await waitFor(() => {
      expect(container.querySelector('[data-verification-band="partial"]')).toBeInTheDocument()
      expect(container.querySelector('[data-verification-band="verified"]')).not.toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Discuss this brief → composeChat
// ---------------------------------------------------------------------------

describe("BriefsPane — discuss this brief", () => {
  it("calls composeChat with the brief context when clicked", async () => {
    const brief = makeBrief()
    mockUseBriefs.mockReturnValue({ data: [brief], isLoading: false, isError: false, refetch: vi.fn() })
    const user = userEvent.setup()
    render(<BriefsPane />, { wrapper: createWrapper() })

    const card = await screen.findByRole("button", { name: /open daily brief/i })
    await user.click(card)

    const discussBtn = await screen.findByRole("button", { name: /discuss this brief/i })
    await user.click(discussBtn)

    expect(composeChatMock).toHaveBeenCalledOnce()
    const arg = composeChatMock.mock.calls[0][0] as { text: string }
    expect(arg.text).toMatch(/daily brief/i)
  })
})

// ---------------------------------------------------------------------------
// Kind tabs — Daily | Weekly, never Inbox
// ---------------------------------------------------------------------------

describe("BriefsPane — kind tabs", () => {
  it("shows Daily and Weekly tabs and never an Inbox tab", async () => {
    render(<BriefsPane />, { wrapper: createWrapper() })
    expect(await screen.findByRole("tab", { name: /daily/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /weekly/i })).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: /inbox/i })).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Selection persists across tab switches (per-kind, since TabsContent unmounts)
// ---------------------------------------------------------------------------

describe("BriefsPane — selection persists across tab switches", () => {
  it("keeps each kind's own drill-down selection when switching tabs", async () => {
    const dailyBrief = makeBrief({ id: "daily-1", kind: "daily" })
    const weeklyBrief = makeBrief({
      id: "weekly-1",
      kind: "weekly",
      sections: [{ title: "SUMMARY", body: "Weekly roundup body." }],
    })

    mockUseBriefs.mockImplementation((kind: BriefKind) =>
      kind === "daily"
        ? { data: [dailyBrief], isLoading: false, isError: false, refetch: vi.fn() }
        : { data: [weeklyBrief], isLoading: false, isError: false, refetch: vi.fn() },
    )

    const user = userEvent.setup()
    render(<BriefsPane />, { wrapper: createWrapper() })

    // Drill into the daily brief.
    const dailyCard = await screen.findByRole("button", { name: /open daily brief/i })
    await user.click(dailyCard)
    expect(await screen.findByRole("button", { name: /back to briefs/i })).toBeInTheDocument()

    // Switch to Weekly and drill into the weekly brief too.
    await user.click(screen.getByRole("tab", { name: /weekly/i }))
    const weeklyCard = await screen.findByRole("button", { name: /open weekly brief/i })
    await user.click(weeklyCard)
    expect(await screen.findByRole("button", { name: /back to briefs/i })).toBeInTheDocument()

    // Switch back to Daily — its selection must have survived the unmount/remount.
    await user.click(screen.getByRole("tab", { name: /daily/i }))
    expect(await screen.findByRole("button", { name: /back to briefs/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /open daily brief/i })).not.toBeInTheDocument()

    // And Weekly's own selection must have independently survived too.
    await user.click(screen.getByRole("tab", { name: /weekly/i }))
    expect(await screen.findByRole("button", { name: /back to briefs/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /open weekly brief/i })).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// List preview strips markdown syntax
// ---------------------------------------------------------------------------

describe("BriefsPane — list preview strips markdown", () => {
  it("does not render literal markdown syntax in the card preview", async () => {
    const brief = makeBrief({
      sections: [{ title: "CONNECTIONS", body: "**Alpha** shipped `v2` and it was _great_." }],
    })
    mockUseBriefs.mockReturnValue({ data: [brief], isLoading: false, isError: false, refetch: vi.fn() })
    render(<BriefsPane />, { wrapper: createWrapper() })

    const card = await screen.findByRole("button", { name: /open daily brief/i })
    expect(card.textContent).not.toContain("**")
  })
})

// ---------------------------------------------------------------------------
// axe-clean
// ---------------------------------------------------------------------------

describe("BriefsPane — axe-clean", () => {
  it("is axe-clean in loading state", async () => {
    mockUseBriefs.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() })
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const results = await axe(container as any)
    expect(results).toHaveNoViolations()
  })

  it("is axe-clean in error state", async () => {
    mockUseBriefs.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() })
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })

  it("is axe-clean in empty state", async () => {
    mockUseBriefs.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() })
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })

  it("is axe-clean in success state (list + detail)", async () => {
    const brief = makeBrief()
    mockUseBriefs.mockReturnValue({ data: [brief], isLoading: false, isError: false, refetch: vi.fn() })
    const user = userEvent.setup()
    const { container } = render(<BriefsPane />, { wrapper: createWrapper() })

    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })

    const card = await screen.findByRole("button", { name: /open daily brief/i })
    await user.click(card)

    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })
})
