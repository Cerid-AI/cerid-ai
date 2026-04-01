// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { USER_PRESETS, getPresetById, type PresetId } from "@/lib/user-presets"

describe("USER_PRESETS", () => {
  it("contains exactly three presets", () => {
    expect(USER_PRESETS).toHaveLength(3)
  })

  it("has unique IDs", () => {
    const ids = USER_PRESETS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it("quick preset sets simple mode", () => {
    const quick = getPresetById("quick")
    expect(quick.uiMode).toBe("simple")
  })

  it("balanced preset sets advanced mode", () => {
    const balanced = getPresetById("balanced")
    expect(balanced.uiMode).toBe("advanced")
  })

  it("maximum preset sets advanced mode", () => {
    const maximum = getPresetById("maximum")
    expect(maximum.uiMode).toBe("advanced")
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
