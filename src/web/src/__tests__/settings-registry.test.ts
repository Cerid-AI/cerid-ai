// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Registry CI gate — copy drift is a CI failure, not a review catch.
 *
 * Every SettingDef must carry non-empty helpText, a scopeOfEffect display
 * sentence, and at least one search keyword (migrated controls must include
 * their pre-SEXTANT tab/section names — enforced socially via review, the
 * floor here is ≥1). Entitlement-gated defs must name the server feature
 * flag they mirror so flag-off messaging can differ from tier-locked.
 */

import { describe, expect, it } from "vitest"
import {
  CATEGORY_META,
  searchSettings,
  SETTINGS_REGISTRY,
} from "@/lib/settings-registry"

const CATEGORY_IDS = new Set(CATEGORY_META.map((c) => c.id))

describe("settings registry contract", () => {
  it("has unique ids", () => {
    const ids = SETTINGS_REGISTRY.map((d) => d.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it.each(SETTINGS_REGISTRY.map((d) => [d.id, d] as const))(
    "%s satisfies the def contract",
    (_id, def) => {
      expect(def.label.trim()).not.toBe("")
      expect(def.helpText.trim()).not.toBe("")
      expect(def.scopeOfEffect.display.trim()).not.toBe("")
      expect(["device", "server", "synced", "env"]).toContain(def.scopeOfEffect.scope)
      expect(def.keywords.length).toBeGreaterThanOrEqual(1)
      expect(def.keywords.every((k) => k.trim() !== "")).toBe(true)
      expect(CATEGORY_IDS.has(def.category)).toBe(true)
      expect(def.id.startsWith(`${def.category}.`)).toBe(true)
      expect(def.group.trim()).not.toBe("")
      expect(["core", "advanced"]).toContain(def.level)
      if (def.entitlement) {
        expect(def.featureFlag, `${def.id} declares entitlement without featureFlag`).toBeTruthy()
      }
      if (def.type === "enum") {
        expect(def.options?.length ?? 0).toBeGreaterThan(0)
      }
      if (def.writer.kind === "env") {
        expect(def.scopeOfEffect.scope).toBe("env")
      }
      if (def.dependsOn) {
        expect(
          SETTINGS_REGISTRY.some((other) => other.id === def.dependsOn?.id),
          `${def.id} dependsOn unknown id ${def.dependsOn.id}`,
        ).toBe(true)
      }
    },
  )

  it("search finds the theme row by old-world synonyms", () => {
    const matches = searchSettings(SETTINGS_REGISTRY, "dark mode", { tier: "community" })
    expect(matches.map((m) => m.def.id)).toContain("appearance.theme.mode")
  })

  it("search is token-AND (a non-matching token excludes the def)", () => {
    const matches = searchSettings(SETTINGS_REGISTRY, "dark zzznope", { tier: "community" })
    expect(matches).toHaveLength(0)
  })

  it("search excludes visibleWhen-hidden defs", () => {
    const hidden = [
      {
        ...SETTINGS_REGISTRY[0],
        id: "system.test.hidden",
        label: "Hidden xyzzy row",
        visibleWhen: () => false,
      },
    ]
    expect(searchSettings(hidden, "xyzzy", { tier: "community" })).toHaveLength(0)
  })

  it("search ranks label matches above helpText matches", () => {
    const base = SETTINGS_REGISTRY[0]
    const defs = [
      { ...base, id: "system.test.a", label: "Other", helpText: "mentions zebra here", keywords: ["x"] },
      { ...base, id: "system.test.b", label: "Zebra stripes", helpText: "plain", keywords: ["x"] },
    ]
    const matches = searchSettings(defs, "zebra", { tier: "community" })
    expect(matches[0].def.id).toBe("system.test.b")
  })
})
