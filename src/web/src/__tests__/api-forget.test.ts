// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")
vi.stubEnv("VITE_CERID_API_KEY", "")

const {
  previewForget, previewForgetItems, searchForgetAssist, forgetSubjects, restoreForget, emptyTrash, fetchTrash, fetchReceipts, fetchReceipt,
  ForgetConflictError,
} = await import("@/lib/api")

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

function call(fetchMock: ReturnType<typeof vi.fn>) {
  const [url, init] = fetchMock.mock.calls[0]
  return { url: String(url), method: (init?.method ?? "GET") as string, body: init?.body ? JSON.parse(init.body) : undefined }
}

beforeEach(() => vi.stubGlobal("fetch", mockFetch({})))
afterEach(() => vi.restoreAllMocks())

describe("forget API client", () => {
  it("previews a conversation", async () => {
    const f = mockFetch({ subject: { kind: "conversation", id: "c1" }, title: "", groups: [], derived_facts: 0, out_of_reach: [] })
    vi.stubGlobal("fetch", f)
    const pv = await previewForget("c1")
    expect(call(f)).toEqual({ url: "http://test-mcp:8888/forget/preview", method: "POST", body: { kind: "conversation", id: "c1" } })
    expect(pv.subject?.id).toBe("c1")
  })

  it("previews a selection of documents and passages", async () => {
    const f = mockFetch({ subject: null, title: "", groups: [], derived_facts: 0, notes: [], out_of_reach: [] })
    vi.stubGlobal("fetch", f)
    const subjects = [{ kind: "artifact" as const, id: "a".repeat(64) }, { kind: "chunk" as const, id: `${"b".repeat(64)}_0123456789abcdef` }]
    const pv = await previewForgetItems(subjects)
    expect(call(f)).toEqual({ url: "http://test-mcp:8888/forget/preview", method: "POST", body: { subjects } })
    expect(pv.subject).toBeNull()
  })

  it("forgets subjects in the chosen mode", async () => {
    const f = mockFetch({ forget_id: "fg_1", state: "trashed", receipt: null })
    vi.stubGlobal("fetch", f)
    const subjects = [{ kind: "conversation" as const, id: "c1" }, { kind: "artifact" as const, id: "a1" }]
    const r = await forgetSubjects(subjects, "permanent")
    expect(call(f)).toEqual({ url: "http://test-mcp:8888/forget", method: "POST", body: { subjects, mode: "permanent", source: "api" } })
    expect(r.forget_id).toBe("fg_1")
  })

  it("names the assistant as the source of its forgets", async () => {
    const f = mockFetch({ forget_id: "fg_2", state: "trashed", receipt: null })
    vi.stubGlobal("fetch", f)
    await forgetSubjects([{ kind: "artifact", id: "a1" }], "trash", "agent")
    expect(call(f).body).toMatchObject({ source: "agent" })
  })

  it("asks the assistant, and only sends allow_cloud when the user agreed", async () => {
    const f = mockFetch({ status: "grouped", model: "local", reason: "", cloud_model: "", scope: "x", total: 0, groups: [] })
    vi.stubGlobal("fetch", f)
    await searchForgetAssist("my old 401k")
    expect(call(f)).toEqual({ url: "http://test-mcp:8888/forget/assist/search", method: "POST", body: { scope: "my old 401k", allow_cloud: false } })
    await searchForgetAssist("my old 401k", true)
    expect(JSON.parse(f.mock.calls[1][1].body)).toEqual({ scope: "my old 401k", allow_cloud: true })
  })

  it("restores, and a 409 is a ForgetConflictError", async () => {
    const ok = mockFetch({ restored: 1 })
    vi.stubGlobal("fetch", ok)
    expect(await restoreForget("fg_1")).toEqual({ restored: 1 })
    expect(call(ok)).toMatchObject({ url: "http://test-mcp:8888/forget/fg_1/restore", method: "POST" })
    vi.stubGlobal("fetch", mockFetch({ detail: "purge started" }, 409))
    await expect(restoreForget("fg_1")).rejects.toBeInstanceOf(ForgetConflictError)
  })

  it("empties the trash and lists trash and receipts", async () => {
    const e = mockFetch({ purged: ["fg_1"] })
    vi.stubGlobal("fetch", e)
    expect(await emptyTrash()).toEqual({ purged: ["fg_1"] })
    expect(call(e)).toMatchObject({ url: "http://test-mcp:8888/forget/trash/empty", method: "POST" })

    const t = mockFetch({ items: [{ forget_id: "fg_1" }] })
    vi.stubGlobal("fetch", t)
    expect(await fetchTrash()).toEqual([{ forget_id: "fg_1" }])
    expect(call(t)).toMatchObject({ url: "http://test-mcp:8888/forget/trash", method: "GET" })

    const r = mockFetch({ items: [{ forget_id: "fg_2" }] })
    vi.stubGlobal("fetch", r)
    expect(await fetchReceipts()).toEqual([{ forget_id: "fg_2" }])
    expect(call(r).url).toBe("http://test-mcp:8888/forget/receipts")

    const one = mockFetch({ forget_id: "fg_2", adapters: {} })
    vi.stubGlobal("fetch", one)
    expect((await fetchReceipt("fg_2")).forget_id).toBe("fg_2")
    expect(call(one).url).toBe("http://test-mcp:8888/forget/receipts/fg_2")
  })

  it("throws on a server error", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "boom" }, 500))
    await expect(fetchTrash()).rejects.toThrow()
  })
})
