// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Shared visual encoding for Constellation renderers. Nodes
// (instanced-nodes) and links (neural-links) must agree on community
// color so an edge reads as belonging to its endpoints' clusters.

export const COMMUNITY_PALETTE_RGB = [
  [0.898, 0.518, 0.478], [0.898, 0.659, 0.478], [0.898, 0.784, 0.478], [0.831, 0.686, 0.216],
  [0.784, 0.898, 0.478], [0.659, 0.898, 0.478], [0.478, 0.898, 0.784], [0.478, 0.784, 0.898],
  [0.478, 0.659, 0.898], [0.659, 0.478, 0.898], [0.784, 0.478, 0.898], [0.898, 0.478, 0.784],
] as const

export const GRAPHITE: readonly [number, number, number] = [0.36, 0.40, 0.50]

export function communityRgb(communityId: string | null): readonly [number, number, number] {
  if (!communityId) return GRAPHITE
  let h = 0
  for (let i = 0; i < communityId.length; i++) {
    h = ((h << 5) - h) + communityId.charCodeAt(i)
    h |= 0
  }
  const idx = Math.abs(h) % COMMUNITY_PALETTE_RGB.length
  return COMMUNITY_PALETTE_RGB[idx]
}

export function nodeRadius(mentionCount: number): number {
  // Small relative to the layout's ~0.5-unit node spacing: the linkage
  // web is the structure; nodes are jewels at the junctions, not a ball
  // pit that occludes it. Range ≈ 0.10 (1 mention) → 0.42 (1K mentions).
  return 0.1 + Math.log1p(Math.max(0, mentionCount)) * 0.045
}

export function degreeRadius(degree: number): number {
  // Size = graph centrality (yFiles KG guidance: size encodes importance).
  // Degree, not mention count, is what the force layout organizes around —
  // so visual weight and spatial position tell the same story.
  // 0 connections → barely-there dust; 293 (top hub) → 0.58.
  if (degree <= 0) return 0.05
  return 0.09 + Math.log1p(degree) * 0.086
}

/**
 * Deterministic per-key pseudo-random in [0, 1). Used for stagger
 * offsets and pulse phases so re-renders don't re-randomize the scene
 * (same constraint as ambient-particles' fixed seed).
 */
export function hash01(key: string): number {
  let h = 2166136261
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 10000) / 10000
}

// ---------------------------------------------------------------------------
// Lens color encodings. Trust maps to the canonical verification bands
// (green/amber/red — same scale as <VerifiedResponse>); type reuses the
// community palette keyed by type name so the count of hues stays bounded.
// ---------------------------------------------------------------------------

const TRUST_RGB: Record<string, readonly [number, number, number]> = {
  verified: [0.30, 0.82, 0.46],
  partial: [0.95, 0.75, 0.30],
  unverified: [0.90, 0.38, 0.36],
  contradicted: [0.95, 0.25, 0.30],
}

export function trustRgb(trustState: string): readonly [number, number, number] {
  return TRUST_RGB[trustState] ?? GRAPHITE
}

export function typeRgb(entityType: string): readonly [number, number, number] {
  if (!entityType || entityType === "unknown") return GRAPHITE
  const idx = Math.floor(hash01(entityType) * COMMUNITY_PALETTE_RGB.length)
  return COMMUNITY_PALETTE_RGB[idx % COMMUNITY_PALETTE_RGB.length]
}
