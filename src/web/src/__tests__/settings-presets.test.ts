// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Settings preset detection regression tests.
 *
 * Backstops the 2026-04-24 bug: clicking *Balanced* in the Pipeline section
 * left *Efficient* highlighted. Root cause was a first-match scan in
 * `detectActivePreset` against PRESET_TOGGLE_KEYS only — Efficient was a
 * strict superset of Balanced on the toggle keys, so it always won.
 *
 * The fix ranks matching presets by specificity (the preset that explicitly
 * sets more keys wins). These tests pin that contract.
 */
import { describe, it, expect } from "vitest"
import { PRESETS, detectActivePreset } from "@/lib/settings-presets"

describe("detectActivePreset", () => {
  it("returns 'efficient' when current settings exactly match efficient's payload", () => {
    expect(detectActivePreset(PRESETS.efficient.values as Record<string, unknown>))
      .toBe("efficient")
  })

  it("returns 'balanced' when current settings exactly match balanced's payload", () => {
    // The 2026-04-24 regression — used to return "efficient" because of the
    // first-match-on-superset bug. With the specificity tiebreaker, balanced
    // wins because it sets more numeric fields (auto_inject_threshold,
    // mmr_lambda, semantic_cache_threshold) than efficient.
    expect(detectActivePreset(PRESETS.balanced.values as Record<string, unknown>))
      .toBe("balanced")
  })

  it("returns 'maximum' when current settings exactly match maximum's payload", () => {
    expect(detectActivePreset(PRESETS.maximum.values as Record<string, unknown>))
      .toBe("maximum")
  })

  it("returns null when current settings don't match any preset", () => {
    const custom = {
      ...PRESETS.balanced.values,
      enable_self_rag: false, // diverge from every preset
    } as Record<string, unknown>
    expect(detectActivePreset(custom)).toBeNull()
  })

  it("ignores extra fields on settings that are not part of any preset", () => {
    // Real settings carry many fields presets don't touch (provider keys,
    // feature flags, etc.). detectActivePreset must look only at preset keys.
    const settingsWithExtras = {
      ...PRESETS.balanced.values,
      openrouter_api_key_set: true,
      machine_id: "abc123",
      feature_tier: "community",
    } as Record<string, unknown>
    expect(detectActivePreset(settingsWithExtras)).toBe("balanced")
  })

  it("returns null on empty settings (no preset is a no-op)", () => {
    expect(detectActivePreset({})).toBeNull()
  })
})
