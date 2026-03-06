// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { MessageBubble } from "@/components/chat/message-bubble"
import type { ChatMessage, SourceRef } from "@/lib/types"

const makeMsg = (overrides: Partial<ChatMessage> = {}): ChatMessage => ({
  id: "msg-1",
  role: "assistant",
  content: "Hello world",
  timestamp: Date.now(),
  ...overrides,
})

describe("MessageBubble", () => {
  it("renders user message with flex-row-reverse alignment", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ role: "user", content: "Hi there" })} />,
    )
    expect(screen.getByText("Hi there")).toBeInTheDocument()
    // User messages use flex-row-reverse to push avatar to the right
    expect(container.querySelector(".flex-row-reverse")).toBeTruthy()
  })

  it("renders assistant message without flex-row-reverse", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ role: "assistant", content: "I can help!" })} />,
    )
    expect(screen.getByText("I can help!")).toBeInTheDocument()
    // Assistant messages do NOT get flex-row-reverse
    expect(container.querySelector(".flex-row-reverse")).toBeFalsy()
  })

  it("renders markdown content", () => {
    render(
      <MessageBubble message={makeMsg({ content: "**bold text** and *italic*" })} />,
    )
    expect(screen.getByText("bold text")).toBeInTheDocument()
  })

  it("renders code blocks", () => {
    render(
      <MessageBubble message={makeMsg({ content: "```python\nprint('hello')\n```" })} />,
    )
    expect(screen.getByText("print('hello')")).toBeInTheDocument()
  })

  it("shows model badge for assistant messages", () => {
    render(
      <MessageBubble message={makeMsg({ model: "openrouter/openai/gpt-4o-mini" })} />,
    )
    expect(screen.getByText("GPT-4o Mini")).toBeInTheDocument()
  })

  it("shows model badge for user messages when model is set", () => {
    // The ModelBadge is rendered for any message with a model, regardless of role
    render(
      <MessageBubble message={makeMsg({ role: "user", model: "openrouter/openai/gpt-4o-mini" })} />,
    )
    expect(screen.getByText("GPT-4o Mini")).toBeInTheDocument()
  })

  it("shows source attribution when sources are present", () => {
    const sources: SourceRef[] = [
      { artifact_id: "a1", filename: "test.py", domain: "coding", relevance: 0.9, chunk_index: 0 },
    ]
    render(
      <MessageBubble message={makeMsg({ sourcesUsed: sources })} />,
    )
    expect(screen.getByText("1 source")).toBeInTheDocument()
  })

  it("does not show source attribution when no sources", () => {
    render(
      <MessageBubble message={makeMsg({ sourcesUsed: [] })} />,
    )
    expect(screen.queryByText(/source/)).not.toBeInTheDocument()
  })

  it("shows loading animation when content is empty for assistant", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ content: "" })} />,
    )
    // Loading dots should be present
    expect(container.querySelector(".animate-bounce")).toBeTruthy()
  })

  it("shows verification badge when verification status is done", () => {
    render(
      <MessageBubble
        message={makeMsg()}
        verificationStatus={{ state: "done", verified: 3, unverified: 1, uncertain: 0, total: 4 }}
      />,
    )
    // Component renders "{verified}/{total} verified"
    expect(screen.getByText("3/4 verified")).toBeInTheDocument()
  })

  it("shows Verifying text for loading verification", () => {
    render(
      <MessageBubble
        message={makeMsg()}
        verificationStatus={{ state: "loading" }}
      />,
    )
    expect(screen.getByText("Verifying")).toBeInTheDocument()
  })

  it("renders multiple messages in sequence", () => {
    const msg1 = makeMsg({ id: "m1", role: "user", content: "Question" })
    const msg2 = makeMsg({ id: "m2", role: "assistant", content: "Answer" })
    const { container } = render(
      <>
        <MessageBubble message={msg1} />
        <MessageBubble message={msg2} />
      </>,
    )
    expect(screen.getByText("Question")).toBeInTheDocument()
    expect(screen.getByText("Answer")).toBeInTheDocument()
    // Only the user message has flex-row-reverse
    expect(container.querySelectorAll(".flex-row-reverse")).toHaveLength(1)
  })
})
