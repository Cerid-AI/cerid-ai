// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Data-prep for the cosmos.gl "Live" scene (B8). Converts the already-loaded
// /graph/embeddings/3d payload into cosmos.gl's flat Float32Array buffers:
// point positions, RGBA colors, and link index-pairs. Pure — cosmos.gl itself
// owns the WebGL; this is just marshalling, so it's unit-testable.

/**
 * Flatten entity server positions into cosmos.gl's [x1,y1,x2,y2,...] point
 * buffer. Seeds the "Live" scene from the stable server layout so reduced
 * motion (and the paused first frame) shows the familiar arrangement.
 */
export function positionsFromEntities(entities: { x: number; y: number }[]): Float32Array {
  const out = new Float32Array(entities.length * 2)
  for (let i = 0; i < entities.length; i++) {
    out[i * 2] = entities[i].x
    out[i * 2 + 1] = entities[i].y
  }
  return out
}

/**
 * Random point positions in [0, spaceSize] for the "re-run big bang" control —
 * scatter everything, then let the GPU simulation self-organize. `rand` is
 * injectable for deterministic tests; defaults to Math.random.
 */
export function randomPositions(n: number, spaceSize: number, rand: () => number = Math.random): Float32Array {
  const out = new Float32Array(n * 2)
  for (let i = 0; i < n * 2; i++) out[i] = rand() * spaceSize
  return out
}

/**
 * Flatten links into cosmos.gl's [source,target,...] index-pair buffer,
 * dropping self-loops and any pair that references an out-of-range point.
 */
export function linksToPairs(links: [number, number, number, string][], n: number): Float32Array {
  const pairs: number[] = []
  for (const [si, ti] of links) {
    if (si < 0 || ti < 0 || si >= n || ti >= n || si === ti) continue
    pairs.push(si, ti)
  }
  return new Float32Array(pairs)
}

/**
 * Expand the n×3 RGB lens colors (the same buffer the 3D scene uses) into
 * cosmos.gl's n×4 RGBA point-color buffer at a fixed alpha. Missing colors
 * fall back to opaque white so a point is never invisible.
 */
export function colorsFromRgb(rgb: Float32Array | undefined, n: number, alpha: number): Float32Array {
  const out = new Float32Array(n * 4)
  for (let i = 0; i < n; i++) {
    if (rgb && rgb.length >= (i + 1) * 3) {
      out[i * 4] = rgb[i * 3]
      out[i * 4 + 1] = rgb[i * 3 + 1]
      out[i * 4 + 2] = rgb[i * 3 + 2]
    } else {
      out[i * 4] = 1
      out[i * 4 + 1] = 1
      out[i * 4 + 2] = 1
    }
    out[i * 4 + 3] = alpha
  }
  return out
}
