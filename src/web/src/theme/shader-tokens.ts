// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Shader-ready color tokens for the Atlas / Constellation visualization
// tier. Mirrors the canonical OKLCH palettes in
// `tasks/2026-05-21-cerid-design-system-v2.md` §6, exported as packed
// RGBA-uint32 + hex-string forms for GPU consumption.
//
// Source of truth: design-system-v2.md §6. When tokens evolve, regenerate
// this file via `scripts/gen-shader-tokens.ts` (Phase A Day 5+); for now
// the values are hand-mirrored. The graphology adapter palette in
// `lib/graph/graphology-adapter.ts` must stay in sync.

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Pack a #RRGGBB hex string into [r, g, b, a] floats (0-1). For uniform
 * buffer / vertex attribute upload.
 */
export function hexToRgba(hex: string, alpha = 1): [number, number, number, number] {
  const cleaned = hex.replace("#", "")
  const r = parseInt(cleaned.slice(0, 2), 16) / 255
  const g = parseInt(cleaned.slice(2, 4), 16) / 255
  const b = parseInt(cleaned.slice(4, 6), 16) / 255
  return [r, g, b, alpha]
}

/**
 * Pack RGBA floats into a single 32-bit uint suitable for upload as a
 * vertex attribute (sigma's preferred color encoding).
 */
export function packRgba(rgba: [number, number, number, number]): number {
  const [r, g, b, a] = rgba
  return (
    (Math.round(r * 255) & 0xff) |
    ((Math.round(g * 255) & 0xff) << 8) |
    ((Math.round(b * 255) & 0xff) << 16) |
    ((Math.round(a * 255) & 0xff) << 24)
  )
}

/** Convenience: hex → packed uint32 directly. */
export function packHex(hex: string, alpha = 1): number {
  return packRgba(hexToRgba(hex, alpha))
}

// ---------------------------------------------------------------------------
// Trust halo palette (design-system-v2 §6.3)
// ---------------------------------------------------------------------------

export const TRUST_HALO_HEX = {
  verified: "#5AECCB", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  partial: "#E8C56A", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  unverified: "#D4AF37", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  contradicted: "#FF6B6B", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  unknown: "#5C6680", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
} as const

export type TrustState = keyof typeof TRUST_HALO_HEX

// ---------------------------------------------------------------------------
// Community palette (design-system-v2 §6.2) — 12 OKLCH-derived clusters
// ---------------------------------------------------------------------------

export const COMMUNITY_PALETTE_HEX: string[] = [
  "#E5847A", "#E5A87A", "#E5C87A", "#D4AF37", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  "#C8E57A", "#A8E57A", "#7AE5C8", "#7AC8E5", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  "#7AA8E5", "#A87AE5", "#C87AE5", "#E57AC8", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
]

// ---------------------------------------------------------------------------
// Edge palette (design-system-v2 §6.4)
// ---------------------------------------------------------------------------

export const EDGE_HEX = {
  mentions: "#7AC8E5", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  works_on: "#D4AF37", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  discussed_with: "#A8E57A", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  contradicts: "#FF6B6B", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  temporal: "#E8C56A", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
} as const

// ---------------------------------------------------------------------------
// Background / surface tier (design-system-v2 §2 — Cerid Vault palette)
// ---------------------------------------------------------------------------

export const SURFACE_HEX = {
  vaultDeep: "#0A1F3D", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  vaultSurface: "#142B52", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  brandTeal: "#00E5D8", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  brandGold: "#D4AF37", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  graphiteFallback: "#5C6680", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
} as const

// ---------------------------------------------------------------------------
// Source-family palette (Sources Constellation tier-1 overview) — relocated
// from components/sources/sources-constellation.tsx; Three.js `color` /
// `emissive` props can't consume CSS vars.
// ---------------------------------------------------------------------------

export const SOURCE_FAMILY_HEX = {
  files: "#f0b860", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  feeds: "#5ec5b6", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  mail: "#7a9ad7", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  calendar: "#c79a6e", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  chat: "#b08adc", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  media: "#e88373", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  webhook: "#82c89a", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  adapter: "#9fbfa3", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  pack: "#dcc36a", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  anchor: "#d4a44e", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var) — Cerid gold
} as const

// ---------------------------------------------------------------------------
// Constellation hub/super-node label palette (troika text `color` /
// `outlineColor` props — same GPU-consumption constraint as above)
// ---------------------------------------------------------------------------

export const LABEL_HEX = {
  default: "#C8D4E6", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
  hover: "#8CF5DC", // drift-allowed: brand shader/Sigma color registry (color prop can't use CSS var)
} as const

// ---------------------------------------------------------------------------
// Aggregate export — single object the shader-tokens generator emits
// ---------------------------------------------------------------------------

export const SHADER_TOKENS = {
  trust: TRUST_HALO_HEX,
  community: COMMUNITY_PALETTE_HEX,
  edge: EDGE_HEX,
  surface: SURFACE_HEX,
} as const
