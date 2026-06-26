// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Shared visual encoding for Constellation renderers. Nodes
// (instanced-nodes) and links (neural-links) must agree on community
// color so an edge reads as belonging to its endpoints' clusters.
//
// Pure, dependency-free helpers (communityRgb, nodeBaseAlpha, GRAPHITE, …)
// live in palette-pure.ts so they can be unit-tested without the
// @/lib/graph/identity dependency chain.

import type { MapTokens } from "./map/community-layer"
import { domainColor } from "@/lib/graph/identity"
import {
  COMMUNITY_PALETTE_RGB,
  GRAPHITE,
  ISOLATED_COMMUNITY_ID,
  communityRgb,
  nodeBaseAlpha,
} from "./palette-pure"

// Re-export pure helpers so all consumers keep a single import path.
export {
  COMMUNITY_PALETTE_RGB,
  GRAPHITE,
  ISOLATED_COMMUNITY_ID,
  communityRgb,
  nodeBaseAlpha,
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

/**
 * Token-routed domain color for the 3D canvas path.
 *
 * Converts the resolved hex token from MapTokens (already oklch→hex via
 * community-layer's canvas readback) to an [r,g,b] triple in [0,1] for
 * WebGL instanced rendering. This is the first token-derived color in the
 * 3D path, setting the Cycle-4 precedent for demoting COMMUNITY_PALETTE_RGB.
 *
 * Pre-job / null domain → GRAPHITE (byte-identical to today's untouched scene).
 */
export function domainRgb(
  tokens: MapTokens,
  domain: string | null | undefined,
): readonly [number, number, number] {
  const hex = domainColor(tokens, domain)
  if (hex === tokens.domainOther || !hex.startsWith("#") || hex.length < 7) return GRAPHITE
  const n = parseInt(hex.slice(1, 7), 16)
  return [((n >> 16) & 0xff) / 255, ((n >> 8) & 0xff) / 255, (n & 0xff) / 255]
}
