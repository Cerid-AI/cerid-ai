// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// GUI spec MUST-6 — fetchPlugins must deliver display_name and plugin_type
// regardless of which backend handler answered GET /plugins: the plugins
// router serves an array with `plugin_type`, while app.routers.health (which
// wins registration order in the real app) serves a dict of loader records
// spelling the same field `type`.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fetchPlugins } from "@/lib/api/settings"

beforeEach(() => vi.clearAllMocks())

describe("fetchPlugins normalization", () => {
  it("array shape (plugins router): passes display_name and plugin_type through", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        plugins: [{
          name: "apple_mail", display_name: "Apple Mail", plugin_type: "connector",
          version: "0.1.0", description: "", tier_required: "pro", enabled: false,
          status: "disabled", file_types: [], config_schema: null, capabilities: [],
        }],
        total: 1,
      }),
    }))
    const out = await fetchPlugins()
    expect(out.plugins).toHaveLength(1)
    expect(out.plugins[0].display_name).toBe("Apple Mail")
    expect(out.plugins[0].plugin_type).toBe("connector")
  })

  it("dict shape (health router loader records): maps `type` to plugin_type", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        plugins: {
          gmail: {
            name: "gmail", display_name: "Gmail", type: "connector",
            version: "0.1.0", description: "", tier: "pro",
          },
          ocr: {
            name: "ocr", display_name: "OCR", type: "parser",
            version: "1.0.0", description: "", tier: "community",
          },
        },
      }),
    }))
    const out = await fetchPlugins()
    const byName = Object.fromEntries(out.plugins.map((p) => [p.name, p]))
    expect(byName.gmail.plugin_type).toBe("connector")
    expect(byName.gmail.display_name).toBe("Gmail")
    expect(byName.ocr.plugin_type).toBe("parser")
  })
})
