// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { describe, it, expect } from "vitest"
import { buildTimeHistogram, parseCreatedAt, timeNodeState, bornBetween, type TimeFilter } from "./time-window"

const ENT = (id: string, created_at: string | null) => ({ id, created_at })

describe("parseCreatedAt", () => {
  it("parses an ISO date to epoch ms", () => {
    expect(parseCreatedAt("2026-01-15T00:00:00Z")).toBe(Date.parse("2026-01-15T00:00:00Z"))
  })
  it("returns null for null/empty/garbage", () => {
    expect(parseCreatedAt(null)).toBeNull()
    expect(parseCreatedAt(undefined)).toBeNull()
    expect(parseCreatedAt("not-a-date")).toBeNull()
  })
})

describe("buildTimeHistogram", () => {
  it("returns null when no entity has a valid created_at", () => {
    expect(buildTimeHistogram([ENT("a", null), ENT("b", "bad")], 10)).toBeNull()
  })
  it("spans min→max and bins dated entities", () => {
    const h = buildTimeHistogram(
      [ENT("a", "2026-01-01T00:00:00Z"), ENT("b", "2026-01-01T00:00:00Z"), ENT("c", "2026-02-01T00:00:00Z"), ENT("d", null)],
      4,
    )!
    expect(h.minMs).toBe(Date.parse("2026-01-01T00:00:00Z"))
    expect(h.maxMs).toBe(Date.parse("2026-02-01T00:00:00Z"))
    expect(h.buckets).toHaveLength(4)
    const total = h.buckets.reduce((s, b) => s + b.count, 0)
    expect(total).toBe(3) // the 3 dated entities; null skipped
    expect(h.buckets[0].count).toBe(2) // both Jan-01 land in the first bucket
    expect(h.buckets[h.buckets.length - 1].count).toBe(1) // Feb-01 lands in the last
  })
})

describe("timeNodeState", () => {
  const t = (iso: string) => Date.parse(iso)
  const created = "2026-01-15T00:00:00Z"

  it("is visible when no filter is active", () => {
    expect(timeNodeState(created, null)).toBe("visible")
  })

  it("undated nodes are always visible (honest — never hidden by time)", () => {
    const filter: TimeFilter = { window: [t("2020-01-01"), t("2021-01-01")], cursor: null }
    expect(timeNodeState(null, filter)).toBe("visible")
  })

  it("brush window: in-window visible, out-of-window dimmed (not hidden)", () => {
    const inWin: TimeFilter = { window: [t("2026-01-01"), t("2026-02-01")], cursor: null }
    expect(timeNodeState(created, inWin)).toBe("visible")
    const outWin: TimeFilter = { window: [t("2026-03-01"), t("2026-04-01")], cursor: null }
    expect(timeNodeState(created, outWin)).toBe("dim")
  })

  it("playback cursor: born-before visible, not-yet-born hidden", () => {
    expect(timeNodeState(created, { window: null, cursor: t("2026-02-01") })).toBe("visible")
    expect(timeNodeState(created, { window: null, cursor: t("2026-01-10") })).toBe("hidden")
  })
})

describe("bornBetween (A8 birth pulses)", () => {
  const t = (d: string) => Date.parse(`${d}T00:00:00Z`)
  const ents = [
    ENT("a", "2026-01-05T00:00:00Z"),
    ENT("b", "2026-01-10T00:00:00Z"),
    ENT("c", "2026-01-15T00:00:00Z"),
    ENT("d", null),
    ENT("e", "bad-date"),
  ]

  it("returns ids born in (prev, now]", () => {
    expect(bornBetween(ents, t("2026-01-05"), t("2026-01-10"))).toEqual(["b"])
    expect(bornBetween(ents, t("2026-01-01"), t("2026-01-15"))).toEqual(["a", "b", "c"])
  })

  it("excludes the prev boundary, includes the now boundary", () => {
    expect(bornBetween(ents, t("2026-01-10"), t("2026-01-15"))).toEqual(["c"])
  })

  it("null prev means everything up to now (first playback step)", () => {
    expect(bornBetween(ents, null, t("2026-01-05"))).toEqual(["a"])
  })

  it("undated/garbage entities never appear", () => {
    expect(bornBetween(ents, null, t("2026-12-31"))).toEqual(["a", "b", "c"])
  })

  it("caps the result deterministically", () => {
    expect(bornBetween(ents, null, t("2026-12-31"), 2)).toEqual(["a", "b"])
  })

  it("returns [] when nothing crosses", () => {
    expect(bornBetween(ents, t("2026-01-15"), t("2026-01-16"))).toEqual([])
  })
})
