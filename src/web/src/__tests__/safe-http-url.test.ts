// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"

import { safeHttpUrl } from "@/lib/kb-utils"

describe("safeHttpUrl", () => {
  it("passes through http and https URLs", () => {
    expect(safeHttpUrl("http://example.com/x")).toBe("http://example.com/x")
    expect(safeHttpUrl("https://example.com")).toBe("https://example.com")
  })

  it("rejects javascript:, data:, and vbscript: schemes", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull()
    expect(safeHttpUrl("JavaScript:alert(1)")).toBeNull()
    expect(safeHttpUrl("data:text/html,<script>alert(1)</script>")).toBeNull()
    expect(safeHttpUrl("vbscript:msgbox(1)")).toBeNull()
  })

  it("treats null/undefined/empty as no URL", () => {
    expect(safeHttpUrl(null)).toBeNull()
    expect(safeHttpUrl(undefined)).toBeNull()
    expect(safeHttpUrl("")).toBeNull()
  })

  it("resolves a same-origin relative path to an http(s) URL", () => {
    // jsdom origin is http(s); a relative path resolves to a safe scheme.
    expect(safeHttpUrl("/wiki/entity")).toBe("/wiki/entity")
  })

  it("returns null for unparseable garbage", () => {
    expect(safeHttpUrl("http://[bad")).toBeNull()
  })
})
