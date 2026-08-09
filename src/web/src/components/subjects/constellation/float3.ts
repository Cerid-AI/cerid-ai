// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Shared organic-float displacement for the 3D Constellation. Each node drifts
// on a small, smooth, seamless path around its FIXED UMAP seed — the graph
// "breathes" without a force sim and without the semantic layout moving. The
// TS and GLSL forms below are kept byte-for-byte equivalent so the CPU fallback
// and the shader agree. Sum of two sines per axis at incommensurate low
// frequencies → organic, non-repeating-looking, bounded in [-amp, amp].

export function float3(seed: number, t: number, amp = 1): [number, number, number] {
  const a = seed * 6.2831
  return [
    (amp * (Math.sin(t * 0.31 + a) + 0.5 * Math.sin(t * 0.53 + a * 1.7))) / 1.5,
    (amp * (Math.sin(t * 0.27 + a * 1.3) + 0.5 * Math.sin(t * 0.47 + a * 2.1))) / 1.5,
    (amp * (Math.sin(t * 0.23 + a * 0.7) + 0.5 * Math.sin(t * 0.41 + a * 1.1))) / 1.5,
  ]
}

// GLSL twin of float3 — inject into the node/edge vertex shaders. Uniform uTime
// supplies t; a per-vertex/per-instance seed attribute supplies seed.
export const FLOAT3_GLSL = /* glsl */ `
  vec3 float3(float seed, float t, float amp) {
    float a = seed * 6.2831;
    return amp * vec3(
      (sin(t * 0.31 + a) + 0.5 * sin(t * 0.53 + a * 1.7)) / 1.5,
      (sin(t * 0.27 + a * 1.3) + 0.5 * sin(t * 0.47 + a * 2.1)) / 1.5,
      (sin(t * 0.23 + a * 0.7) + 0.5 * sin(t * 0.41 + a * 1.1)) / 1.5
    );
  }
`
