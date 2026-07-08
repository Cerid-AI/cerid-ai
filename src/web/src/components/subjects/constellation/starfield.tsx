// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Parallax starfield (B4). Two-to-three concentric drei <Stars> shells at
// differing radius / depth / rotation-speed. As the cathedral auto-rotates the
// shells drift at different apparent rates, giving the void real depth — the
// 100,000-Stars / space-map parallax trick — instead of one flat backdrop.
//
// Star counts stay governed by the quality tier (the caller passes the tier's
// starCount budget, which we split across shells). The far shell only exists at
// Ultra. Speeds fall to 0 under reduced motion so the field is static.

import { Stars } from "@react-three/drei"

export interface ParallaxStarfieldProps {
  /** Total star budget from the quality tier — split across the shells. */
  count: number
  /** When false (reduced motion), all shells hold still. */
  animate?: boolean
  /** Ultra adds a third, far, slow shell for extra depth. */
  ultra?: boolean
}

export function ParallaxStarfield({ count, animate = true, ultra = false }: ParallaxStarfieldProps) {
  const s = animate ? 1 : 0
  return (
    <>
      {/* Near shell — brighter, faster drift. */}
      <Stars
        radius={40}
        depth={30}
        count={Math.round(count * 0.5)}
        factor={2}
        saturation={0.15}
        fade
        speed={s * 0.8}
      />
      {/* Mid shell — the bulk of the field. */}
      <Stars
        radius={65}
        depth={45}
        count={Math.round(count * 0.35)}
        factor={3.5}
        saturation={0.2}
        fade
        speed={s * 0.35}
      />
      {/* Far shell (Ultra only) — big, sparse, barely drifting. */}
      {ultra && (
        <Stars
          radius={95}
          depth={55}
          count={Math.round(count * 0.3)}
          factor={5}
          saturation={0.25}
          fade
          speed={s * 0.12}
        />
      )}
    </>
  )
}
