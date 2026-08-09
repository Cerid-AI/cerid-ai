import { describe, it, expect } from "vitest"
import { QUALITY_SETTINGS } from "../quality"

describe("quality float flag", () => {
  it("is off on low, on for medium+", () => {
    expect(QUALITY_SETTINGS.low.float).toBe(false)
    expect(QUALITY_SETTINGS.medium.float).toBe(true)
    expect(QUALITY_SETTINGS.high.float).toBe(true)
    expect(QUALITY_SETTINGS.ultra.float).toBe(true)
  })
})
