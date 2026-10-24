// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// What an answer credits is what its prompt carried (audit F41, F43, F74).
import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatSend } from "@/hooks/use-chat-send"
import type { ChatMessage, KBQueryResult, SourceRef } from "@/lib/types"

vi.mock("@/lib/api", () => ({
  queryKB: vi.fn(),
  recallMemories: vi.fn(),
  compressConversation: vi.fn().mockResolvedValue({ messages: [], original_tokens: 0, compressed_tokens: 0 }),
}))

import { queryKB, recallMemories } from "@/lib/api"

const mockQueryKB = queryKB as ReturnType<typeof vi.fn>
const mockRecallMemories = recallMemories as ReturnType<typeof vi.fn>

// A llama-family id: its 16,000-char context budget is small enough to overflow.
const LLAMA = "openrouter/meta-llama/llama-4-maverick"

beforeEach(() => {
  vi.clearAllMocks()
  mockQueryKB.mockResolvedValue({ results: [] })
  mockRecallMemories.mockResolvedValue([])
})

const doc = (o: Partial<KBQueryResult>): KBQueryResult => ({
  content: "chunk",
  relevance: 0.8,
  artifact_id: "a",
  filename: "a.md",
  domain: "general",
  chunk_index: 0,
  collection: "kb_general",
  ingested_at: "2026-10-01T00:00:00Z",
  ...o,
})

function options(overrides: Record<string, unknown> = {}) {
  const send = vi.fn()
  return {
    activeId: "conv-1",
    activeMessages: [] as ChatMessage[],
    create: vi.fn().mockReturnValue("conv-new"),
    addMessage: vi.fn(),
    updateModel: vi.fn(),
    replaceMessages: vi.fn(),
    send,
    selectedModel: LLAMA,
    setSelectedModel: vi.fn(),
    routingMode: "manual",
    costSensitivity: "medium" as const,
    autoInject: true,
    autoInjectThreshold: 0.5,
    includePacks: true,
    injectedContext: [] as KBQueryResult[],
    kbResults: [] as KBQueryResult[],
    clearInjected: vi.fn(),
    privateModeLevel: 0,
    onBeforeSend: vi.fn(),
    ...overrides,
  }
}

async function sendOnce(opts: ReturnType<typeof options>, text: string) {
  const { result } = renderHook(() => useChatSend(opts))
  await act(async () => {
    await result.current.handleSend(text)
  })
  const [, messages, , sources] = opts.send.mock.calls[0] as [string, Pick<ChatMessage, "role" | "content">[], string, SourceRef[] | undefined]
  return { system: messages.find((m) => m.role === "system")?.content ?? "", sources: sources ?? [] }
}

describe("sources an answer credits", () => {
  it("leave out a document the context budget dropped", async () => {
    mockQueryKB.mockResolvedValue({
      results: [
        doc({ artifact_id: "big", filename: "big.md", relevance: 0.9, content: "alpha ".repeat(2000) }),
        doc({ artifact_id: "over", filename: "over.md", relevance: 0.85, content: "bravo ".repeat(1000) }),
      ],
    })

    const { system, sources } = await sendOnce(options(), "tell me about alpha")

    expect(system).toContain("big.md")
    expect(system).not.toContain("over.md")
    expect(sources.map((s) => s.artifact_id)).toEqual(["big"])
  })

  it("leave a dropped document free to be shown on a later turn", async () => {
    const big = doc({ artifact_id: "big", filename: "big.md", relevance: 0.9, content: "alpha ".repeat(2000) })
    const over = doc({ artifact_id: "over", filename: "over.md", relevance: 0.85, content: "bravo ".repeat(1000) })
    mockQueryKB.mockResolvedValue({ results: [big, over] })
    const opts = options()
    const { result } = renderHook(() => useChatSend(opts))
    await act(async () => {
      await result.current.handleSend("tell me about alpha")
    })

    mockQueryKB.mockResolvedValue({ results: [over] })
    await act(async () => {
      await result.current.handleSend("and bravo?")
    })

    const second = opts.send.mock.calls[1][1] as Pick<ChatMessage, "role" | "content">[]
    expect(second.find((m) => m.role === "system")?.content ?? "").toContain("over.md")
  })

  it("label a memory as memory even when it carries a URL", async () => {
    // A memory row from the server's retrieval keeps the URL of the page it was learned from.
    mockQueryKB.mockResolvedValue({
      results: [
        doc({ artifact_id: "d1", relevance: 0.9 }),
        doc({ artifact_id: "m1", relevance: 0.9, content: "we chose X", source_type: "memory", source_url: "https://example.com" }),
      ],
    })

    const { sources } = await sendOnce(options(), "what did we decide")

    expect(sources.find((s) => s.artifact_id === "m1")?.source_type).toBe("memory")
  })
})

describe("the relative injection floor", () => {
  it("is not raised over documents by a high-salience memory", async () => {
    mockRecallMemories.mockResolvedValue([
      { memory_id: "m1", memory_type: "fact", content: "a remembered fact", summary: "", relevance: 0.95 },
    ])
    mockQueryKB.mockResolvedValue({
      results: [doc({ artifact_id: "d1", filename: "answer.md", relevance: 0.3, content: "the document that answers" })],
    })

    const { system } = await sendOnce(options(), "a question the document answers")

    // One pool put the floor at 0.5 x 0.95 = 0.475 and dropped the 0.30 document.
    expect(system).toContain("answer.md")
    expect(system).toContain("a remembered fact")
  })
})
