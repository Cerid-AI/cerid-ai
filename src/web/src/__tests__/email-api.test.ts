// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

vi.stubEnv("VITE_MCP_URL", "http://test-mcp:8888")

const { configureEmail, fetchEmailStatus, pollEmailNow, deleteEmailSource } = await import("@/lib/api/email")

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

const CFG = {
  host: "imap.example.com",
  port: 993,
  user: "me@example.com",
  password: "secret", // pragma: allowlist secret
  folder: "INBOX",
  poll_interval: 15,
}

beforeEach(() => vi.stubGlobal("fetch", mockFetch({})))
afterEach(() => vi.restoreAllMocks())

describe("email api client", () => {
  it("configureEmail POSTs the config to /data-sources/email/configure", async () => {
    const f = mockFetch({ status: "configured", host: CFG.host, user: CFG.user })
    vi.stubGlobal("fetch", f)
    const res = await configureEmail(CFG)
    expect(res.status).toBe("configured")
    const [url, init] = f.mock.calls[0]
    expect(String(url)).toContain("/data-sources/email/configure")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body)).toMatchObject({ host: CFG.host, port: 993 })
  })

  it("fetchEmailStatus GETs the status endpoint", async () => {
    const f = mockFetch({ last_poll: "2026-06-19T00:00:00Z", messages_ingested: 4, errors: [] })
    vi.stubGlobal("fetch", f)
    const res = await fetchEmailStatus()
    expect(res.messages_ingested).toBe(4)
    expect(String(f.mock.calls[0][0])).toContain("/data-sources/email/status")
  })

  it("pollEmailNow POSTs to /poll-now", async () => {
    const f = mockFetch({ status: "ok", messages: 2 })
    vi.stubGlobal("fetch", f)
    const res = await pollEmailNow()
    expect(res.messages).toBe(2)
    const [url, init] = f.mock.calls[0]
    expect(String(url)).toContain("/data-sources/email/poll-now")
    expect(init.method).toBe("POST")
  })

  it("deleteEmailSource DELETEs the email source", async () => {
    const f = mockFetch({ status: "deleted" })
    vi.stubGlobal("fetch", f)
    const res = await deleteEmailSource()
    expect(res.status).toBe("deleted")
    expect(f.mock.calls[0][1].method).toBe("DELETE")
  })

  it("throws a descriptive error on a failed configure", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "login failed" }, 422))
    await expect(configureEmail(CFG)).rejects.toThrow(/login failed/)
  })
})
