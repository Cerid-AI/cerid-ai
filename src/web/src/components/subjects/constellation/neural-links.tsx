// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Neural linkage renderer for Constellation. One LineSegments draw call
// for all CO_MENTIONED edges, with a custom additive shader that gives
// the graph its nervous-system quality:
//
//   - Base lines: faint community-tinted strands (gradient from source
//     community color to target community color along each edge).
//   - Synaptic pulses: a bright teal-white band travels source→target
//     on each edge, phase-offset per edge so the whole graph shimmers
//     with asynchronous firing instead of strobing in lockstep.
//   - Organic growth: each edge draws itself in source→target after its
//     birth time. New edges arriving on a corpus refetch get fresh birth
//     times, so the web visibly grows where the knowledge grew.
//
// Perf: ~16K edges = 32K vertices, ONE draw call, zero per-frame CPU
// work (the only mutation is the uTime uniform). Additive blending +
// depthWrite:false keeps overdraw cheap and sorts-free.

import { useEffect, useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  type LineSegments as ThreeLineSegments,
  ShaderMaterial,
} from "three"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"
import { communityRgb, hash01 } from "./palette"

export interface NeuralLinksProps {
  entities: EntityEmbedding3D[]
  /** [sourceIdx, targetIdx, weight] triples indexing into entities */
  links: [number, number, number][]
  /** When false (reduced motion), edges render fully grown and pulses freeze. */
  animate?: boolean
  /** Quality toggle: synaptic pulse animation on/off (growth unaffected). */
  pulses?: boolean
  /**
   * Hovered entity index (into entities) or null. Edges touching the
   * hovered node brighten; everything else recedes — the Obsidian
   * neighborhood-focus interaction.
   */
  hoveredIndex?: number | null
  /** Lens colors (n×3 RGB per entity). Falls back to community colors. */
  colors?: Float32Array
  /** Per-entity visibility — an edge fades with its dimmest endpoint. */
  visibility?: Float32Array
}

const VERTEX_SHADER = /* glsl */ `
  attribute vec3 aColor;
  attribute float aT;
  attribute float aSeed;
  attribute float aWeight;
  attribute float aBirth;
  attribute float aDim;

  varying vec3 vColor;
  varying float vT;
  varying float vSeed;
  varying float vWeight;
  varying float vBirth;
  varying float vDim;

  void main() {
    vColor = aColor;
    vT = aT;
    vSeed = aSeed;
    vWeight = aWeight;
    vBirth = aBirth;
    vDim = aDim;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

const FRAGMENT_SHADER = /* glsl */ `
  uniform float uTime;
  uniform float uPulseAmp;
  uniform float uBaseAlpha;

  varying vec3 vColor;
  varying float vT;
  varying float vSeed;
  varying float vWeight;
  varying float vBirth;
  varying float vDim;

  void main() {
    // Growth: the edge draws in from source (t=0) to target (t=1) over
    // ~0.9s starting at its birth time. Fully grown edges pay only the
    // clamp.
    float grow = clamp((uTime - vBirth) / 0.9, 0.0, 1.0);
    float drawn = 1.0 - smoothstep(grow - 0.12, grow, vT);
    if (drawn <= 0.001) discard;

    // Base strand: stronger edges are more present, but everything stays
    // calm — the pulses carry the energy. vDim implements neighborhood
    // focus: 1 = neutral, >1 = highlighted (hover), <1 = receded.
    float alpha = uBaseAlpha * (0.35 + 0.65 * vWeight) * drawn * vDim;

    // Synaptic pulse: a narrow band travels 0→1, speed and phase vary
    // per edge. Brighter + whiter at the band's core.
    float speed = 0.10 + 0.14 * vSeed;
    float p = fract(uTime * speed + vSeed * 7.31);
    float band = 1.0 - smoothstep(0.0, 0.055, abs(vT - p));
    float pulse = band * band * uPulseAmp * (0.45 + 0.55 * vWeight) * min(vDim, 1.6);

    vec3 pulseColor = mix(vColor, vec3(0.55, 1.0, 0.88), 0.75);
    vec3 color = vColor * alpha + pulseColor * pulse;

    gl_FragColor = vec4(color, min(alpha + pulse, 1.0));
  }
`

export function NeuralLinks({
  entities,
  links,
  animate = true,
  pulses = true,
  hoveredIndex = null,
  colors: lensColors,
  visibility,
}: NeuralLinksProps) {
  const lineRef = useRef<ThreeLineSegments>(null)
  // Edge keys already shown — edges that survive a refetch must NOT
  // re-grow; only genuinely new linkage animates in (Obsidian-style).
  const seenEdges = useRef<Set<string>>(new Set())
  const clockStart = useRef<number | null>(null)
  // Current shader-clock time, read by the geometry build so edges that
  // arrive mid-session get birth times in the present, not at t=0.
  const matTime = useRef(0)

  const geometry = useMemo(() => {
    const n = links.length
    const geom = new BufferGeometry()
    const positions = new Float32Array(n * 2 * 3)
    const colors = new Float32Array(n * 2 * 3)
    const ts = new Float32Array(n * 2)
    const seeds = new Float32Array(n * 2)
    const weights = new Float32Array(n * 2)
    const births = new Float32Array(n * 2)

    let maxW = 1
    for (const [, , w] of links) maxW = Math.max(maxW, w)

    let newEdgeRank = 0
    for (let i = 0; i < n; i++) {
      const [si, ti, w] = links[i]
      const s = entities[si]
      const t = entities[ti]
      if (!s || !t) continue

      const o = i * 6
      positions[o + 0] = s.x; positions[o + 1] = s.y; positions[o + 2] = s.z
      positions[o + 3] = t.x; positions[o + 4] = t.y; positions[o + 5] = t.z

      if (lensColors) {
        colors[o + 0] = lensColors[si * 3]; colors[o + 1] = lensColors[si * 3 + 1]; colors[o + 2] = lensColors[si * 3 + 2]
        colors[o + 3] = lensColors[ti * 3]; colors[o + 4] = lensColors[ti * 3 + 1]; colors[o + 5] = lensColors[ti * 3 + 2]
      } else {
        const [sr, sg, sb] = communityRgb(s.community)
        const [tr, tg, tb] = communityRgb(t.community)
        colors[o + 0] = sr; colors[o + 1] = sg; colors[o + 2] = sb
        colors[o + 3] = tr; colors[o + 4] = tg; colors[o + 5] = tb
      }

      const v = i * 2
      ts[v] = 0; ts[v + 1] = 1

      const key = `${s.id}|${t.id}`
      const seed = hash01(key)
      seeds[v] = seed; seeds[v + 1] = seed

      // log-normalized weight: most co-mentions are 1; the few heavy
      // pairs (e.g. SOL↔ETH at 39) should read clearly without
      // blowing out the scene.
      const wNorm = Math.log1p(w) / Math.log1p(maxW)
      weights[v] = wNorm; weights[v + 1] = wNorm

      // Birth: previously seen edges are born in the past (instantly
      // grown); new edges stagger in over ~2.5s in discovery order,
      // anchored to the CURRENT shader clock so mid-session corpus
      // growth animates instead of popping in fully drawn.
      let birth = -10
      if (!seenEdges.current.has(key)) {
        seenEdges.current.add(key)
        birth = matTime.current + 0.15 + (newEdgeRank++) * 0.0008 + seed * 0.4
      }
      births[v] = birth; births[v + 1] = birth
    }

    geom.setAttribute("position", new BufferAttribute(positions, 3))
    geom.setAttribute("aColor", new BufferAttribute(colors, 3))
    geom.setAttribute("aT", new BufferAttribute(ts, 1))
    geom.setAttribute("aSeed", new BufferAttribute(seeds, 1))
    geom.setAttribute("aWeight", new BufferAttribute(weights, 1))
    geom.setAttribute("aBirth", new BufferAttribute(births, 1))
    geom.setAttribute("aDim", new BufferAttribute(new Float32Array(n * 2).fill(1), 1))
    return geom
  }, [entities, links, lensColors])

  // Neighborhood focus + lens visibility: edges touching the hovered
  // node brighten, the rest recede; an edge whose endpoint is filtered
  // out fades with it. One Float32Array rewrite per change (~32K floats).
  useEffect(() => {
    const dim = geometry.getAttribute("aDim") as BufferAttribute | undefined
    if (!dim) return
    const arr = dim.array as Float32Array
    for (let i = 0; i < links.length; i++) {
      const [si, ti] = links[i]
      let v = Math.min(visibility?.[si] ?? 1, visibility?.[ti] ?? 1)
      if (hoveredIndex !== null && hoveredIndex !== undefined) {
        v = (si === hoveredIndex || ti === hoveredIndex) ? Math.max(v, 1) * 3.0 : Math.min(v, 0.12)
      }
      arr[i * 2] = v
      arr[i * 2 + 1] = v
    }
    dim.needsUpdate = true
  }, [hoveredIndex, links, geometry, visibility])

  const material = useMemo(() => {
    return new ShaderMaterial({
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      uniforms: {
        uTime: { value: animate ? 0 : 1000 },
        uPulseAmp: { value: animate ? 1.0 : 0.0 },
        uBaseAlpha: { value: 0.12 },
      },
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
    })
    // animate is intentionally captured at creation: a live toggle of the
    // OS reduced-motion setting re-mounts the canvas anyway.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    return () => {
      geometry.dispose()
      material.dispose()
    }
  }, [geometry, material])

  // Quality tier can flip pulses without remounting the geometry.
  useEffect(() => {
    material.uniforms.uPulseAmp.value = animate && pulses ? 1.0 : 0.0
  }, [material, animate, pulses])

  // Single uniform tick — the GPU does everything else.
  useFrame(({ clock }) => {
    if (!animate) return
    if (clockStart.current === null) clockStart.current = clock.elapsedTime
    matTime.current = clock.elapsedTime - clockStart.current
    material.uniforms.uTime.value = matTime.current
  })

  if (links.length === 0) return null

  return <lineSegments ref={lineRef} args={[geometry, material]} frustumCulled={false} />
}
