// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * formatTimestamp must never render "Invalid Date" — it should produce
 * either a real formatted date OR the em-dash placeholder.
 *
 * The 2026-04-23 incident: backend renamed manifest.last_exported_at →
 * manifest.timestamp, the reader was never updated, `new Date(undefined)`
 * returned an Invalid Date, and the System tab showed the literal text
 * "Invalid Date" to the user.
 */
import { describe, it, expect } from "vitest"
import { formatTimestamp } from "@/components/settings/sync-section"

describe("formatTimestamp (Invalid Date guard)", () => {
  it("returns em-dash for undefined", () => {
    expect(formatTimestamp(undefined)).toBe("—")
  })

  it("returns em-dash for null", () => {
    expect(formatTimestamp(null)).toBe("—")
  })

  it("returns em-dash for empty string", () => {
    expect(formatTimestamp("")).toBe("—")
  })

  it("formats a valid ISO 8601 timestamp", () => {
    const out = formatTimestamp("2026-02-22T19:00:35.608625+00:00")
    expect(out).not.toBe("Invalid Date")
    expect(out).not.toBe("—")
    // Locale-dependent format — assert it contains the year/month signal.
    expect(out).toMatch(/Feb/)
    expect(out).toMatch(/22/)
  })

  it("returns the raw input (NOT 'Invalid Date') when parse fails", () => {
    // Garbage input should fall back to the input string, never literal "Invalid Date".
    const out = formatTimestamp("not-a-date-string")
    expect(out).not.toBe("Invalid Date")
    expect(out).toBe("not-a-date-string")
  })
})
