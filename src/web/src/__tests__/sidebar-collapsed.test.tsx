// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Sidebar } from "@/components/layout/sidebar"
import { ConversationsProvider } from "@/contexts/conversations-context"

vi.mock("@/lib/api", () => ({
  fetchModelUpdatesFull: vi.fn().mockResolvedValue({
    updates: [{ update_id: "coding:x", model_id: "x", update_type: "new", details: {}, detected_at: "now" }],
  }),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  fetchForgottenConversations: vi.fn().mockResolvedValue({ items: [], cursor: null }),
  ConversationGoneError: class ConversationGoneError extends Error { id = "" },
  syncConversation: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchHealth: vi.fn().mockResolvedValue({ version: "1.0.0" }),
  applyModelUpdates: vi.fn(),
}))

const noop = () => {}

function renderSidebar(collapsed: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ConversationsProvider>
        <Sidebar
          activePane="chat"
          onPaneChange={noop}
          collapsed={collapsed}
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
})

const NAV_LABELS = ["Chat", "Subjects", "Memories", "Briefs", "Workflows", "Automations", "Sources", "Settings"]

describe("Sidebar, collapsed", () => {
  it.each(NAV_LABELS)("names the %s button, which shows only an icon", (label) => {
    renderSidebar(true)
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument()
  })

  // jsdom does no layout, so this pins the rule rather than the pixels: the
  // nav button does not shrink, so a second control beside it in a 56 px
  // sidebar lands outside it. Stacked, both fit. Measured in a browser.
  it("stacks the new-conversation and model-update controls under their nav button", async () => {
    renderSidebar(true)
    const plus = screen.getByRole("button", { name: "New conversation" })
    const download = await screen.findByRole("button", { name: "1 model update available" })
    for (const control of [plus, download]) {
      expect(control.parentElement).toHaveClass("flex-col")
    }
    expect(plus.parentElement).toContainElement(screen.getByRole("button", { name: "Chat" }))
    expect(download.parentElement).toContainElement(screen.getByRole("button", { name: "Settings" }))
  })
})

describe("Sidebar, expanded", () => {
  it("keeps the new-conversation and model-update controls beside their nav button", async () => {
    renderSidebar(false)
    const plus = screen.getByRole("button", { name: "New conversation" })
    const download = await screen.findByRole("button", { name: "1 model update available" })
    for (const control of [plus, download]) {
      expect(control.parentElement).not.toHaveClass("flex-col")
    }
  })
})
