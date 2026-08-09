import { describe, it, expect, vi, beforeEach } from "vitest"
import { listConnectors, startConnectorAuth } from "@/lib/api/connectors"

beforeEach(() => vi.clearAllMocks())

describe("connectors client", () => {
  it("lists connectors (unwraps .connectors)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ connectors: [{ slug: "gmail", display_name: "Gmail", auth_kind: "google_oauth" }] }) }))
    const out = await listConnectors()
    expect(out).toHaveLength(1); expect(out[0].slug).toBe("gmail")
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/connectors")
  })
  it("starts auth via POST", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ auth_kind: "google_oauth", auth_url: "https://x", instructions: "go" }) }))
    const r = await startConnectorAuth("gmail")
    expect(r.auth_url).toBe("https://x")
    const [url, opts] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toContain("/connectors/gmail/auth/start"); expect(opts?.method).toBe("POST")
  })
})
