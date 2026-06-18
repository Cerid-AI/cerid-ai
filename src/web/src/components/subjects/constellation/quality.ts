// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
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
