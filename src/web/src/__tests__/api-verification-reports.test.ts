// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_CERID_API_KEY", "")

const { fetchVerificationReports, storeVerificationReports, streamVerification, submitClaimFeedback } = await import("@/lib/api")

function reply(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status < 300, status, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)),
  })
}

function sent(): { url: string; method: string; body: Record<string, unknown> | undefined } {
  const [url, init] = vi.mocked(fetch).mock.calls[0]
  return { url: String(url), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined }
}

beforeEach(() => vi.stubGlobal("fetch", reply({})))
afterEach(() => vi.restoreAllMocks())

describe("verification reports live on the server, per message", () => {
  it("fetches a conversation's reports by message", async () => {
    vi.stubGlobal("fetch", reply({ reports: { m1: { claims: [] } } }))
    expect(await fetchVerificationReports("c 1")).toEqual({ m1: { claims: [] } })
    expect(sent().url).toBe("http://test-mcp:8888/verification/c%201/reports")
  })

  it("an error is an error, not an empty set of reports", async () => {
    vi.stubGlobal("fetch", reply({ detail: "down" }, 500))
    await expect(fetchVerificationReports("c1")).rejects.toThrow()
  })

  it("hands kept reports over and learns which the server holds", async () => {
    vi.stubGlobal("fetch", reply({ stored: ["m1"] }))
    expect(await storeVerificationReports("c1", { m1: {} as never })).toEqual(["m1"])
    expect(sent()).toMatchObject({ method: "POST", url: "http://test-mcp:8888/verification/c1/reports", body: { reports: { m1: {} } } })
  })

  it("names the message a verification and its feedback belong to", async () => {
    streamVerification("text", "c1", undefined, undefined, undefined, undefined, undefined, undefined, "m-7")
    expect(sent().body).toMatchObject({ conversation_id: "c1", message_id: "m-7" })
    vi.stubGlobal("fetch", reply({ status: "ok" }))
    await submitClaimFeedback("c1", 0, true, "m-7")
    expect(sent().body).toMatchObject({ conversation_id: "c1", claim_index: 0, message_id: "m-7" })
  })
})
