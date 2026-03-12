// Copyright (c) 2026 Justin Michaels. All rights reserved.
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

  it("casual preset sets simple mode", () => {
    const casual = getPresetById("casual")
    expect(casual.uiMode).toBe("simple")
  })

  it("researcher preset sets advanced mode", () => {
    const researcher = getPresetById("researcher")
    expect(researcher.uiMode).toBe("advanced")
  })

  it("power-user preset sets advanced mode", () => {
    const powerUser = getPresetById("power-user")
    expect(powerUser.uiMode).toBe("advanced")
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

  it("power-user enables all pipeline features", () => {
    const pu = getPresetById("power-user")
    expect(pu.settings.enable_feedback_loop).toBe(true)
    expect(pu.settings.enable_hallucination_check).toBe(true)
    expect(pu.settings.enable_self_rag).toBe(true)
    expect(pu.settings.enable_semantic_cache).toBe(true)
    expect(pu.settings.enable_late_interaction).toBe(true)
  })

  it("casual disables advanced features", () => {
    const casual = getPresetById("casual")
    expect(casual.settings.enable_feedback_loop).toBe(false)
    expect(casual.settings.enable_hallucination_check).toBe(false)
    expect(casual.settings.enable_self_rag).toBe(false)
    expect(casual.settings.enable_semantic_cache).toBe(false)
  })
})

describe("getPresetById", () => {
  it("returns correct preset for each valid ID", () => {
    const ids: PresetId[] = ["casual", "researcher", "power-user"]
    for (const id of ids) {
      const preset = getPresetById(id)
      expect(preset.id).toBe(id)
    }
  })
})
