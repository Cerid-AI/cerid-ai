// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("@/lib/api", () => ({
  syncConversation: vi.fn().mockResolvedValue(undefined),
  deleteConversationSync: vi.fn().mockResolvedValue(undefined),
  fetchSyncedConversations: vi.fn().mockResolvedValue([]),
  fetchForgottenConversations: vi.fn().mockResolvedValue({ items: [], cursor: null }),
  forgetSubjects: vi.fn(),
  restoreForget: vi.fn(),
  storeVerificationReports: vi.fn(),
  ConversationGoneError: class extends Error {},
  ForgetConflictError: class extends Error {},
  ForgetHttpError: class extends Error {},
}))

import { renderHook } from "@testing-library/react"
import { useConversations } from "@/hooks/use-conversations"
import * as api from "@/lib/api"
import type { HallucinationReport } from "@/lib/types"

const REPORT: HallucinationReport = {
  conversation_id: "c1", timestamp: "t", skipped: false,
  claims: [{ claim: "A", status: "verified", similarity: 0.9 }],
  summary: { total: 1, verified: 1, unverified: 0, uncertain: 0 },
}
const MODEL = "openrouter/openai/gpt-4o-mini"

function seed(extra: Record<string, unknown>) {
  localStorage.setItem("cerid-conversations", JSON.stringify([{
    id: "c1", title: "c1", model: MODEL, createdAt: 1, updatedAt: 2,
    messages: [{ id: "a1", role: "assistant", content: "x", timestamp: 1 }, { id: "a2", role: "assistant", content: "y", timestamp: 2 }],
    ...extra,
  }]))
}

const stored = () => JSON.parse(localStorage.getItem("cerid-conversations") ?? "[]")[0]

describe("reports kept inside conversations go to the server once (forget spec §6.2)", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("hands them over and drops the copies the server took", async () => {
    seed({ verificationReports: { a1: REPORT, a2: REPORT } })
    vi.mocked(api.storeVerificationReports).mockResolvedValue(["a1"])
    renderHook(() => useConversations())
    await vi.waitFor(() => expect(stored().verificationReports).toEqual({ a2: REPORT }))
    expect(api.storeVerificationReports).toHaveBeenCalledWith("c1", { a1: REPORT, a2: REPORT })
  })

  it("keeps every copy when the server cannot take them", async () => {
    seed({ verificationReports: { a1: REPORT } })
    vi.mocked(api.storeVerificationReports).mockRejectedValue(new Error("offline"))
    renderHook(() => useConversations())
    await vi.waitFor(() => expect(api.storeVerificationReports).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 10))
    expect(stored().verificationReports).toEqual({ a1: REPORT })
  })

  it("the legacy single report belonged to the last answer", async () => {
    seed({ verificationReport: REPORT })
    vi.mocked(api.storeVerificationReports).mockResolvedValue(["a2"])
    renderHook(() => useConversations())
    await vi.waitFor(() => expect(stored().verificationReport).toBeUndefined())
    expect(api.storeVerificationReports).toHaveBeenCalledWith("c1", { a2: REPORT })
    expect(stored().verificationReports).toBeUndefined()
  })

  it("asks nothing of the server for a conversation without reports", () => {
    seed({})
    renderHook(() => useConversations())
    expect(api.storeVerificationReports).not.toHaveBeenCalled()
  })
})
