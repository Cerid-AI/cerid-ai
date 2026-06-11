// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { organizeByDomain, organizeWithPinned } from "./organize"
import type { EntitySummary } from "@/lib/types/wiki"
import type { DomainCount } from "@/lib/api/domains"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeEntity(slug: string, domain: string | null = null): EntitySummary {
  return {
    slug,
    name: slug.charAt(0).toUpperCase() + slug.slice(1),
    entity_type: "ORG",
    summary_preview: null,
    related_count: 0,
    recent_activity_score: 0,
    last_updated_at: null,
    primary_domain: domain,
  }
}

function makeDomainCount(name: string, entityCount: number): DomainCount {
  return {
    name,
    icon: null,
    description: null,
    in_taxonomy: false,
    artifact_count: entityCount * 3,
    entity_count: entityCount,
    sub_categories: [],
  }
}

// Live distribution from grounding data: research=1529, general=849, coding=496,
// then thin tail.
const LIVE_DOMAIN_INDEX: DomainCount[] = [
  makeDomainCount("research", 1529),
  makeDomainCount("general", 849),
  makeDomainCount("coding", 496),
  makeDomainCount("finance", 42),
  makeDomainCount("projects", 18),
  makeDomainCount("personal", 5),
]

// ---------------------------------------------------------------------------
// organizeByDomain
// ---------------------------------------------------------------------------

describe("organizeByDomain — basic grouping", () => {
  it("groups entities by primary_domain", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "coding"),
      makeEntity("c", "research"),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    const domainNames = sections.map((s) => s.domain)
    expect(domainNames).toContain("research")
    expect(domainNames).toContain("coding")
    expect(sections.find((s) => s.domain === "research")?.entities).toHaveLength(2)
    expect(sections.find((s) => s.domain === "coding")?.entities).toHaveLength(1)
  })

  it("null-domain entities land in 'Other' section last", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", null),
      makeEntity("c", null),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    expect(sections[sections.length - 1].domain).toBeNull()
    expect(sections[sections.length - 1].label).toBe("Other")
    expect(sections[sections.length - 1].entities).toHaveLength(2)
  })

  it("sections are ordered by domainIndex entity_count desc", () => {
    const entities = [
      makeEntity("a", "coding"),
      makeEntity("b", "research"),
      makeEntity("c", "general"),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    const order = sections.map((s) => s.domain)
    // research(1529) before general(849) before coding(496)
    expect(order.indexOf("research")).toBeLessThan(order.indexOf("general"))
    expect(order.indexOf("general")).toBeLessThan(order.indexOf("coding"))
  })

  it("runtime-minted domain not in index falls after indexed domains", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "boardroom_foundation"),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    // "research" is in index, "boardroom_foundation" is not → research first
    expect(sections[0].domain).toBe("research")
    expect(sections.some((s) => s.domain === "boardroom_foundation")).toBe(true)
  })

  it("section count reflects domainIndex entity_count, not result set size", () => {
    const entities = [makeEntity("a", "research"), makeEntity("b", "research")]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    const researchSection = sections.find((s) => s.domain === "research")!
    // count from domainIndex, not entities.length
    expect(researchSection.count).toBe(1529)
    expect(researchSection.entities).toHaveLength(2)
  })

  it("empty entity list produces zero sections", () => {
    const { sections } = organizeByDomain([], LIVE_DOMAIN_INDEX)
    expect(sections).toHaveLength(0)
  })
})

describe("organizeByDomain — headerless collapse (A7)", () => {
  it("headerless=true when only one section", () => {
    const entities = [makeEntity("a", "research"), makeEntity("b", "research")]
    const { headerless } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    expect(headerless).toBe(true)
  })

  it("headerless=true for all-null domains (pre-job state)", () => {
    const entities = [makeEntity("a", null), makeEntity("b", null)]
    const { headerless } = organizeByDomain(entities, [])
    expect(headerless).toBe(true)
  })

  it("headerless=false when multiple distinct domains", () => {
    const entities = [makeEntity("a", "research"), makeEntity("b", "coding")]
    const { headerless } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    expect(headerless).toBe(false)
  })
})

describe("organizeByDomain — per-section cap + overflow", () => {
  it("cap limits visible entities and reports overflow count", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "research"),
      makeEntity("c", "research"),
      makeEntity("d", "research"),
      makeEntity("e", "research"),
      makeEntity("f", "research"),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX, { cap: 5 })
    const sec = sections.find((s) => s.domain === "research")!
    expect(sec.entities).toHaveLength(5)
    expect(sec.overflow).toBe(1)
  })

  it("no overflow when entities <= cap", () => {
    const entities = [makeEntity("a", "research"), makeEntity("b", "research")]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX, { cap: 5 })
    const sec = sections.find((s) => s.domain === "research")!
    expect(sec.overflow).toBe(0)
  })

  it("cap=undefined shows all entities", () => {
    const many = Array.from({ length: 20 }, (_, i) => makeEntity(`e${i}`, "research"))
    const { sections } = organizeByDomain(many, LIVE_DOMAIN_INDEX)
    const sec = sections.find((s) => s.domain === "research")!
    expect(sec.entities).toHaveLength(20)
    expect(sec.overflow).toBe(0)
  })
})

describe("organizeByDomain — excludeSlugs", () => {
  it("excludeSlugs removes entities from sections", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "research"),
      makeEntity("c", "coding"),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX, {
      excludeSlugs: new Set(["a", "c"]),
    })
    const research = sections.find((s) => s.domain === "research")
    // "a" excluded; "b" remains
    expect(research?.entities.map((e) => e.slug)).toEqual(["b"])
    // coding section gone entirely ("c" excluded)
    expect(sections.find((s) => s.domain === "coding")).toBeUndefined()
  })
})

describe("organizeByDomain — flatItems", () => {
  it("flatItems empty for single-section (headerless)", () => {
    const entities = [makeEntity("a", "research")]
    const { flatItems } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    expect(flatItems).toHaveLength(0)
  })

  it("flatItems includes headers + entities for multi-section", () => {
    const entities = [makeEntity("a", "research"), makeEntity("b", "coding")]
    const { flatItems } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    const headers = flatItems.filter((fi) => fi.isHeader)
    const rows = flatItems.filter((fi) => !fi.isHeader)
    expect(headers).toHaveLength(2)
    expect(rows).toHaveLength(2)
  })

  it("headers are skippable — entity rows are directly indexable (no headers in entities list)", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "coding"),
      makeEntity("c", "coding"),
    ]
    const { flatItems } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    const entityItems = flatItems.filter((fi) => !fi.isHeader)
    expect(entityItems).toHaveLength(3)
    // Each entity item has an entity field
    for (const item of entityItems) {
      if (!item.isHeader) {
        expect(item.entity.slug).toBeTruthy()
      }
    }
  })
})

describe("organizeByDomain — skew test (1529 vs thin tail)", () => {
  it("research appears first in the live distribution", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "general"),
      makeEntity("c", "coding"),
      makeEntity("d", "finance"),
      makeEntity("e", "projects"),
    ]
    const { sections } = organizeByDomain(entities, LIVE_DOMAIN_INDEX)
    expect(sections[0].domain).toBe("research")
  })

  it("per-section cap 5 prevents research from dominating when capped", () => {
    const researchEntities = Array.from({ length: 20 }, (_, i) =>
      makeEntity(`r${i}`, "research"),
    )
    const codingEntities = Array.from({ length: 3 }, (_, i) =>
      makeEntity(`c${i}`, "coding"),
    )
    const { sections } = organizeByDomain(
      [...researchEntities, ...codingEntities],
      LIVE_DOMAIN_INDEX,
      { cap: 5 },
    )
    const research = sections.find((s) => s.domain === "research")!
    const coding = sections.find((s) => s.domain === "coding")!
    expect(research.entities).toHaveLength(5)
    expect(research.overflow).toBe(15)
    // Coding section still visible despite thin tail
    expect(coding.entities).toHaveLength(3)
  })
})

// ---------------------------------------------------------------------------
// organizeWithPinned
// ---------------------------------------------------------------------------

describe("organizeWithPinned — Best Matches + de-dup", () => {
  it("pins the first pinCount entities in server relevance order", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "general"),
      makeEntity("c", "coding"),
      makeEntity("d", "research"),
      makeEntity("e", "coding"),
      makeEntity("f", "finance"),
    ]
    const { pinned } = organizeWithPinned(entities, LIVE_DOMAIN_INDEX, { pinCount: 3 })
    expect(pinned.entities.map((e) => e.slug)).toEqual(["a", "b", "c"])
  })

  it("de-dups Best Matches from domain sections when total <= dedupeThreshold", () => {
    const entities = [
      makeEntity("a", "research"),
      makeEntity("b", "coding"),
      makeEntity("c", "research"),
    ]
    const { pinned, rest } = organizeWithPinned(entities, LIVE_DOMAIN_INDEX, {
      pinCount: 2,
      dedupeThreshold: 10,
    })
    // Pinned: a, b
    expect(pinned.entities.map((e) => e.slug)).toEqual(["a", "b"])
    // Rest should not contain a or b (de-dup applied)
    const restSlugs = rest.sections.flatMap((s) => s.entities.map((e) => e.slug))
    expect(restSlugs).not.toContain("a")
    expect(restSlugs).not.toContain("b")
    // c is not in Best Matches, so it appears in its domain section
    expect(restSlugs).toContain("c")
  })

  it("does NOT de-dup when total > dedupeThreshold", () => {
    const entities = Array.from({ length: 12 }, (_, i) =>
      makeEntity(`e${i}`, i % 2 === 0 ? "research" : "coding"),
    )
    const { pinned, rest } = organizeWithPinned(entities, LIVE_DOMAIN_INDEX, {
      pinCount: 5,
      dedupeThreshold: 10,
    })
    // 12 entities > threshold of 10 → no de-dup
    const pinnedSlugs = new Set(pinned.entities.map((e) => e.slug))
    const restSlugs = new Set(rest.sections.flatMap((s) => s.entities.map((e) => e.slug)))
    // Some pinned entities appear in rest too
    const overlap = [...pinnedSlugs].filter((s) => restSlugs.has(s))
    expect(overlap.length).toBeGreaterThan(0)
  })

  it("handles empty entity list gracefully", () => {
    const { pinned, rest } = organizeWithPinned([], LIVE_DOMAIN_INDEX)
    expect(pinned.entities).toHaveLength(0)
    expect(rest.sections).toHaveLength(0)
  })
})
