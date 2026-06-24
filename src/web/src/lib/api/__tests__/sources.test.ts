import { describe, it, expect, vi, beforeEach } from "vitest"
import { listIngestionSources } from "@/lib/api/sources"

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
