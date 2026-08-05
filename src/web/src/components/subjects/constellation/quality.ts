// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Constellation quality tiers. One knob the user actually understands —
// Low / Medium / High / Ultra — mapped to renderer settings:
//
//   low    — flat 2D knowledge graph: z collapsed, orbit locked to
//            pan/zoom, no glow/pulses/stars/particles, dpr 1. The
//            Obsidian-classic reading view; runs on anything.
//   medium — light 3D: nodes + links + glow, no pulses/particles,
//            reduced stars, dpr ≤1.5.
//   high   — the full living scene: glow, synaptic pulses, growth,
//            starfield, particles, dpr ≤2. (Default.)
//   ultra  — AAA pass for capable GPUs (AMD Vega II-class and up):
//            everything in high plus real HDR bloom + vignette
//            postprocessing and a denser starfield.
//
// The choice persists per machine in localStorage — quality is a
// hardware property, not knowledge-base state.

export type QualityTier = "low" | "medium" | "high" | "ultra"

export interface QualitySettings {
  flat: boolean
  glow: boolean
  pulses: boolean
  particles: boolean
  starCount: number
  dpr: [number, number]
  antialias: boolean
  postprocessing: boolean
  autoRotate: boolean
  float: boolean
}

export const QUALITY_SETTINGS: Record<QualityTier, QualitySettings> = {
  low: {
    flat: true,
    glow: false,
    pulses: false,
    particles: false,
    starCount: 0,
    dpr: [1, 1],
    antialias: false,
    postprocessing: false,
    autoRotate: false,
    float: false,
  },
  medium: {
    flat: false,
    glow: true,
    pulses: false,
    particles: false,
    starCount: 800,
    dpr: [1, 1.5],
    antialias: true,
    postprocessing: false,
    autoRotate: true,
    float: true,
  },
  high: {
    flat: false,
    glow: true,
    pulses: true,
    particles: true,
    starCount: 2000,
    dpr: [1, 2],
    antialias: true,
    postprocessing: false,
    autoRotate: true,
    float: true,
  },
  ultra: {
    flat: false,
    glow: true,
    pulses: true,
    particles: true,
    starCount: 3500,
    dpr: [1, 2],
    antialias: true,
    postprocessing: true,
    autoRotate: true,
    float: true,
  },
}

export const QUALITY_TIERS: QualityTier[] = ["low", "medium", "high", "ultra"]

const STORAGE_KEY = "cerid-constellation-quality"

export function loadQuality(): QualityTier {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && QUALITY_TIERS.includes(stored as QualityTier)) {
      return stored as QualityTier
    }
  } catch {
    // storage unavailable (private mode) — fall through to default
  }
  return "high"
}

export function saveQuality(tier: QualityTier): void {
  try {
    localStorage.setItem(STORAGE_KEY, tier)
  } catch {
    // storage unavailable — selection lives for the session only
  }
}

// ---------------------------------------------------------------------------
// Runtime adaptive-quality helpers — pure functions, no side-effects.
// These drive the PerformanceMonitor step-down/step-up ladder.
// The persisted user tier is NEVER modified by these; they only compute the
// effective (transient, per-session) tier.
// ---------------------------------------------------------------------------

/** Step the effective quality down by one tier on GPU pressure. Floors at "low". */
export function degradeTier(tier: QualityTier): QualityTier {
  switch (tier) {
    case "ultra":  return "high"
    case "high":   return "medium"
    case "medium": return "low"
    case "low":    return "low"
  }
}

/** Step the effective quality up by one tier when the GPU has headroom. Caps at the persisted tier. */
export function upgradeTier(tier: QualityTier, ceiling: QualityTier): QualityTier {
  const idx = QUALITY_TIERS.indexOf(tier)
  const cap = QUALITY_TIERS.indexOf(ceiling)
  const next = Math.min(idx + 1, cap)
  return QUALITY_TIERS[next]
}

/**
 * Maps camera distance to the number of hub labels to render.
 * Far out: only the very highest-degree hubs are legible; close in: show all.
 * Kept in quality.ts (a pure module with no side-effect imports) so it can be
 * unit-tested without pulling in Three.js or palette modules.
 */
export function visibleLabelCount(cameraDistance: number, max: number): number {
  // Thresholds tuned to the default camera position (distance ~29) and
  // the graph spread (force layout ~±15 units).
  if (cameraDistance >= 55) return Math.min(3, max)
  if (cameraDistance >= 40) return Math.min(6, max)
  if (cameraDistance >= 28) return Math.min(12, max)
  return max
}

/**
 * Base fill-opacity for a persistent hub label at a given camera distance (B2).
 * Surviving labels also soften as the camera pulls away, so the LOD reads as a
 * gentle fade rather than a hard pop-out when a label crosses the count cull in
 * visibleLabelCount. Never returns 0 — the far tier stays faintly legible.
 * Shares the 28/40/55 distance bands with visibleLabelCount for a coherent LOD.
 * Hovered/pinned labels override this to 1.0 in the component.
 */
export function labelFillOpacity(cameraDistance: number): number {
  if (cameraDistance >= 55) return 0.5
  if (cameraDistance >= 40) return 0.62
  if (cameraDistance >= 28) return 0.75
  return 0.85
}
