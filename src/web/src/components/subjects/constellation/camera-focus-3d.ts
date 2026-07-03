// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure camera-framing math for community focus / drill-down. Frames a set of
// 3D points by their (cheap mean-center) bounding sphere and returns a camera
// target+position that keeps the current view direction while backing off just
// enough to fit the sphere in view. No three.js — unit-testable.

export type Vec3 = [number, number, number]

export function boundingSphere(points: Vec3[]): { center: Vec3; radius: number } {
  if (points.length === 0) return { center: [0, 0, 0], radius: 0 }
  let cx = 0, cy = 0, cz = 0
  for (const p of points) { cx += p[0]; cy += p[1]; cz += p[2] }
  const n = points.length
  const center: Vec3 = [cx / n, cy / n, cz / n]
  let r = 0
  for (const p of points) {
    r = Math.max(r, Math.hypot(p[0] - center[0], p[1] - center[1], p[2] - center[2]))
  }
  return { center, radius: r }
}

// Distance so a sphere of `radius` fits the vertical FOV, with margin. Pulled
// out of cameraTargetFor so callers outside <Canvas> (no camera position
// available) can still recover "how far out did we frame this community" —
// the exit-from-focus check in Constellation.tsx needs it independent of the
// current camera position, and this value never depended on camPos anyway.
export function framingDistanceFor(radius: number, fovDeg = 55): number {
  const half = (fovDeg * Math.PI) / 180 / 2
  return Math.max(4, (radius / Math.sin(half)) * 1.3 + 2)
}

export function cameraTargetFor(
  center: Vec3,
  radius: number,
  camPos: Vec3,
  fovDeg = 55,
): { target: Vec3; position: Vec3 } {
  const dist = framingDistanceFor(radius, fovDeg)
  // Keep the direction the camera currently views the center from.
  let dx = camPos[0] - center[0], dy = camPos[1] - center[1], dz = camPos[2] - center[2]
  let len = Math.hypot(dx, dy, dz)
  if (len < 1e-6) { dx = 0; dy = 0; dz = 1; len = 1 }
  const u: Vec3 = [dx / len, dy / len, dz / len]
  return {
    target: center,
    position: [center[0] + u[0] * dist, center[1] + u[1] * dist, center[2] + u[2] * dist],
  }
}
