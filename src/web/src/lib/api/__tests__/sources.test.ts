import { describe, it, expect, vi, beforeEach } from "vitest"
import { fetchWebhookUrl, listIngestionSources } from "@/lib/api/sources"

beforeEach(() => vi.clearAllMocks())

describe("listIngestionSources", () => {
  it("returns only ingestion kinds (drops external_api/plugin)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "folder:1", kind: "folder", display_name: "Notes" },
        { id: "x", kind: "external_api", display_name: "Wikipedia" },
        { id: "y", kind: "plugin", display_name: "Foo" },
        { id: "rss:1", kind: "rss", display_name: "HN" },
      ],
    }))
    const out = await listIngestionSources()
    expect(out.map((s) => s.kind).sort()).toEqual(["folder", "rss"])
  })
})

describe("fetchWebhookUrl", () => {
  it("POSTs: the token is shown in cleartext, so it never rides a cacheable GET", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "https://kb.example/sdk/v1/ingest/webhook/tok", require_hmac: true, curl_example: "curl" }),
    })
    vi.stubGlobal("fetch", fetchMock)
    const out = await fetchWebhookUrl("src-1")
    expect(out.require_hmac).toBe(true)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain("/sources/src-1/webhook-url")
    expect(init.method).toBe("POST")
  })
})
