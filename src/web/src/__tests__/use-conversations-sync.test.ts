// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  syncConversationsBulk: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
}))

import { renderHook, act } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"

describe("useConversations cloud sync", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("syncs conversation to server on create", async () => {
    const { result } = renderHook(() => useConversations())
    act(() => { result.current.create("openrouter/openai/gpt-4o-mini") })
    await vi.waitFor(() => {
      expect(api.syncConversation).toHaveBeenCalledTimes(1)
    })
  })

  it("calls deleteConversationSync on remove", async () => {
    const { result } = renderHook(() => useConversations())
    let id: string
    act(() => { id = result.current.create("openrouter/openai/gpt-4o-mini") })
    vi.mocked(api.syncConversation).mockClear()
    act(() => { result.current.remove(id!) })
    await vi.waitFor(() => {
      expect(api.deleteConversationSync).toHaveBeenCalledWith(id!)
    })
  })

  it("fetches server conversations on mount", async () => {
    renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(api.fetchSyncedConversations).toHaveBeenCalledTimes(1)
    })
  })

  it("merges server conversations not in localStorage", async () => {
    const serverConvo = {
      id: "server-only", title: "From server", messages: [],
      model: "openrouter/openai/gpt-4o-mini",
      createdAt: 1710000000000, updatedAt: 1710000000000,
    }
    vi.mocked(api.fetchSyncedConversations).mockResolvedValueOnce([serverConvo])
    const { result } = renderHook(() => useConversations())
    await vi.waitFor(() => {
      expect(result.current.conversations.some(c => c.id === "server-only")).toBe(true)
    })
  })
})
