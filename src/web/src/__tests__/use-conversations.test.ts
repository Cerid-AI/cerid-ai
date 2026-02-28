// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach, vi } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import { MODELS } from "@/lib/types"

// Mock localStorage
const store: Record<string, string> = {}
beforeEach(() => {
  Object.keys(store).forEach((k) => delete store[k])
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
  })
})

const DEFAULT_MODEL = MODELS[0].id

describe("useConversations", () => {
  it("starts with empty conversations", () => {
    const { result } = renderHook(() => useConversations())
    expect(result.current.conversations).toEqual([])
    expect(result.current.active).toBeNull()
    expect(result.current.activeId).toBeNull()
  })

  it("creates a new conversation", () => {
    const { result } = renderHook(() => useConversations())

    let id: string
    act(() => {
      id = result.current.create(DEFAULT_MODEL)
    })

    expect(result.current.conversations).toHaveLength(1)
    expect(result.current.activeId).toBe(id!)
    expect(result.current.active?.model).toBe(DEFAULT_MODEL)
    expect(result.current.active?.title).toBe("New conversation")
  })

  it("adds a message and updates title for first user message", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => {
      convoId = result.current.create(DEFAULT_MODEL)
    })

    act(() => {
      result.current.addMessage(convoId!, {
        id: "msg-1",
        role: "user",
        content: "What are Python decorators?",
        timestamp: Date.now(),
      })
    })

    expect(result.current.active?.messages).toHaveLength(1)
    expect(result.current.active?.title).toBe("What are Python decorators?")
  })

  it("does not update title for subsequent messages", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m1", role: "user", content: "First message", timestamp: Date.now(),
      })
    })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m2", role: "assistant", content: "Response", model: DEFAULT_MODEL, timestamp: Date.now(),
      })
    })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m3", role: "user", content: "A completely different topic", timestamp: Date.now(),
      })
    })

    expect(result.current.active?.title).toBe("First message")
  })

  it("updates the last message content (streaming)", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m1", role: "assistant", content: "", model: DEFAULT_MODEL, timestamp: Date.now(),
      })
    })
    act(() => {
      result.current.updateLastMessage(convoId!, "Hello, ")
    })
    act(() => {
      result.current.updateLastMessage(convoId!, "Hello, world!")
    })

    expect(result.current.active?.messages[0].content).toBe("Hello, world!")
  })

  it("updates model on a conversation", () => {
    const { result } = renderHook(() => useConversations())
    const newModel = MODELS[1].id

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.updateModel(convoId!, newModel)
    })

    expect(result.current.active?.model).toBe(newModel)
  })

  it("removes a conversation and selects next", () => {
    const { result } = renderHook(() => useConversations())

    let id1: string
    let id2: string
    act(() => { id1 = result.current.create(DEFAULT_MODEL) })
    act(() => { id2 = result.current.create(DEFAULT_MODEL) })

    // id2 is now active (most recent, at index 0)
    expect(result.current.activeId).toBe(id2!)

    act(() => { result.current.remove(id2!) })

    expect(result.current.conversations).toHaveLength(1)
    expect(result.current.activeId).toBe(id1!)
  })

  it("persists conversations to localStorage", () => {
    const { result } = renderHook(() => useConversations())

    act(() => { result.current.create(DEFAULT_MODEL) })

    const stored = JSON.parse(store["cerid-conversations"] ?? "[]")
    expect(stored).toHaveLength(1)
  })

  it("preserves sourcesUsed through add/update cycle", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m1",
        role: "assistant",
        content: "",
        model: DEFAULT_MODEL,
        timestamp: Date.now(),
        sourcesUsed: [
          { artifact_id: "a1", filename: "f.txt", domain: "coding", relevance: 0.9, chunk_index: 0 },
        ],
      })
    })
    act(() => {
      result.current.updateLastMessage(convoId!, "Streamed content")
    })

    const msg = result.current.active?.messages[0]
    expect(msg?.content).toBe("Streamed content")
    expect(msg?.sourcesUsed).toHaveLength(1)
    expect(msg?.sourcesUsed![0].artifact_id).toBe("a1")
  })

  it("migrates old model IDs missing openrouter/ prefix", () => {
    // Pre-seed localStorage with an old-format conversation
    store["cerid-conversations"] = JSON.stringify([
      {
        id: "old-conv",
        title: "Old conversation",
        messages: [],
        model: "anthropic/claude-sonnet-4",
        createdAt: Date.now(),
        updatedAt: Date.now(),
      },
    ])

    const { result } = renderHook(() => useConversations())

    expect(result.current.conversations[0].model).toBe("openrouter/anthropic/claude-sonnet-4")
  })

  it("replaces all messages for a conversation", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m1", role: "user", content: "Original message", timestamp: Date.now(),
      })
    })

    const newMessages = [
      { id: "summary-1", role: "system" as const, content: "Summary of conversation", timestamp: Date.now() },
    ]
    act(() => {
      result.current.replaceMessages(convoId!, newMessages)
    })

    expect(result.current.active?.messages).toHaveLength(1)
    expect(result.current.active?.messages[0].content).toBe("Summary of conversation")
    expect(result.current.active?.messages[0].role).toBe("system")
  })

  it("clears all messages for a conversation", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m1", role: "user", content: "Message to clear", timestamp: Date.now(),
      })
    })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m2", role: "assistant", content: "Response", model: DEFAULT_MODEL, timestamp: Date.now(),
      })
    })

    expect(result.current.active?.messages).toHaveLength(2)

    act(() => {
      result.current.clearMessages(convoId!)
    })

    expect(result.current.active?.messages).toHaveLength(0)
  })

  it("replaceMessages persists to localStorage", () => {
    const { result } = renderHook(() => useConversations())

    let convoId: string
    act(() => { convoId = result.current.create(DEFAULT_MODEL) })
    act(() => {
      result.current.addMessage(convoId!, {
        id: "m1", role: "user", content: "Original", timestamp: Date.now(),
      })
    })

    const newMsgs = [
      { id: "s1", role: "system" as const, content: "Summarized", timestamp: Date.now() },
    ]
    act(() => {
      result.current.replaceMessages(convoId!, newMsgs)
    })

    const stored = JSON.parse(store["cerid-conversations"] ?? "[]")
    expect(stored[0].messages).toHaveLength(1)
    expect(stored[0].messages[0].content).toBe("Summarized")
  })
})
