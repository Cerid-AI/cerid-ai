import { describe, it, expect } from "vitest"
import { boundingSphere, cameraTargetFor } from "../camera-focus-3d"

describe("boundingSphere", () => {
  it("contains all points", () => {
    const pts: [number, number, number][] = [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]]
    const { center, radius } = boundingSphere(pts)
    for (const p of pts) {
      const d = Math.hypot(p[0] - center[0], p[1] - center[1], p[2] - center[2])
      expect(d).toBeLessThanOrEqual(radius + 1e-6)
    }
  })
  it("handles a single point (radius 0)", () => {
    const { center, radius } = boundingSphere([[3, 4, 5]])
    expect(center).toEqual([3, 4, 5])
    expect(radius).toBe(0)
  })
})

describe("cameraTargetFor", () => {
  it("targets the sphere center and backs off with radius", () => {
    const near = cameraTargetFor([0, 0, 0], 1, [0, 0, 10])
    const far = cameraTargetFor([0, 0, 0], 5, [0, 0, 10])
    expect(near.target).toEqual([0, 0, 0])
    const dNear = Math.hypot(...near.position)
    const dFar = Math.hypot(...far.position)
    expect(dFar).toBeGreaterThan(dNear) // bigger sphere → camera further out
  })
  it("keeps the current view direction", () => {
    const { position } = cameraTargetFor([0, 0, 0], 1, [0, 0, 10])
    // camera stays on the +z axis it was already on
    expect(position[0]).toBeCloseTo(0)
    expect(position[1]).toBeCloseTo(0)
    expect(position[2]).toBeGreaterThan(0)
  })
})
