// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Task 3.2 — sidebar Simple/Advanced settings-mode toggle.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Sidebar } from "@/components/layout/sidebar"
import { ConversationsProvider } from "@/contexts/conversations-context"
import { getSettingsMode } from "@/lib/settings-mode"

vi.mock("@/lib/api", () => ({
  fetchModelUpdatesFull: vi.fn().mockResolvedValue({ updates: [] }),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  syncConversation: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
}))

vi.mock("@/lib/api/settings", () => ({
  fetchHealth: vi.fn().mockResolvedValue({ version: "1.0.0" }),
  applyModelUpdates: vi.fn(),
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
})

describe("Sidebar settings-mode toggle", () => {
  it("reflects the current settings mode (defaults to simple)", () => {
    renderSidebar()
    expect(screen.getByRole("button", { name: /switch to advanced/i })).toBeInTheDocument()
  })

  it("flips the settings mode on click and updates its label", () => {
    renderSidebar()
    const toggle = screen.getByRole("button", { name: /switch to advanced/i })
    fireEvent.click(toggle)
    expect(getSettingsMode()).toBe("advanced")
    expect(screen.getByRole("button", { name: /switch to simple/i })).toBeInTheDocument()
  })

  it("is axe-clean", async () => {
    const { container } = renderSidebar()
    expect(await axe(container)).toHaveNoViolations()
  })
})
