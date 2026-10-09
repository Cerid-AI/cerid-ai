// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"

vi.mock("@/lib/api", () => ({
  previewForget: vi.fn().mockResolvedValue({
    subject: { kind: "conversation", id: "c1" }, title: "First conversation",
    groups: [{ key: "memories", default: "checked", items: [{ kind: "artifact", id: "m1", label: "A memory", shared_with: 0 }] }],
    derived_facts: 0, out_of_reach: [],
  }),
}))

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ConversationList } from "@/components/chat/conversation-list"
import type { Conversation } from "@/lib/types"

const now = Date.now()

const makeConversation = (id: string, title: string): Conversation => ({
  id,
  title,
  messages: [],
  model: "openrouter/openai/gpt-4o",
  createdAt: now - 1000,
  updatedAt: now,
})

const mockConversations: Conversation[] = [
  makeConversation("c1", "First conversation"),
  makeConversation("c2", "Second conversation"),
  makeConversation("c3", "Third conversation"),
]

const defaultProps = {
  onArchive: vi.fn(),
  onUnarchive: vi.fn(),
  showArchived: false,
  archivedCount: 0,
  onToggleShowArchived: vi.fn(),
  onForget: vi.fn(),
  onBulkArchive: vi.fn(),
}

describe("ConversationList", () => {
  it("renders empty state when no conversations", () => {
    render(
      <ConversationList conversations={[]} activeId={null} onSelect={vi.fn()} {...defaultProps} />,
    )
    expect(screen.getByText("No conversations yet")).toBeInTheDocument()
  })

  it("renders conversation titles", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} />,
    )
    expect(screen.getByText("First conversation")).toBeInTheDocument()
    expect(screen.getByText("Second conversation")).toBeInTheDocument()
    expect(screen.getByText("Third conversation")).toBeInTheDocument()
  })

  it("highlights the active conversation", () => {
    render(
      <ConversationList conversations={mockConversations} activeId="c2" onSelect={vi.fn()} {...defaultProps} />,
    )
    const activeItem = screen.getByText("Second conversation").closest("[role='button']")
    expect(activeItem?.className).toMatch(/bg-/)
  })

  it("calls onSelect when a conversation is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} {...defaultProps} />,
    )
    await user.click(screen.getByText("First conversation"))
    expect(onSelect).toHaveBeenCalledWith("c1")
  })

  it("calls onSelect on Enter key", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} {...defaultProps} />,
    )
    const item = screen.getByText("First conversation").closest("[role='button']") as HTMLElement
    item.focus()
    await user.keyboard("{Enter}")
    expect(onSelect).toHaveBeenCalledWith("c1")
  })

  it("opens the forget dialog and confirms with the chosen mode and derived items", async () => {
    const user = userEvent.setup()
    const onForget = vi.fn()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} {...defaultProps} onForget={onForget} />,
    )
    await user.click(screen.getAllByLabelText("Delete conversation")[0])
    // Gated behind the dialog: a bare click forgets nothing (audit: one misclick).
    expect(onForget).not.toHaveBeenCalled()
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("Delete this conversation?")).toBeInTheDocument()
    await within(dialog).findByRole("checkbox", { name: "A memory" })
    await user.click(within(dialog).getByRole("button", { name: "Move to Trash" }))
    expect(onForget).toHaveBeenCalledWith(["c1"], "trash", [{ kind: "artifact", id: "m1" }])
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("forgets nothing when the dialog is cancelled", async () => {
    const user = userEvent.setup()
    const onForget = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} onForget={onForget} />,
    )
    await user.click(screen.getAllByLabelText("Delete conversation")[0])
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(onForget).not.toHaveBeenCalled()
  })

  it("renders all conversations in order", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} />,
    )
    const titles = ["First conversation", "Second conversation", "Third conversation"]
    titles.forEach((title) => {
      expect(screen.getByText(title)).toBeInTheDocument()
    })
  })

  it("shows archive toggle with count", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} archivedCount={2} />,
    )
    expect(screen.getByText("View archived (2)")).toBeInTheDocument()
  })

  it("shows back to active when viewing archived", () => {
    render(
      <ConversationList conversations={[]} activeId={null} onSelect={vi.fn()} {...defaultProps} showArchived={true} />,
    )
    expect(screen.getByText("Back to active")).toBeInTheDocument()
  })

  it("calls onArchive when archive button is clicked", async () => {
    const user = userEvent.setup()
    const onArchive = vi.fn()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} {...defaultProps} onArchive={onArchive} />,
    )
    const archiveButtons = screen.getAllByLabelText("Archive conversation")
    await user.click(archiveButtons[0])
    expect(onArchive).toHaveBeenCalledWith("c1")
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("shows search input", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} />,
    )
    expect(screen.getByPlaceholderText("Search conversations...")).toBeInTheDocument()
  })

  // F-02-05: conversation titles are truncated in the sidebar; hovering must
  // surface the full title via a Radix Tooltip so the user can disambiguate
  // multiple long-titled conversations without clicking through each one.
  it("wraps each conversation title in a Tooltip trigger", () => {
    const longTitle = "What is the standard deduction for a single filer in 2026?"
    render(
      <ConversationList
        conversations={[makeConversation("c1", longTitle)]}
        activeId={null}
        onSelect={vi.fn()}
       
        {...defaultProps}
      />,
    )
    const titleSpan = screen.getByText(longTitle)
    // Radix marks tooltip triggers with data-slot="tooltip-trigger" on the
    // resolved DOM element. asChild forwards the slot attribute to our span.
    const trigger = titleSpan.closest("[data-slot='tooltip-trigger']")
    expect(trigger).not.toBeNull()
  })
})

// Round 5 item 5.3 (D20-A) — rename is discoverable from the row's actions,
// not only by double-clicking the title.
describe("ConversationList rename action", () => {
  it("opens the same inline editor the double-click uses and focuses it", async () => {
    const user = userEvent.setup()
    const onRename = vi.fn()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} {...defaultProps} onRename={onRename} />,
    )
    await user.click(screen.getAllByLabelText("Rename conversation")[0])
    const editor = screen.getByDisplayValue("First conversation")
    expect(editor).toHaveFocus()
    expect(onSelect).not.toHaveBeenCalled()

    await user.clear(editor)
    await user.type(editor, "Renamed{Enter}")
    expect(onRename).toHaveBeenCalledWith("c1", "Renamed")
  })

  it("is absent when the list has no rename handler", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} />,
    )
    expect(screen.queryByLabelText("Rename conversation")).toBeNull()
  })

  it("bulk delete offers Move to Trash and Forget permanently", async () => {
    const user = userEvent.setup()
    const onForget = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} {...defaultProps} onForget={onForget} />,
    )
    await user.click(screen.getByLabelText("Edit conversations"))
    const boxes = screen.getAllByRole("checkbox")
    await user.click(boxes[0])
    await user.click(boxes[1])
    await user.click(screen.getByRole("button", { name: /Delete \(2\)/ }))
    let dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: "Forget permanently" }))
    expect(onForget).toHaveBeenLastCalledWith(["c1", "c2"], "permanent", [])
    await user.click(screen.getByLabelText("Edit conversations"))
    await user.click(screen.getAllByRole("checkbox")[2])
    await user.click(screen.getByRole("button", { name: /Delete \(1\)/ }))
    dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: "Move to Trash" }))
    expect(onForget).toHaveBeenLastCalledWith(["c3"], "trash", [])
  })
})
