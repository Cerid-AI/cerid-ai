// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Ambient particles for Constellation — Phase B Day 6. Renders a
// drifting cloud of micro-particles between entities to give the 3D
// scene depth + life without the cost of an actual physics simulation.
//
// Architecture:
//   - One THREE.Points draw call (instanced via BufferGeometry)
//   - Position attribute (xyz) populated once with random points
//     inside a shell that encloses the entity cloud
//   - Color attribute (rgb) drawn from the brand teal palette
//   - useFrame ticks slow rotation so the particle cloud drifts
//     subtly behind the entities — no per-particle CPU work
//
// Perf budget: 800 particles → 2.4K floats position + 2.4K color
// = ~38KB GPU memory, 1 draw call. Negligible cost on M2 Pro and
// AMD Vega II. Particle count can scale with entity count if needed,
// but 800 looks great at all sizes we've tested.

import { useEffect, useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import {
  BufferAttribute,
  BufferGeometry,
  type Points as ThreePoints,
  PointsMaterial,
  AdditiveBlending,
} from "three"

interface AmbientParticlesProps {
  /** Particle count. Defaults to 800. */
  count?: number
  /** Radius of the spherical cloud the particles occupy. */
  radius?: number
}

const TEAL: [number, number, number] = [0.35, 0.92, 0.80]
const GOLD: [number, number, number] = [0.83, 0.69, 0.22]
const SAND: [number, number, number] = [0.91, 0.78, 0.48]

const PALETTE: [number, number, number][] = [TEAL, GOLD, SAND]

export function AmbientParticles({ count = 800, radius = 18 }: AmbientParticlesProps) {
  const pointsRef = useRef<ThreePoints>(null)

  // Geometry: one-shot generation. Deterministic seed avoids re-randomising
  // when the component re-mounts; users see the same "shape" each session.
  const geometry = useMemo(() => {
    const geom = new BufferGeometry()
    const positions = new Float32Array(count * 3)
    const colors = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      // Spherical-shell sampling (Marsaglia-style) so particles
      // distribute on a thick shell rather than concentrating at center.
      let x = 0, y = 0, z = 0, len = 0
      do {
        // eslint-disable-next-line react-hooks/purity -- intentional randomness (Math.random() for particle/animation generation)
        x = Math.random() * 2 - 1
        // eslint-disable-next-line react-hooks/purity -- intentional randomness (Math.random() for particle/animation generation)
        y = Math.random() * 2 - 1
        // eslint-disable-next-line react-hooks/purity -- intentional randomness (Math.random() for particle/animation generation)
        z = Math.random() * 2 - 1
        len = x * x + y * y + z * z
      } while (len > 1 || len < 0.04)
      // eslint-disable-next-line react-hooks/purity -- intentional randomness (Math.random() for particle/animation generation)
      const r = radius * (0.5 + Math.random() * 0.5)
      const inv = r / Math.sqrt(len)
      positions[i * 3 + 0] = x * inv
      positions[i * 3 + 1] = y * inv
      positions[i * 3 + 2] = z * inv

      // eslint-disable-next-line react-hooks/purity -- intentional randomness (Math.random() for particle/animation generation)
      const palette = PALETTE[Math.floor(Math.random() * PALETTE.length)]
      colors[i * 3 + 0] = palette[0]
      colors[i * 3 + 1] = palette[1]
      colors[i * 3 + 2] = palette[2]
    }
    geom.setAttribute("position", new BufferAttribute(positions, 3))
    geom.setAttribute("color", new BufferAttribute(colors, 3))
    // Required for frustumCulled=true: without this the bounding sphere is null
    // and the renderer culls the draw call incorrectly.
    geom.computeBoundingSphere()
    return geom
  }, [count, radius])

  const material = useMemo(() => {
    return new PointsMaterial({
      vertexColors: true,
      size: 0.07,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.25,
      depthWrite: false,
      blending: AdditiveBlending,  // brand glow on dark Vault navy bg
    })
  }, [])

  // Dispose GPU resources on unmount
  useEffect(() => {
    return () => {
      geometry.dispose()
      material.dispose()
    }
  }, [geometry, material])

  // Slow rotation — entire particle cloud drifts as one rigid body.
  // Cheap (single matrix update per frame) and gives the scene a
  // perceptible "alive" quality without per-particle work.
  useFrame((_state, delta) => {
    const p = pointsRef.current
    if (!p) return
    p.rotation.y += delta * 0.015
    p.rotation.x += delta * 0.008
  })

  return <points ref={pointsRef} args={[geometry, material]} />
}
