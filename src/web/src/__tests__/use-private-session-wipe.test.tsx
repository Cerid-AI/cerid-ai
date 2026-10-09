// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

const convos = vi.hoisted(() => ({ list: [] as { id: string; private?: boolean }[] }))

vi.mock("@/contexts/conversations-context", () => ({
  useConversationsContext: () => ({ conversations: convos.list }),
}))
vi.mock("@/lib/api/settings", () => ({ wipePrivateSession: vi.fn() }))

import { renderHook } from "@testing-library/react"
import { usePrivateSessionWipe, getTabSessionId } from "@/hooks/use-private-session-wipe"
import { wipePrivateSession } from "@/lib/api/settings"

describe("usePrivateSessionWipe", () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    vi.clearAllMocks()
    convos.list = [{ id: "p1", private: true }, { id: "plain" }, { id: "p2", private: true }]
  })

  it("at L4, closing the tab wipes its private conversations under the tab's session id", () => {
    localStorage.setItem("cerid-private-mode-level", "4")
    renderHook(() => usePrivateSessionWipe())
    window.dispatchEvent(new Event("beforeunload"))
    expect(wipePrivateSession).toHaveBeenCalledTimes(1)
    expect(wipePrivateSession).toHaveBeenCalledWith({ sessionId: getTabSessionId(), conversationIds: ["p1", "p2"] })
  })

  it("below L4 closing the tab sends nothing", () => {
    localStorage.setItem("cerid-private-mode-level", "1")
    renderHook(() => usePrivateSessionWipe())
    window.dispatchEvent(new Event("beforeunload"))
    expect(wipePrivateSession).not.toHaveBeenCalled()
  })

  it("reads the level and the conversations at close time, not at mount", () => {
    const { rerender } = renderHook(() => usePrivateSessionWipe())
    localStorage.setItem("cerid-private-mode-level", "4")
    convos.list = [{ id: "p3", private: true }]
    rerender()
    window.dispatchEvent(new Event("beforeunload"))
    expect(wipePrivateSession).toHaveBeenCalledWith({ sessionId: getTabSessionId(), conversationIds: ["p3"] })
  })

  it("keeps one session id per tab", () => {
    const first = getTabSessionId()
    expect(first).toMatch(/^tab-/)
    expect(getTabSessionId()).toBe(first)
  })
})
