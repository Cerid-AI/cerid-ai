// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
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
  onBulkDelete: vi.fn(),
  onBulkArchive: vi.fn(),
}

describe("ConversationList", () => {
  it("renders empty state when no conversations", () => {
    render(
      <ConversationList conversations={[]} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} />,
    )
    expect(screen.getByText("No conversations yet")).toBeInTheDocument()
  })

  it("renders conversation titles", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} />,
    )
    expect(screen.getByText("First conversation")).toBeInTheDocument()
    expect(screen.getByText("Second conversation")).toBeInTheDocument()
    expect(screen.getByText("Third conversation")).toBeInTheDocument()
  })

  it("highlights the active conversation", () => {
    render(
      <ConversationList conversations={mockConversations} activeId="c2" onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} />,
    )
    const activeItem = screen.getByText("Second conversation").closest("[role='button']")
    expect(activeItem?.className).toMatch(/bg-/)
  })

  it("calls onSelect when a conversation is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={vi.fn()} {...defaultProps} />,
    )
    await user.click(screen.getByText("First conversation"))
    expect(onSelect).toHaveBeenCalledWith("c1")
  })

  it("calls onSelect on Enter key", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={vi.fn()} {...defaultProps} />,
    )
    const item = screen.getByText("First conversation").closest("[role='button']") as HTMLElement
    item.focus()
    await user.keyboard("{Enter}")
    expect(onSelect).toHaveBeenCalledWith("c1")
  })

  it("calls onDelete when delete button is clicked", async () => {
    const user = userEvent.setup()
    const onDelete = vi.fn()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={onDelete} {...defaultProps} />,
    )
    const deleteButtons = screen.getAllByLabelText("Delete conversation")
    await user.click(deleteButtons[0])
    expect(onDelete).toHaveBeenCalledWith("c1")
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("renders all conversations in order", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} />,
    )
    const titles = ["First conversation", "Second conversation", "Third conversation"]
    titles.forEach((title) => {
      expect(screen.getByText(title)).toBeInTheDocument()
    })
  })

  it("shows archive toggle with count", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} archivedCount={2} />,
    )
    expect(screen.getByText("View archived (2)")).toBeInTheDocument()
  })

  it("shows back to active when viewing archived", () => {
    render(
      <ConversationList conversations={[]} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} showArchived={true} />,
    )
    expect(screen.getByText("Back to active")).toBeInTheDocument()
  })

  it("calls onArchive when archive button is clicked", async () => {
    const user = userEvent.setup()
    const onArchive = vi.fn()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={vi.fn()} {...defaultProps} onArchive={onArchive} />,
    )
    const archiveButtons = screen.getAllByLabelText("Archive conversation")
    await user.click(archiveButtons[0])
    expect(onArchive).toHaveBeenCalledWith("c1")
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("shows search input", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} {...defaultProps} />,
    )
    expect(screen.getByPlaceholderText("Search conversations...")).toBeInTheDocument()
  })
})
