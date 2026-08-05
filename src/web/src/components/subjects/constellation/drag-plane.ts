// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Pure ray/plane math for 3D node dragging. The caller builds a plane through
// the grabbed node facing the camera; projecting the pointer ray onto it makes
// the node track the cursor in screen space. No three.js types so it unit-tests
// in jsdom.

export type Vec3 = [number, number, number]

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

export function planeIntersect(
  rayOrigin: Vec3,
  rayDir: Vec3,
  planePoint: Vec3,
  planeNormal: Vec3,
): Vec3 | null {
  const denom = dot(rayDir, planeNormal)
  if (Math.abs(denom) < 1e-6) return null // parallel
  const t = dot(sub(planePoint, rayOrigin), planeNormal) / denom
  if (t < 0) return null // plane is behind the ray origin
  return [rayOrigin[0] + rayDir[0] * t, rayOrigin[1] + rayDir[1] * t, rayOrigin[2] + rayDir[2] * t]
}
