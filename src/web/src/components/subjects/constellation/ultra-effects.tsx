// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Ultra-tier postprocessing stack. Lazy-loaded — only users who pick
// Ultra pay for the postprocessing bundle and the fullscreen passes.
//
// Theme-aware (B1). The two themes want opposite treatments:
//
//   dark  — Bloom is the AAA ingredient: the additive glow sprites and
//           synaptic pulses cross the luminance threshold and halo
//           naturally, like emissive materials in a modern engine.
//           mipmapBlur keeps the pass cheap on AMD-Mac/ANGLE (no full-res
//           gaussian chain). Bloom is effectively selective — only the
//           bright instances (hovered/focused nodes get a per-instance
//           brightness boost through instanced-nodes' setColorAt / glow
//           aDim channel) push past the threshold; the calm background
//           stays sub-threshold. Vignette pulls focus to the center.
//   light  — Bloom washes out a light background into a white haze, so it
//           is OFF. N8AO (half-res depth AO) does the depth work instead:
//           it seats the spheres into the scene with contact shadowing so
//           the graph reads as volumetric even without glow. WCAG-safe —
//           AO only darkens crevices, never text/label contrast.

import { Bloom, EffectComposer, N8AO, Vignette } from "@react-three/postprocessing"

export interface UltraEffectsProps {
  /** Resolved theme. Dark → bloom + vignette; light → ambient occlusion, no bloom. */
  dark?: boolean
}

export default function UltraEffects({ dark = true }: UltraEffectsProps) {
  if (!dark) {
    // Light mode: depth via ambient occlusion, no bloom.
    return (
      <EffectComposer>
        <N8AO halfRes aoRadius={2.2} intensity={2.6} distanceFalloff={1.0} quality="medium" />
      </EffectComposer>
    )
  }
  return (
    <EffectComposer>
      <Bloom
        intensity={1.15}
        luminanceThreshold={0.18}
        luminanceSmoothing={0.55}
        mipmapBlur
      />
      <Vignette eskil={false} offset={0.16} darkness={0.62} />
    </EffectComposer>
  )
}
