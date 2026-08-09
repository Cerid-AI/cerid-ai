// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
import { describe, it, expect, beforeEach } from "vitest"
import { loadMapConfig, MAP_CONFIG_DEFAULTS } from "../map-config"

const STORAGE_KEY = "cerid-map-config"

describe("map-config territories (A4)", () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it("defaults to nebula territories (the pre-A4 hull rendering)", () => {
    expect(MAP_CONFIG_DEFAULTS.territories).toBe("nebula")
    expect(loadMapConfig().territories).toBe("nebula")
  })

  it("migrates legacy hullsVisible=false to territories off", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ hullsVisible: false }))
    expect(loadMapConfig().territories).toBe("off")
  })

  it("migrates legacy hullsVisible=true to territories nebula", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ hullsVisible: true }))
    expect(loadMapConfig().territories).toBe("nebula")
  })

  it("an explicit stored territories value wins over legacy hullsVisible", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ hullsVisible: false, territories: "contours" }))
    expect(loadMapConfig().territories).toBe("contours")
  })
})
