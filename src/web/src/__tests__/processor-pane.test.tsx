// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
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
  useProcessorSettingsMutation: vi.fn(),
}))

import {
  useProcessorStatus,
  useProcessorRecent,
  useProcessorMutations,
  useProcessorSettingsMutation,
} from "@/hooks/use-processor"

const mockUseStatus = useProcessorStatus as ReturnType<typeof vi.fn>
const mockUseRecent = useProcessorRecent as ReturnType<typeof vi.fn>
const mockUseMutations = useProcessorMutations as ReturnType<typeof vi.fn>
const mockUseSettingsMutation = useProcessorSettingsMutation as ReturnType<typeof vi.fn>

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
    mode: "local",
    monthly_spend_usd: 0,
    cap_usd: 5,
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

const nopSettingsMutation = {
  updateMode: vi.fn().mockResolvedValue({ status: "ok", updated: {} }),
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
  mockUseSettingsMutation.mockReturnValue(nopSettingsMutation)

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
      expect(screen.getByText("Failed to load processor status")).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
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
// Mode badge + selector (Task 2.5d)
// ---------------------------------------------------------------------------

describe("ProcessorPane — mode selector", () => {
  it("renders the mode badge for the current mode", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "hybrid" }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(screen.getByTestId("processor-mode-badge").textContent).toMatch(/hybrid/i)
    })
  })

  it("renders the current mode as the Select's value", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "disabled" }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const trigger = await screen.findByTestId("processor-mode-select")
    expect(trigger.textContent).toMatch(/disabled/i)
  })

  it("selecting a different mode fires the settings mutation with the correct payload", async () => {
    const user = userEvent.setup()
    const updateModeFn = vi.fn().mockResolvedValue({ status: "ok", updated: {} })
    mockUseSettingsMutation.mockReturnValue({ updateMode: updateModeFn, isPending: false })
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "local" }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })

    const trigger = await screen.findByTestId("processor-mode-select")
    await user.click(trigger)
    const option = await screen.findByRole("option", { name: /hybrid/i })
    await user.click(option)

    expect(updateModeFn).toHaveBeenCalledWith("hybrid")
  })

  it("disables the Select while the settings mutation is pending", async () => {
    mockUseSettingsMutation.mockReturnValue({ updateMode: vi.fn(), isPending: true })
    mockUseStatus.mockReturnValue({
      data: makeStatus(),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const trigger = await screen.findByTestId("processor-mode-select")
    expect(trigger).toBeDisabled()
  })
})

// ---------------------------------------------------------------------------
// Monthly-spend meter (Task 2.5d)
// ---------------------------------------------------------------------------

describe("ProcessorPane — monthly-spend meter", () => {
  it("renders the spend percentage and dollar figures in hybrid mode", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "hybrid", monthly_spend_usd: 1.2, cap_usd: 5 }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const meter = await screen.findByTestId("processor-spend-meter")
    const bar = within(meter).getByRole("progressbar")
    expect(bar).toHaveAttribute("aria-valuenow", "24")
    expect(screen.getByText("$1.20 / $5.00 this month")).toBeInTheDocument()
  })

  it("colours the fill amber at 80%+ and red at 100%+", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "hybrid", monthly_spend_usd: 4.5, cap_usd: 5 }),
      isLoading: false,
      isError: false,
    })
    const { container, rerender } = render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(() => {
      expect(container.querySelector("[class*='bg-amber-500']")).toBeInTheDocument()
    })

    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "hybrid", monthly_spend_usd: 6, cap_usd: 5 }),
      isLoading: false,
      isError: false,
    })
    rerender(<ProcessorPane />)
    await waitFor(() => {
      expect(container.querySelector("[class*='bg-red-500']")).toBeInTheDocument()
    })
  })

  it("renders the no-cap-set fallback instead of NaN when cap_usd is 0", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "hybrid", monthly_spend_usd: 0.5, cap_usd: 0 }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const note = await screen.findByTestId("processor-spend-note")
    expect(note.textContent).toMatch(/no cap set/i)
    expect(note.textContent).not.toMatch(/NaN/)
    expect(screen.queryByTestId("processor-spend-meter")).not.toBeInTheDocument()
  })

  it("renders a muted note instead of a bar when mode is not hybrid", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "local", monthly_spend_usd: 0, cap_usd: 5 }),
      isLoading: false,
      isError: false,
    })
    render(<ProcessorPane />, { wrapper: createWrapper() })
    const note = await screen.findByTestId("processor-spend-note")
    expect(note.textContent).toMatch(/hybrid mode only/i)
    expect(screen.queryByTestId("processor-spend-meter")).not.toBeInTheDocument()
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

  it("is axe-clean in error state", async () => {
    mockUseStatus.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    const { container } = render(<ProcessorPane />, { wrapper: createWrapper() })
    await waitFor(async () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const results = await axe(container as any)
      expect(results).toHaveNoViolations()
    })
  })

  it("is axe-clean in hybrid mode with the mode select and spend meter visible", async () => {
    mockUseStatus.mockReturnValue({
      data: makeStatus({ mode: "hybrid", monthly_spend_usd: 1.2, cap_usd: 5 }),
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
})
