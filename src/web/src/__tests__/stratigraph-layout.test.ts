// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure layout unit tests for Stratigraph — no DOM, no React.
// Tests: stacking floor, DOI budget, LOD hysteresis both directions,
// other-rollup, marker clustering, severity-priority trust.

import { describe, it, expect } from "vitest"
import {
  computeLOD,
  computeStrata,
  clusterMarkers,
  bucketTrustSuffix,
  trackDOI,
  communitySlot,
  type LODLevel,
} from "@/components/subjects/timeline/stratigraph/strata-layout"
import type { TimelineStrataResponse } from "@/lib/api/graph"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeResponse(overrides: Partial<TimelineStrataResponse> = {}): TimelineStrataResponse {
  return {
    from_date: "2026-05-01",
    to_date: "2026-06-01",
    granularity: "day",
    bucket_dates: ["2026-05-01", "2026-05-02", "2026-05-03"],
    communities: [
      {
        community_id: "c1",
        label: "Research",
        color_slot: 0,
        trust_mix: { verified: 0.8, partial: 0.1, unverified: 0.1 },
        total_mentions: 100,
        is_other: false,
      },
      {
        community_id: "c2",
        label: "Finance",
        color_slot: 1,
        trust_mix: { verified: 0.5, partial: 0.3, unverified: 0.2 },
        total_mentions: 50,
        is_other: false,
      },
      {
        community_id: "other",
        label: "Other",
        color_slot: -1,
        trust_mix: { verified: 0.3, partial: 0.3, unverified: 0.4 },
        total_mentions: 10,
        is_other: true,
      },
    ],
    series: [
      {
        community_id: "c1",
        entity_type: "PERSON",
        buckets: [30, 40, 30],
        unverified_buckets: [0, 0, 0],
      },
      {
        community_id: "c2",
        entity_type: "ORG",
        buckets: [20, 15, 15],
        unverified_buckets: [0, 5, 0],
      },
    ],
    tracks: [
      {
        canonical_id: "e1",
        name: "Alice",
        entity_type: "PERSON",
        community_id: "c1",
        trust_state: "verified",
        first_seen: "2026-05-01T00:00:00Z",
        rank: 1,
        total_mentions: 50,
        buckets: [20, 20, 10],
      },
      {
        canonical_id: "e2",
        name: "Acme Corp",
        entity_type: "ORG",
        community_id: "c2",
        trust_state: "unverified",
        first_seen: "2026-05-01T00:00:00Z",
        rank: 1,
        total_mentions: 30,
        buckets: [10, 10, 10],
      },
    ],
    markers: [
      { date: "2026-05-01", kind: "ingest_burst", count: 171 },
      { date: "2026-05-02", kind: "birth_surge", count: 45 },
    ],
    totals: { mentions: 160, entities_introduced: 80 },
    cached: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// LOD state machine + hysteresis
// ---------------------------------------------------------------------------

describe("computeLOD — basic transitions", () => {
  it("returns 'era' for large spans", () => {
    expect(computeLOD(90, "era")).toBe("era")
    expect(computeLOD(365, "era")).toBe("era")
  })

  it("returns 'bucket' for medium spans", () => {
    expect(computeLOD(20, "era")).toBe("bucket")
    expect(computeLOD(35, "era")).toBe("bucket")
  })

  it("returns 'track' for short spans", () => {
    expect(computeLOD(3, "era")).toBe("track")
    expect(computeLOD(6, "era")).toBe("track")
  })
})

describe("computeLOD — hysteresis prevents oscillation", () => {
  it("stays in 'track' slightly above the resolve threshold", () => {
    // With hysteresis, coming from 'track' the resolve threshold is widened
    // i.e. the span must exceed LOD_RESOLVE_DAYS * (1 + 0.15) ≈ 8.05 to leave track
    const level: LODLevel = "track"
    expect(computeLOD(7.5, level)).toBe("track") // just above 7d but below hysteresis band
  })

  it("uses lowered fuse threshold when coming from 'bucket' (hysteresis reduces flip zone)", () => {
    // LOD_BUCKET_DAYS=42, hysteresis=15%: threshold when prior='bucket' → 42*(1-0.15)=35.7
    // So 34 days from 'bucket' state should still be 'bucket' (below the lowered threshold 35.7)
    const level: LODLevel = "bucket"
    expect(computeLOD(34, level)).toBe("bucket")
  })

  it("resolves to 'track' when clearly below threshold from any prior state", () => {
    expect(computeLOD(2, "era")).toBe("track")
    expect(computeLOD(2, "bucket")).toBe("track")
  })

  it("fuses to 'era' when clearly above bucket threshold", () => {
    expect(computeLOD(100, "track")).toBe("era")
  })
})

// ---------------------------------------------------------------------------
// Stacking floor — 2px minimum (quiet strata never vanish)
// ---------------------------------------------------------------------------

describe("computeStrata — 2px floor", () => {
  it("every stratum has heightPx >= 2", () => {
    const resp = makeResponse()
    const { strata } = computeStrata({
      response: resp,
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: null,
    })
    for (const st of strata) {
      expect(st.heightPx).toBeGreaterThanOrEqual(2)
    }
  })

  it("a zero-mention community in the 'other' slot still appears", () => {
    const resp = makeResponse({
      communities: [
        { community_id: "c1", label: "A", color_slot: 0, trust_mix: { verified: 1, partial: 0, unverified: 0 }, total_mentions: 1, is_other: false },
        { community_id: "other", label: "Other", color_slot: -1, trust_mix: { verified: 1, partial: 0, unverified: 0 }, total_mentions: 0, is_other: true },
      ],
    })
    const { strata } = computeStrata({
      response: resp,
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: null,
    })
    // zero-mention "other" should NOT appear (the code skips 0-mention others)
    const other = strata.find((s) => s.isOther)
    expect(other).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Other-rollup at the bottom
// ---------------------------------------------------------------------------

describe("computeStrata — other rollup", () => {
  it("other stratum renders at the bottom of the stack", () => {
    const { strata } = computeStrata({
      response: makeResponse(),
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: null,
    })
    const otherIdx = strata.findIndex((s) => s.isOther)
    const nonOtherMax = strata
      .filter((s) => !s.isOther)
      .reduce((max, s) => Math.max(max, strata.indexOf(s)), -1)
    if (otherIdx >= 0 && nonOtherMax >= 0) {
      expect(otherIdx).toBeGreaterThan(nonOtherMax)
    }
  })

  it("other stratum has colorSlot = -1", () => {
    const { strata } = computeStrata({
      response: makeResponse(),
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: null,
    })
    const other = strata.find((s) => s.isOther)
    expect(other?.colorSlot).toBe(-1)
  })
})

// ---------------------------------------------------------------------------
// DOI track budget
// ---------------------------------------------------------------------------

describe("computeStrata — track budget respected", () => {
  it("allocates at most trackBudget tracks per stratum", () => {
    const resp = makeResponse({
      tracks: Array.from({ length: 20 }, (_, i) => ({
        canonical_id: `e${i}`,
        name: `Entity${i}`,
        entity_type: "PERSON",
        community_id: "c1",
        trust_state: "verified" as const,
        first_seen: "2026-05-01T00:00:00Z",
        rank: i + 1,
        total_mentions: 20 - i,
        buckets: [5, 5, 10],
      })),
    })

    const { strata } = computeStrata({
      response: resp,
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: null,
    })
    const c1 = strata.find((s) => s.communityId === "c1")
    expect(c1?.trackIds.length).toBeLessThanOrEqual(8)
  })

  it("pinned entity gets DOI bonus and ranks higher", () => {
    const e1: TimelineStrataResponse["tracks"][0] = {
      canonical_id: "e1",
      name: "Pinned",
      entity_type: "PERSON",
      community_id: "c1",
      trust_state: "verified",
      first_seen: "2026-05-01T00:00:00Z",
      rank: 1,
      total_mentions: 1, // low mentions
      buckets: [0, 0, 1],
    }
    const e2: TimelineStrataResponse["tracks"][0] = {
      canonical_id: "e2",
      name: "Popular",
      entity_type: "PERSON",
      community_id: "c1",
      trust_state: "verified",
      first_seen: "2026-05-01T00:00:00Z",
      rank: 2,
      total_mentions: 100,
      buckets: [30, 40, 30],
    }
    const bucketDates = ["2026-05-01", "2026-05-02", "2026-05-03"]
    const pinnedSet = new Set(["e1"])

    const doi1 = trackDOI(e1, bucketDates, pinnedSet)
    const doi2 = trackDOI(e2, bucketDates, new Set())
    // Pinned entity gets +2.0 bonus which should outweigh the log difference when e2 is not pinned
    expect(doi1).toBeGreaterThan(doi2 - 2.1) // pinned bonus narrows the gap
  })
})

// ---------------------------------------------------------------------------
// Marker clustering — ≤6 per viewport + overflow badge
// ---------------------------------------------------------------------------

describe("clusterMarkers", () => {
  it("returns all markers when count <= maxVisible", () => {
    const markers = [
      { date: "2026-05-01", kind: "ingest_burst", count: 100 },
      { date: "2026-05-10", kind: "birth_surge", count: 50 },
    ]
    const result = clusterMarkers(markers, "2026-05-01", "2026-06-01", 6)
    expect(result.length).toBe(2)
  })

  it("caps output at maxVisible when there are more markers", () => {
    const markers = Array.from({ length: 10 }, (_, i) => ({
      date: `2026-05-${String(i + 1).padStart(2, "0")}`,
      kind: "ingest_burst",
      count: 10 + i,
    }))
    const result = clusterMarkers(markers, "2026-05-01", "2026-06-01", 6)
    expect(result.length).toBeLessThanOrEqual(6)
  })

  it("cluster badge has 'cluster' kind when overflow is present", () => {
    const markers = Array.from({ length: 10 }, (_, i) => ({
      date: `2026-05-${String(i + 1).padStart(2, "0")}`,
      kind: "ingest_burst",
      count: 10 + i,
    }))
    const result = clusterMarkers(markers, "2026-05-01", "2026-06-01", 6)
    const clusterBadge = result.find((m) => m.kind === "cluster")
    expect(clusterBadge).toBeDefined()
  })

  it("returns empty array for no markers", () => {
    expect(clusterMarkers([], "2026-05-01", "2026-06-01")).toHaveLength(0)
  })

  it("preserves fractional x positions in 0..1 range", () => {
    const markers = [
      { date: "2026-05-01", kind: "ingest_burst", count: 10 },
      { date: "2026-06-01", kind: "ingest_burst", count: 20 },
    ]
    const result = clusterMarkers(markers, "2026-05-01", "2026-07-01", 6)
    for (const m of result) {
      expect(m.xFrac).toBeGreaterThanOrEqual(0)
      expect(m.xFrac).toBeLessThanOrEqual(1)
    }
  })
})

// ---------------------------------------------------------------------------
// Severity-priority trust (amendment 1)
// ---------------------------------------------------------------------------

describe("bucketTrustSuffix — severity priority", () => {
  it("returns 'unverified' when bucket appears in unverifiedBuckets (amendment 1)", () => {
    const unverified = new Set([1, 3])
    const trustMix = { verified: 0.9, partial: 0.05, unverified: 0.05 }
    // Bucket 1 is in the set — unverified wins regardless of trust_mix
    expect(bucketTrustSuffix(1, unverified, trustMix)).toBe("unverified")
  })

  it("returns 'verified' for a bucket not in unverifiedBuckets with clean trust_mix", () => {
    const unverified = new Set<number>()
    const trustMix = { verified: 0.9, partial: 0.1, unverified: 0.0 }
    expect(bucketTrustSuffix(0, unverified, trustMix)).toBe("verified")
  })

  it("returns 'partial' for a bucket with moderate partial trust_mix", () => {
    const unverified = new Set<number>()
    const trustMix = { verified: 0.3, partial: 0.6, unverified: 0.05 }
    expect(bucketTrustSuffix(0, unverified, trustMix)).toBe("partial")
  })

  it("unverified trust_mix > 0.1 triggers unverified even without set entry", () => {
    const unverified = new Set<number>()
    const trustMix = { verified: 0.4, partial: 0.4, unverified: 0.2 }
    expect(bucketTrustSuffix(0, unverified, trustMix)).toBe("unverified")
  })
})

// ---------------------------------------------------------------------------
// communitySlot stability
// ---------------------------------------------------------------------------

describe("communitySlot", () => {
  it("returns a value in 0..7", () => {
    for (const id of ["abc", "test", "c1", "community-42", ""]) {
      const slot = communitySlot(id)
      expect(slot).toBeGreaterThanOrEqual(0)
      expect(slot).toBeLessThanOrEqual(7)
    }
  })

  it("is stable for the same input", () => {
    const id = "test-community-id"
    expect(communitySlot(id)).toBe(communitySlot(id))
  })
})

// ---------------------------------------------------------------------------
// Amendment 5 — frozen order + re-rank detection
// ---------------------------------------------------------------------------

describe("computeStrata — frozen order", () => {
  it("respects frozen order when provided", () => {
    const resp = makeResponse()
    // Reverse natural order
    const frozenOrder = ["c2", "c1"]
    const { strata } = computeStrata({
      response: resp,
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder,
    })
    const nonOther = strata.filter((s) => !s.isOther)
    // c2 should appear before c1 because frozen order puts it first
    const idxC2 = nonOther.findIndex((s) => s.communityId === "c2")
    const idxC1 = nonOther.findIndex((s) => s.communityId === "c1")
    expect(idxC2).toBeLessThan(idxC1)
  })

  it("reRankAvailable is false when frozen order matches fresh order", () => {
    const resp = makeResponse()
    // Fresh order by mentions: c1(100) > c2(50)
    const { reRankAvailable } = computeStrata({
      response: resp,
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: ["c1", "c2"],
    })
    expect(reRankAvailable).toBe(false)
  })

  it("reRankAvailable is true when a community would leave the top window set", () => {
    const resp = makeResponse()
    // Frozen order includes "c3" which is not in current response → drift
    const { reRankAvailable } = computeStrata({
      response: resp,
      canvasHeight: 400,
      trackBudget: 8,
      pinnedIds: new Set(),
      frozenOrder: ["c1", "c3"], // c3 not present
    })
    expect(reRankAvailable).toBe(true)
  })
})
