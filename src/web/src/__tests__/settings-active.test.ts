// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * "Active configuration" derivation (Settings Overview, ST1/ST2). A setting is
 * "modified" when its registry def carries a `default`, writes via
 * `settings-patch`, and the live server value differs from that default.
 */

import { describe, expect, it } from "vitest"
import {
  isSettingModified,
  modifiedSettingIds,
  modifiedSettings,
  settingCurrentValue,
} from "@/lib/settings-registry"
import { getDef } from "@/lib/settings-registry"

const def = (id: string) => {
  const d = getDef(id)
  if (!d) throw new Error(`missing registry def ${id}`)
  return d
}

describe("settings-active derivation", () => {
  it("settingCurrentValue reads the writer.key off server settings", () => {
    expect(settingCurrentValue(def("retrieval.contextInjection.threshold"), { auto_inject_threshold: 0.4 })).toBe(0.4)
    expect(settingCurrentValue(def("retrieval.contextInjection.threshold"), {})).toBeUndefined()
  })

  it("flags a settings-patch value that differs from its default", () => {
    // default 0.15 (verified in registry); 0.40 is a non-default selection
    expect(isSettingModified(def("retrieval.contextInjection.threshold"), { auto_inject_threshold: 0.4 })).toBe(true)
  })

  it("does NOT flag a value equal to its default", () => {
    expect(isSettingModified(def("retrieval.contextInjection.threshold"), { auto_inject_threshold: 0.15 })).toBe(false)
  })

  it("does NOT flag when the server omits the key (unknown != modified)", () => {
    expect(isSettingModified(def("retrieval.contextInjection.threshold"), {})).toBe(false)
  })

  it("modifiedSettings returns the changed defs with current + default", () => {
    const result = modifiedSettings({
      auto_inject_threshold: 0.4, // modified
      enable_auto_inject: true, // default true → not modified
      rag_mode: "always", // default "smart"/"balanced" → modified
    })
    const ids = result.map((m) => m.def.id)
    expect(ids).toContain("retrieval.contextInjection.threshold")
    expect(ids).not.toContain("retrieval.contextInjection.autoInject")
    const thr = result.find((m) => m.def.id === "retrieval.contextInjection.threshold")
    expect(thr?.current).toBe(0.4)
    expect(thr?.default).toBe(0.15)
  })

  it("modifiedSettingIds is a Set mirror of modifiedSettings", () => {
    const settings = { auto_inject_threshold: 0.4 }
    const ids = modifiedSettingIds(settings)
    expect(ids.has("retrieval.contextInjection.threshold")).toBe(true)
    expect(ids.size).toBe(modifiedSettings(settings).length)
  })

  it("excludes visibleWhen-hidden defs from the active summary", () => {
    // a community-tier ctx must not surface a pro-only modified def that is
    // hidden for the tier; modifiedSettings accepts an optional ctx filter
    const all = modifiedSettings({ auto_inject_threshold: 0.4 }, { tier: "community" })
    expect(all.every((m) => m.def.visibleWhen?.({ tier: "community" }) !== false)).toBe(true)
  })

  it("returns nothing for a null/empty server settings object", () => {
    expect(modifiedSettings(null)).toEqual([])
    expect(modifiedSettingIds(undefined).size).toBe(0)
  })
})
