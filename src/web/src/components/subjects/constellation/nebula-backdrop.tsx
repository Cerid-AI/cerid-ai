// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Brand nebula backdrop (B4). A handful of large, faint, additively-blended
// gas clouds sitting behind the graph — the colored depth that makes the void
// feel like a place rather than a black box. Zero assets: each cloud is a
// procedural radial-gradient CanvasTexture on a camera-facing sprite.
//
// Brand discipline: tints are drawn from the calm brand palette (teal / gold /
// blue / green) only — explicitly NO purple/pink (the AI-slop gradient the
// design system rejects). Peak alpha is capped at 4% so the clouds read as a
// whisper of color, never a wash. Dark-theme only (gated by the caller); on a
// light background additive low-alpha color is invisible anyway.

import { useEffect, useMemo } from "react"
import { AdditiveBlending, CanvasTexture } from "three"
import { hexToRgba, SURFACE_HEX, EDGE_HEX } from "@/theme/shader-tokens"

// Brand-safe tints only. No purple/pink.
const NEBULA_TINTS = [
  SURFACE_HEX.brandTeal, // teal
  SURFACE_HEX.brandGold, // gold
  EDGE_HEX.mentions, // cool blue
  EDGE_HEX.discussed_with, // warm green
]

// Fixed placement behind and around the structure (graph spans ~±15; camera at
// z≈28). Large scale + low alpha → soft background gas, not foreground blobs.
const CLOUD_LAYOUT: { position: [number, number, number]; scale: number }[] = [
  { position: [-22, 8, -30], scale: 56 },
  { position: [26, -6, -34], scale: 48 },
  { position: [-14, -18, -26], scale: 42 },
  { position: [18, 16, -38], scale: 52 },
]

/** Peak center alpha — the "≤4%" brand cap. */
const PEAK_ALPHA = 0.04

function makeNebulaTexture(hex: string): CanvasTexture {
  const size = 256
  const canvas = document.createElement("canvas")
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext("2d")
  const [rf, gf, bf] = hexToRgba(hex)
  const r = Math.round(rf * 255)
  const g = Math.round(gf * 255)
  const b = Math.round(bf * 255)
  if (ctx) {
    const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
    grad.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${PEAK_ALPHA})`)
    grad.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${PEAK_ALPHA * 0.5})`)
    grad.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`)
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, size, size)
  }
  return new CanvasTexture(canvas)
}

export function NebulaBackdrop() {
  const clouds = useMemo(
    () =>
      CLOUD_LAYOUT.map((c, i) => ({
        ...c,
        texture: makeNebulaTexture(NEBULA_TINTS[i % NEBULA_TINTS.length]),
      })),
    [],
  )

  useEffect(() => {
    return () => {
      for (const c of clouds) c.texture.dispose()
    }
  }, [clouds])

  return (
    <group>
      {clouds.map((c, i) => (
        <sprite key={i} position={c.position} scale={[c.scale, c.scale, 1]}>
          <spriteMaterial
            map={c.texture}
            transparent
            depthWrite={false}
            blending={AdditiveBlending}
          />
        </sprite>
      ))}
    </group>
  )
}
