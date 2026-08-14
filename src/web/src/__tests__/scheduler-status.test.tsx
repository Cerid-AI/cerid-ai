// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Run-now honesty (SF-2): the backend answers "collapsed_into_pending" when
// an equivalent job is already pending/running. The card must not toast
// "Running ..." for a run that never started, and must not bust caches.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api/kb", () => ({
  triggerSchedulerJob: vi.fn(),
}))
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() },
}))

import { triggerSchedulerJob } from "@/lib/api/kb"
import { toast } from "sonner"
import { SchedulerStatus } from "@/components/monitoring/scheduler-status"

const mockTrigger = triggerSchedulerJob as ReturnType<typeof vi.fn>

const scheduler = {
  status: "running" as const,
  jobs: [
    {
      id: "compute_umap_3d",
      name: "Constellation 3D coordinate compute",
      next_run: null,
      trigger: "cron[hour='3']",
    },
  ],
}

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SchedulerStatus scheduler={scheduler} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("SchedulerStatus — run-now duplicate honesty (SF-2)", () => {
  it("toasts success for a genuinely started run", async () => {
    mockTrigger.mockResolvedValue({
      status: "started",
      id: "compute_umap_3d",
      name: "Constellation 3D coordinate compute",
      invalidates: [],
    })
    const user = userEvent.setup()
    renderCard()
    await user.click(screen.getByLabelText(/Run Constellation/))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(toast.info).not.toHaveBeenCalled()
  })

  it("does not claim 'Running' when the trigger collapsed into a pending duplicate", async () => {
    mockTrigger.mockResolvedValue({
      status: "collapsed_into_pending",
      id: "compute_umap_3d",
      name: "Constellation 3D coordinate compute",
      existing_job_id: "abc-123",
      detail: "an equivalent 'compute_umap_3d' job is already pending or running",
    })
    const user = userEvent.setup()
    renderCard()
    await user.click(screen.getByLabelText(/Run Constellation/))
    await waitFor(() => expect(toast.info).toHaveBeenCalled())
    expect(toast.success).not.toHaveBeenCalled()
    const [title] = (toast.info as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(title).toMatch(/already queued/)
  })
})
