// Copyright (c) 2026 Justin Michaels. All rights reserved.
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

describe("ConversationList", () => {
  it("renders empty state when no conversations", () => {
    render(
      <ConversationList conversations={[]} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} />,
    )
    expect(screen.getByText("No conversations yet")).toBeInTheDocument()
  })

  it("renders conversation titles", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} />,
    )
    expect(screen.getByText("First conversation")).toBeInTheDocument()
    expect(screen.getByText("Second conversation")).toBeInTheDocument()
    expect(screen.getByText("Third conversation")).toBeInTheDocument()
  })

  it("highlights the active conversation", () => {
    render(
      <ConversationList conversations={mockConversations} activeId="c2" onSelect={vi.fn()} onDelete={vi.fn()} />,
    )
    const activeItem = screen.getByText("Second conversation").closest("[role='button']")
    expect(activeItem?.className).toMatch(/bg-/)
  })

  it("calls onSelect when a conversation is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={vi.fn()} />,
    )
    await user.click(screen.getByText("First conversation"))
    expect(onSelect).toHaveBeenCalledWith("c1")
  })

  it("calls onSelect on Enter key", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={vi.fn()} />,
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
      <ConversationList conversations={mockConversations} activeId={null} onSelect={onSelect} onDelete={onDelete} />,
    )
    // Use aria-label to find actual delete buttons (not the role=button conversation items)
    const deleteButtons = screen.getAllByLabelText("Delete conversation")
    await user.click(deleteButtons[0])
    expect(onDelete).toHaveBeenCalledWith("c1")
    // onSelect should NOT be called when deleting (stopPropagation)
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("renders all conversations in order", () => {
    render(
      <ConversationList conversations={mockConversations} activeId={null} onSelect={vi.fn()} onDelete={vi.fn()} />,
    )
    const titles = ["First conversation", "Second conversation", "Third conversation"]
    titles.forEach((title) => {
      expect(screen.getByText(title)).toBeInTheDocument()
    })
  })
})
