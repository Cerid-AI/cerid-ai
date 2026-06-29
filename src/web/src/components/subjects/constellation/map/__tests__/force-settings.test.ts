import { describe, it, expect } from "vitest"
import { buildForceSettings, shouldRunLayout } from "../force-settings"

describe("buildForceSettings", () => {
  it("enables Barnes-Hut above 500 nodes", () => {
    expect(buildForceSettings(3000).barnesHutOptimize).toBe(true)
    expect(buildForceSettings(100).barnesHutOptimize).toBe(false)
  })
  it("uses light (non-strong) gravity so communities can separate into clusters", () => {
    const s = buildForceSettings(3000)
    expect(s.strongGravityMode).toBe(false)
    expect(s.gravity).toBeLessThan(1)
  })
  it("uses linLog + edge-weight attraction for visible affinity clustering", () => {
    const s = buildForceSettings(3000)
    expect(s.linLogMode).toBe(true)
    expect(s.edgeWeightInfluence).toBeGreaterThan(1)
  })
  it("keeps theta at 0.5 and a non-zero slowDown to damp jitter", () => {
    const s = buildForceSettings(3000)
    expect(s.barnesHutTheta).toBe(0.5)
    expect(s.slowDown).toBeGreaterThanOrEqual(1)
  })
})

describe("shouldRunLayout", () => {
  it("is false when reduced motion is requested", () => {
    expect(shouldRunLayout({ reducedMotion: true, liveLayout: true, nodeCount: 3000 })).toBe(false)
  })
  it("is false when the user disabled live layout", () => {
    expect(shouldRunLayout({ reducedMotion: false, liveLayout: false, nodeCount: 3000 })).toBe(false)
  })
  it("is false for an empty graph", () => {
    expect(shouldRunLayout({ reducedMotion: false, liveLayout: true, nodeCount: 0 })).toBe(false)
  })
  it("is true for a normal graph with motion allowed and live layout on", () => {
    expect(shouldRunLayout({ reducedMotion: false, liveLayout: true, nodeCount: 3000 })).toBe(true)
  })
})
