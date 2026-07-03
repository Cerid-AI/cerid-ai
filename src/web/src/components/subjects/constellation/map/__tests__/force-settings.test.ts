import { describe, it, expect } from "vitest"
import { buildForceSettings, shouldRunLayout } from "../force-settings"

describe("buildForceSettings", () => {
  it("enables Barnes-Hut above 500 nodes", () => {
    expect(buildForceSettings(3000).barnesHutOptimize).toBe(true)
    expect(buildForceSettings(100).barnesHutOptimize).toBe(false)
  })
  it("uses strong gravity so the degree-1 tail stays in the disc (no donut)", () => {
    const s = buildForceSettings(3000)
    // Weak/non-strong gravity let repulsion fling the ~90% single-mention tail
    // to the rim, hollowing the centre into a donut. Strong gravity anchors it.
    expect(s.strongGravityMode).toBe(true)
    expect(s.gravity).toBeGreaterThanOrEqual(1)
  })
  it("keeps repulsion low and linLog off so the disc fills instead of ringing", () => {
    const s = buildForceSettings(3000)
    // linLog + high scalingRatio push a hub-and-leaf topology into an outer
    // ring; low repulsion + plain attraction fills the disc uniformly.
    expect(s.linLogMode).toBe(false)
    expect(s.scalingRatio).toBeLessThanOrEqual(2)
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
