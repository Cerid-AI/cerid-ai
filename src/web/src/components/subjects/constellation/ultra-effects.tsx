// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Ultra-tier postprocessing stack. Lazy-loaded — only users who pick
// Ultra pay for the postprocessing bundle and the fullscreen passes.
//
// Bloom is the AAA ingredient: the additive glow sprites and synaptic
// pulses cross the luminance threshold and halo naturally, like
// emissive materials in a modern engine. mipmapBlur keeps the pass
// cheap on AMD-Mac/ANGLE (no full-res gaussian chain).

import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing"

export default function UltraEffects() {
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
