// Copyright (c) 2026 Cerid AI. All rights reserved.
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

  // --- Markdown rendering improvements ---

  it("renders external links with target=_blank and ExternalLink icon", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ content: "Visit [Example](https://example.com)" })} />,
    )
    const link = container.querySelector("a[href='https://example.com']") as HTMLAnchorElement
    expect(link).toBeTruthy()
    expect(link.target).toBe("_blank")
    expect(link.rel).toBe("noopener noreferrer")
    // Should have the ExternalLink icon as an SVG child
    expect(link.querySelector("svg")).toBeTruthy()
  })

  it("renders internal links without target=_blank", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ content: "Go to [section](#section)" })} />,
    )
    const link = container.querySelector("a[href='#section']") as HTMLAnchorElement
    expect(link).toBeTruthy()
    expect(link.target).not.toBe("_blank")
    // No ExternalLink icon for internal links
    expect(link.querySelector("svg")).toBeFalsy()
  })

  it("renders table with bordered wrapper and striped rows", () => {
    const md = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
    const { container } = render(
      <MessageBubble message={makeMsg({ content: md })} />,
    )
    // Table wrapper with overflow-x-auto and border
    const wrapper = container.querySelector(".overflow-x-auto")
    expect(wrapper).toBeTruthy()
    expect(wrapper?.querySelector("table")).toBeTruthy()
    // Striped rows via even:bg-muted/20
    const rows = container.querySelectorAll("tr")
    expect(rows.length).toBeGreaterThanOrEqual(2)
  })

  it("renders blockquote with border-l-4 styling", () => {
    const { container } = render(
      <MessageBubble message={makeMsg({ content: "> This is a quote" })} />,
    )
    const bq = container.querySelector("blockquote")
    expect(bq).toBeTruthy()
    expect(bq?.className).toContain("border-l-4")
  })

  it("renders headings with proper hierarchy and IDs", () => {
    const md = "# Main Title\n\n## Subtitle\n\n### Section"
    const { container } = render(
      <MessageBubble message={makeMsg({ content: md })} />,
    )
    const h1 = container.querySelector("h1")
    const h2 = container.querySelector("h2")
    const h3 = container.querySelector("h3")
    expect(h1).toBeTruthy()
    expect(h2).toBeTruthy()
    expect(h3).toBeTruthy()
    // IDs generated from heading text
    expect(h1?.id).toBe("main-title")
    expect(h2?.id).toBe("subtitle")
    expect(h3?.id).toBe("section")
  })

  it("renders long code block in collapsed state with expand button", () => {
    // Generate a code block with 30+ lines (exceeds MAX_CODE_LINES=25)
    const lines = Array.from({ length: 30 }, (_, i) => `console.log(${i})`).join("\n")
    const md = "```javascript\n" + lines + "\n```"
    render(
      <MessageBubble message={makeMsg({ content: md })} />,
    )
    // Should show "Show all N lines" button
    expect(screen.getByText("Show all 30 lines")).toBeInTheDocument()
  })

  it("renders short code block without collapse", () => {
    const lines = Array.from({ length: 5 }, (_, i) => `console.log(${i})`).join("\n")
    const md = "```javascript\n" + lines + "\n```"
    render(
      <MessageBubble message={makeMsg({ content: md })} />,
    )
    // Should NOT show expand button for short code
    expect(screen.queryByText(/Show all/)).not.toBeInTheDocument()
  })

  // --- TOC ---

  it("shows TOC for message with 3+ headings", () => {
    const md = "# Introduction\n\nText\n\n## Background\n\nMore text\n\n## Methods\n\nDetails\n\n## Results\n\nFindings"
    render(
      <MessageBubble message={makeMsg({ content: md })} />,
    )
    // TOC should be present with "Contents" label
    expect(screen.getByText("Contents")).toBeInTheDocument()
    // Should list heading texts
    expect(screen.getAllByText("Introduction")).toHaveLength(2) // One in TOC, one in heading
    expect(screen.getAllByText("Background")).toHaveLength(2)
    expect(screen.getAllByText("Methods")).toHaveLength(2)
    expect(screen.getAllByText("Results")).toHaveLength(2)
  })

  it("does not show TOC for fewer than 3 headings", () => {
    const md = "# Title\n\nSome content\n\n## Only Two\n\nMore content"
    render(
      <MessageBubble message={makeMsg({ content: md })} />,
    )
    expect(screen.queryByText("Contents")).not.toBeInTheDocument()
  })
})
