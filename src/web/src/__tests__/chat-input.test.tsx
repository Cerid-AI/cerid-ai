// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

// Mock the drag-drop hook — ChatInput uses it internally
vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({
    isDragOver: false,
    dragHandlers: {
      onDragOver: vi.fn(),
      onDrop: vi.fn(),
      onDragLeave: vi.fn(),
      onDragEnter: vi.fn(),
    },
  }),
}))

import { ChatInput } from "@/components/chat/chat-input"

const defaultProps = {
  onSend: vi.fn(),
  onStop: vi.fn(),
  isStreaming: false,
}

beforeEach(() => {
  vi.restoreAllMocks()
  defaultProps.onSend = vi.fn()
  defaultProps.onStop = vi.fn()
})

describe("ChatInput", () => {
  // ---- Rendering ----

  it("renders textarea with placeholder", () => {
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    expect(textarea).toBeInTheDocument()
    expect(textarea.tagName).toBe("TEXTAREA")
  })

  it("renders send button", () => {
    render(<ChatInput {...defaultProps} />)
    const sendBtn = screen.getByRole("button", { name: /send message/i })
    expect(sendBtn).toBeInTheDocument()
  })

  // ---- Typing ----

  it("typing updates textarea value", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "Hello world")
    expect(textarea).toHaveValue("Hello world")
  })

  it("calls onInputChange callback while typing", async () => {
    const onInputChange = vi.fn()
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} onInputChange={onInputChange} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "Hi")
    expect(onInputChange).toHaveBeenCalled()
  })

  // ---- Enter sends message ----

  it("enter key triggers send callback", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "Hello")
    await user.keyboard("{Enter}")
    expect(defaultProps.onSend).toHaveBeenCalledWith("Hello")
  })

  it("shift+enter does not send", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "Line 1")
    await user.keyboard("{Shift>}{Enter}{/Shift}")
    expect(defaultProps.onSend).not.toHaveBeenCalled()
  })

  // ---- Empty message not sent ----

  it("empty message is not sent via enter", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.click(textarea)
    await user.keyboard("{Enter}")
    expect(defaultProps.onSend).not.toHaveBeenCalled()
  })

  it("whitespace-only message is not sent", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "   ")
    await user.keyboard("{Enter}")
    expect(defaultProps.onSend).not.toHaveBeenCalled()
  })

  // ---- Send button ----

  it("send button click triggers send", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "Test message")
    const sendBtn = screen.getByRole("button", { name: /send message/i })
    await user.click(sendBtn)
    expect(defaultProps.onSend).toHaveBeenCalledWith("Test message")
  })

  it("send button is disabled when input is empty", () => {
    render(<ChatInput {...defaultProps} />)
    const sendBtn = screen.getByRole("button", { name: /send message/i })
    expect(sendBtn).toBeDisabled()
  })

  // ---- Clears after send ----

  it("textarea clears after successful send", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    await user.type(textarea, "Hello")
    await user.keyboard("{Enter}")
    expect(textarea).toHaveValue("")
  })

  // ---- Streaming state ----

  it("textarea is disabled during streaming", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    expect(textarea).toBeDisabled()
  })

  it("shows stop button during streaming", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />)
    const stopBtn = screen.getByRole("button", { name: /stop generation/i })
    expect(stopBtn).toBeInTheDocument()
  })

  it("stop button calls onStop", async () => {
    const user = userEvent.setup()
    render(<ChatInput {...defaultProps} isStreaming={true} />)
    const stopBtn = screen.getByRole("button", { name: /stop generation/i })
    await user.click(stopBtn)
    expect(defaultProps.onStop).toHaveBeenCalledTimes(1)
  })

  it("does not show send button during streaming", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />)
    expect(screen.queryByRole("button", { name: /send message/i })).not.toBeInTheDocument()
  })

  // ---- Disabled prop ----

  it("textarea is disabled when disabled prop is true", () => {
    render(<ChatInput {...defaultProps} disabled={true} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    expect(textarea).toBeDisabled()
  })

  it("send button is disabled when disabled prop is true", () => {
    render(<ChatInput {...defaultProps} disabled={true} />)
    const sendBtn = screen.getByRole("button", { name: /send message/i })
    expect(sendBtn).toBeDisabled()
  })

  // ---- Long message ----

  it("handles long messages gracefully", () => {
    const longMsg = "A".repeat(5000)
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByPlaceholderText(/type a message/i)
    // Use fireEvent throughout — userEvent.type is too slow for 5000 chars
    fireEvent.change(textarea, { target: { value: longMsg } })
    expect(textarea).toHaveValue(longMsg)
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })
    expect(defaultProps.onSend).toHaveBeenCalledWith(longMsg)
  })

  // ---- Accessibility ----

  it("has correct aria-label on textarea", () => {
    render(<ChatInput {...defaultProps} />)
    const textarea = screen.getByLabelText("Chat message input")
    expect(textarea).toBeInTheDocument()
  })

  it("has correct aria-label on send button", () => {
    render(<ChatInput {...defaultProps} />)
    expect(screen.getByLabelText("Send message")).toBeInTheDocument()
  })

  it("has correct aria-label on stop button during streaming", () => {
    render(<ChatInput {...defaultProps} isStreaming={true} />)
    expect(screen.getByLabelText("Stop generation")).toBeInTheDocument()
  })

  // ---- Injected source badge ----

  it("shows injected source count badge", () => {
    render(<ChatInput {...defaultProps} injectedCount={3} />)
    expect(screen.getByText("3 sources")).toBeInTheDocument()
  })

  it("does not show source badge when injectedCount is 0", () => {
    render(<ChatInput {...defaultProps} injectedCount={0} />)
    expect(screen.queryByText(/source/)).not.toBeInTheDocument()
  })
})
