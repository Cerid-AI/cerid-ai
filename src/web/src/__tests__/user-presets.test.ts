// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { USER_PRESETS, getPresetById, type PresetId } from "@/lib/user-presets"

describe("USER_PRESETS", () => {
  it("contains the five preset modes", () => {
    // quick / balanced / maximum + Pro-tier privacy_first / power_user
    // shipped in the UX consolidation pass.
    expect(USER_PRESETS).toHaveLength(5)
  })

  it("privacy_first preset turns all automations off", () => {
    const pf = getPresetById("privacy_first")
    expect(pf.automations).toBeDefined()
    expect(pf.automations!.every((a) => a.enabled === false)).toBe(true)
    expect(pf.automations!.every((a) => a.schedule === "")).toBe(true)
  })

  it("power_user preset enables both automations", () => {
    const pu = getPresetById("power_user")
    expect(pu.automations).toBeDefined()
    expect(pu.automations!.every((a) => a.enabled === true)).toBe(true)
    // Triage on a sub-hour cadence; digest at a daily fixed time.
    const triage = pu.automations!.find((a) => a.feature === "inbox_triage")
    expect(triage?.schedule).toContain("*/")
    const digest = pu.automations!.find((a) => a.feature === "daily_digest")
    expect(digest?.schedule).toMatch(/^\d+ \d+/)
  })

  it("Pro presets carry the requiresPro flag", () => {
    expect(getPresetById("power_user").requiresPro).toBe(true)
    // privacy_first ships at all tiers
    expect(getPresetById("privacy_first").requiresPro).toBeUndefined()
  })

  it("has unique IDs", () => {
    const ids = USER_PRESETS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })


  it("all presets have settings with enable_auto_inject", () => {
    for (const preset of USER_PRESETS) {
      expect(preset.settings).toHaveProperty("enable_auto_inject")
    }
  })

  it("all presets have local storage overrides", () => {
    for (const preset of USER_PRESETS) {
      expect(Object.keys(preset.local).length).toBeGreaterThan(0)
    }
  })

  it("maximum enables all pipeline features", () => {
    const max = getPresetById("maximum")
    expect(max.settings.enable_feedback_loop).toBe(true)
    expect(max.settings.enable_hallucination_check).toBe(true)
    expect(max.settings.enable_self_rag).toBe(true)
    expect(max.settings.enable_semantic_cache).toBe(true)
    expect(max.settings.enable_late_interaction).toBe(true)
    expect(max.settings.enable_memory_consolidation).toBe(true)
    expect(max.settings.enable_context_compression).toBe(true)
  })

  it("quick enables core verification features", () => {
    const quick = getPresetById("quick")
    expect(quick.settings.enable_feedback_loop).toBe(false)
    expect(quick.settings.enable_hallucination_check).toBe(true)
    expect(quick.settings.enable_self_rag).toBe(true)
    expect(quick.settings.enable_semantic_cache).toBe(false)
  })
})

describe("getPresetById", () => {
  it("returns correct preset for each valid ID", () => {
    const ids: PresetId[] = ["quick", "balanced", "maximum"]
    for (const id of ids) {
      const preset = getPresetById(id)
      expect(preset.id).toBe(id)
    }
  })
})
