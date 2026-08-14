// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// RA-31 — the sidebar's model-update badge previously only surfaced a count;
// there was no way to apply the pending updates from the sidebar itself.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Sidebar } from "@/components/layout/sidebar"
import { ConversationsProvider } from "@/contexts/conversations-context"

const fetchModelUpdatesFull = vi.fn()
const applyModelUpdates = vi.fn()

vi.mock("@/lib/api", () => ({
  fetchModelUpdatesFull: (...args: unknown[]) => fetchModelUpdatesFull(...args),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  syncConversation: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchHealth: vi.fn().mockResolvedValue({ version: "1.0.0" }),
  applyModelUpdates: (...args: unknown[]) => applyModelUpdates(...args),
}))

// Stub sonner so toast.success/error don't render into the DOM during tests.
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

const noop = () => {}

function renderSidebar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ConversationsProvider>
        <Sidebar
          activePane="chat"
          onPaneChange={noop}
          collapsed={false}
          onToggleCollapse={noop}
          theme="light"
          onToggleTheme={noop}
        />
      </ConversationsProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  fetchModelUpdatesFull.mockReset()
  applyModelUpdates.mockReset()
})

describe("Sidebar model-update badge", () => {
  it("renders no badge when there are no pending updates", async () => {
    fetchModelUpdatesFull.mockResolvedValue({ updates: [] })
    renderSidebar()
    await waitFor(() => expect(fetchModelUpdatesFull).toHaveBeenCalled())
    expect(screen.queryByLabelText(/model update.*available/i)).toBeNull()
  })

  it("offers an Apply action that calls POST /models/updates/apply", async () => {
    const user = userEvent.setup()
    fetchModelUpdatesFull.mockResolvedValue({
      updates: [
        { update_id: "coding:x", model_id: "x", update_type: "new", details: {}, detected_at: "now" },
      ],
    })
    applyModelUpdates.mockResolvedValue({
      applied: [{ role: "coding", from: "old", to: "new" }],
      restart_required: false,
      catalog_size: 10,
      tier_updates: [],
    })

    renderSidebar()

    const trigger = await screen.findByLabelText(/1 model update available/i)
    await user.click(trigger)

    const applyButton = await screen.findByRole("button", { name: /apply now/i })
    await user.click(applyButton)

    await waitFor(() => expect(applyModelUpdates).toHaveBeenCalledTimes(1))
  })
})
