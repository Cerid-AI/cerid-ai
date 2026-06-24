// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ST12 — KB maintenance actions belong under Knowledge, not System. The
 * rebuild / rescore / regenerate / clear-domain controls operate on the
 * knowledge base, so they live in a `knowledge.maintenance` group now.
 */

import { describe, expect, it } from "vitest"
import { SETTINGS_REGISTRY, getDef } from "@/lib/settings-registry"

const MAINTENANCE_IDS = [
  "knowledge.maintenance.rebuildIndexes",
  "knowledge.maintenance.rescore",
  "knowledge.maintenance.regenerateSummaries",
  "knowledge.maintenance.clearDomain",
]

describe("KB maintenance relocation (ST12)", () => {
  it.each(MAINTENANCE_IDS)("%s is a knowledge/maintenance action", (id) => {
    const def = getDef(id)
    expect(def, `${id} should exist in the registry`).toBeTruthy()
    expect(def?.category).toBe("knowledge")
    expect(def?.group).toBe("maintenance")
    expect(def?.type).toBe("action")
  })

  it("no longer exposes any system.danger.* defs", () => {
    const stragglers = SETTINGS_REGISTRY.filter((d) => d.id.startsWith("system.danger."))
    expect(stragglers.map((d) => d.id)).toEqual([])
  })

  it("keeps the old section names searchable as keywords", () => {
    const clear = getDef("knowledge.maintenance.clearDomain")
    expect(clear?.keywords).toEqual(expect.arrayContaining(["System"]))
  })
})
