// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { axe } from "jest-axe"

// ---------------------------------------------------------------------------
// Mock hooks — control data without network
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-processor", () => ({
  useProcessorStatus: vi.fn(),
  useProcessorRecent: vi.fn(),
  useProcessorMutations: vi.fn(),
}))

import {
  useProcessorStatus,
  useProcessorRecent,
  useProcessorMutations,
} from "@/hooks/use-processor"

const mockUseStatus = useProcessorStatus as ReturnType<typeof vi.fn>
const mockUseRecent = useProcessorRecent as ReturnType<typeof vi.fn>
const mockUseMutations = useProcessorMutations as ReturnType<typeof vi.fn>

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

import type { ProcessorStatus, JobRecord } from "@/lib/types/processor"

function makeStatus(overrides: Partial<ProcessorStatus> = {}): ProcessorStatus {
  return {
    queue_sizes: { high: 0, medium: 0, low: 0 },
    paused: false,
    jobs_completed_24h: 12,
    cost_usd_7d: 0.42,
    throttled_ticks_1h: 3,
    ...overrides,
  }
}

function makeJob(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: "job-1",
    job_type: "wiki.refresh_entity",
    state: "completed",
    priority: "medium",
    payload: {},
    enqueued_at: new Date(Date.now() - 60_000).toISOString(),
    retry_count: 0,
    started_at: new Date(Date.now() - 30_000).toISOString(),
    completed_at: new Date(Date.now() - 5_000).toISOString(),
    estimated_tokens_in: 1000,
    estimated_tokens_out: 200,
    actual_tokens_in: 980,
    actual_tokens_out: 190,
    requires_llm: true,
    model: "claude-3-haiku",
    error_message: null,
    ...overrides,
  }
}

const nopMutations = {
  pause: vi.fn().mockResolvedValue({ paused: true }),
  resume: vi.fn().mockResolvedValue({ paused: false }),
  isPending: false,
}

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

let ProcessorPane: React.ComponentType

beforeEach(async () => {
  vi.restoreAllMocks()
  // Default: data loaded, not paused, no jobs queued, no recent jobs
  mockUseStatus.mockReturnValue({
    data: makeStatus(),
    isLoading: false,
    isError: false,
  })
  mockUseRecent.mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
  })
  mockUseMutations.mockReturnValue(nopMutations)

  const mod = await import("@/components/processor/processor-pane")
  ProcessorPane = mod.default ?? mod.ProcessorPane
})

// ---------------------------------------------------------------------------
// Activity chip variants
// ---------------------------------------------------------------------------

describe("ProcessorPane — activity chip", () => {
  it("shows idle chip when queue empty and not paused", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ queue_sizes: { high: 0, medium: 0, low: 0 }, paused: false }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("processor-activity-chip").textContent).toMatch(/idle/i)
    })
  })

  it("shows pending chip when queue has jobs", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ queue_sizes: { high: 2, medium: 1, low: 0 }, paused: false }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("processor-activity-chip").textContent).toMatch(/3 jobs pending/i)
    })
  })

  it("shows paused chip when paused", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ queue_sizes: { high: 0, medium: 0, low: 0 }, paused: true }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("processor-activity-chip").textContent).toMatch(/paused/i)
    })
  })

  it("shows skeleton while loading", () => {
    mockUseStatus.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    })
    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// Pause / resume buttons
// ---------------------------------------------------------------------------

describe("ProcessorPane — pause/resume buttons", () => {
  it("shows pause button when running", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ paused: false }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("processor-pause-button")).toBeInTheDocument()
    })
  })

  it("shows resume button when paused", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ paused: true }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("processor-resume-button")).toBeInTheDocument()
    })
  })

  it("pause button calls the pause mutation", async () => {
    const user = userEvent.setup()
    const pauseFn = vi.fn().mockResolvedValue({ paused: true })
    mockUseMutations.mockReturnValue({ ...nopMutations, pause: pauseFn })
    mockUseStatus.mockReturnValue({
      data: makeStatus({ paused: false }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })

    const btn = await screen.findByTestId("processor-pause-button")
    await user.click(btn)
    expect(pauseFn).toHaveBeenCalledOnce()
  })

  it("resume button calls the resume mutation", async () => {
    const user = userEvent.setup()
    const resumeFn = vi.fn().mockResolvedValue({ paused: false })
    mockUseMutations.mockReturnValue({ ...nopMutations, resume: resumeFn })
    mockUseStatus.mockReturnValue({
      data: makeStatus({ paused: true }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })

    const btn = await screen.findByTestId("processor-resume-button")
    await user.click(btn)
    expect(resumeFn).toHaveBeenCalledOnce()
  })

  it("pause button is disabled while mutation is pending", async () => {
    mockUseMutations.mockReturnValue({ ...nopMutations, isPending: true })
    mockUseStatus.mockReturnValue({
      data: makeStatus({ paused: false }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const btn = await screen.findByTestId("processor-pause-button")
    expect(btn).toBeDisabled()
  })
})

// ---------------------------------------------------------------------------
// Recent job list
// ---------------------------------------------------------------------------

describe("ProcessorPane — recent job list", () => {
  it("shows empty state when no recent jobs", async () => {
    mockUseRecent.mockReturnValue({ data: [], isLoading: false, isError: false })
    const user = userEvent.setup()
    render(<ProcessorPane />, { wrapper: createWrapper() })

    // Switch to Recent tab
    const recentTab = await screen.findByRole("tab", { name: /recent/i })
    await user.click(recentTab)

    await waitFor(() => {
      expect(screen.getByText("No recent jobs")).toBeInTheDocument()
    })
  })

  it("renders job rows when data is present", async () => {
    const jobs = [
      makeJob({ id: "job-1", job_type: "wiki.refresh_entity" }),
      makeJob({ id: "job-2", job_type: "community.refresh", state: "failed" }),
    ]
    mockUseRecent.mockReturnValue({ data: jobs, isLoading: false, isError: false })

    const user = userEvent.setup()
    render(<ProcessorPane />, { wrapper: createWrapper() })

    const recentTab = await screen.findByRole("tab", { name: /recent/i })
    await user.click(recentTab)

    await waitFor(() => {
      expect(screen.getByText("wiki.refresh_entity")).toBeInTheDocument()
      expect(screen.getByText("community.refresh")).toBeInTheDocument()
    })
  })

  it("shows loading skeleton in recent tab", async () => {
    mockUseRecent.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const user = userEvent.setup()
    render(<ProcessorPane />, { wrapper: createWrapper() })

    const recentTab = await screen.findByRole("tab", { name: /recent/i })
    await user.click(recentTab)

    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    // After switching tabs in the second render...
    const tabs = container.querySelectorAll("[role='tab']")
    const recentTabEl = Array.from(tabs).find(t => t.textContent?.match(/recent/i))
    if (recentTabEl) await user.click(recentTabEl)
    // Skeleton should be present somewhere in the DOM
    const skeletons = container.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("shows error alert in recent tab on fetch failure", async () => {
    mockUseRecent.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    const user = userEvent.setup()
    render(<ProcessorPane />, { wrapper: createWrapper() })

    const recentTab = await screen.findByRole("tab", { name: /recent/i })
    await user.click(recentTab)

    await waitFor(() => {
      expect(screen.getByText(/Failed to load recent jobs/i)).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Four-state matrix (D.2)
// ---------------------------------------------------------------------------

describe("ProcessorPane — 4-state matrix", () => {
  it("loading: shows skeletons", () => {
    mockUseStatus.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    expect(container.querySelectorAll("[class*=animate-pulse]").length).toBeGreaterThan(0)
  })

  it("idle: shows idle chip with green colouring", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ queue_sizes: { high: 0, medium: 0, low: 0 }, paused: false }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const chip = await screen.findByTestId("processor-activity-chip")
    expect(chip.className).toMatch(/green/)
  })

  it("pending: shows amber colouring when jobs queued", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ queue_sizes: { high: 5, medium: 0, low: 0 } }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const chip = await screen.findByTestId("processor-activity-chip")
    expect(chip.className).toMatch(/amber/)
  })

  it("error: shows destructive alert when status errors", async () => {
    mockUseStatus.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText("Status unavailable")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// Stats triplet
// ---------------------------------------------------------------------------

describe("ProcessorPane — metrics triplet", () => {
  it("renders 24h job count, 7d cost, and throttled ticks", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ jobs_completed_24h: 42, cost_usd_7d: 1.23, throttled_ticks_1h: 7 }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByText("42")).toBeInTheDocument()
      expect(screen.getByText("$1.23")).toBeInTheDocument()
      expect(screen.getByText("7")).toBeInTheDocument()
    })
  })
})

// ---------------------------------------------------------------------------
// axe accessibility
// ---------------------------------------------------------------------------

describe("ProcessorPane — axe-clean", () => {
  it("is axe-clean in idle state", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus(),
      isLoading: false,
      isError: false,
    })
    mockUseRecent.mockReturnValue({ data: [], isLoading: false, isError: false })

    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })

  it("is axe-clean in paused state", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ paused: true }),
      isLoading: false,
      isError: false,
    })
    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })

  it("is axe-clean in loading state", async () => {
    mockUseStatus.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const results = await axe(container as any)
    expect(results).toHaveNoViolations()
  })
})
