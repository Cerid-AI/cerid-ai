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
  verified: "#5AECCB",
  partial: "#E8C56A",
  unverified: "#D4AF37",
  contradicted: "#FF6B6B",
  unknown: "#5C6680",
} as const

export type TrustState = keyof typeof TRUST_HALO_HEX

// ---------------------------------------------------------------------------
// Community palette (design-system-v2 §6.2) — 12 OKLCH-derived clusters
// ---------------------------------------------------------------------------

export const COMMUNITY_PALETTE_HEX: string[] = [
  "#E5847A", "#E5A87A", "#E5C87A", "#D4AF37",
  "#C8E57A", "#A8E57A", "#7AE5C8", "#7AC8E5",
  "#7AA8E5", "#A87AE5", "#C87AE5", "#E57AC8",
]

// ---------------------------------------------------------------------------
// Edge palette (design-system-v2 §6.4)
// ---------------------------------------------------------------------------

export const EDGE_HEX = {
  mentions: "#7AC8E5",
  works_on: "#D4AF37",
  discussed_with: "#A8E57A",
  contradicts: "#FF6B6B",
  temporal: "#E8C56A",
} as const

// ---------------------------------------------------------------------------
// Background / surface tier (design-system-v2 §2 — Cerid Vault palette)
// ---------------------------------------------------------------------------

export const SURFACE_HEX = {
  vaultDeep: "#0A1F3D",
  vaultSurface: "#142B52",
  brandTeal: "#00E5D8",
  brandGold: "#D4AF37",
  graphiteFallback: "#5C6680",
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
