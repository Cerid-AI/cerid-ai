// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Neural linkage renderer for Constellation. One LineSegments draw call
// for all edges, with a custom additive shader that gives the graph its
// nervous-system quality:
//
//   - Base lines: community-tinted strands (gradient from source community
//     color to target community color along each edge). co_mention edges
//     render brighter (teal-neutral); similar edges render dimmer/cooler.
//   - Synaptic pulses: a bright teal-white band travels source→target
//     on each edge, phase-offset per edge so the whole graph shimmers
//     with asynchronous firing instead of strobing in lockstep.
//   - Organic growth: each edge draws itself in source→target after its
//     birth time. New edges arriving on a corpus refetch get fresh birth
//     times, so the web visibly grows where the knowledge grew.
//
// Kind distinction: aIsSimilar=0 → co_mention (base alpha × 1.0, full
// weight floor); aIsSimilar=1 → similar (dimmer, cooler tone). Passed as
// a per-vertex float attribute so the shader can branch without a uniform
// change per-draw.
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
import type { Vec3 } from "./drag-plane"
import { FLOAT3_GLSL } from "./float3"
import { communityRgb, hash01 } from "./palette"

export interface NeuralLinksProps {
  entities: EntityEmbedding3D[]
  /** [sourceIdx, targetIdx, weight, kind] 4-tuples indexing into entities; kind is "co_mention" or "similar" */
  links: [number, number, number, string][]
  /** When false (reduced motion), edges render fully grown and pulses freeze. */
  animate?: boolean
  /** Quality toggle: synaptic pulse animation on/off (growth unaffected). */
  pulses?: boolean
  /** Quality toggle: organic per-node float around the fixed UMAP seed. */
  float?: boolean
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
  /** Transient drag-pin overrides from InstancedNodes (entity id → world position). */
  pinnedPositions?: Map<string, Vec3>
  /** Bumped on every pinnedPositions change; the trigger for the incident-edge patch effect below (avoids a full geometry rebuild per drag-move). */
  pinVersion?: number
}

const VERTEX_SHADER = /* glsl */ `
  ${FLOAT3_GLSL}

  attribute vec3 aColor;
  attribute float aT;
  attribute float aSeed;
  attribute float aWeight;
  attribute float aBirth;
  attribute float aDim;
  attribute float aIsSimilar;
  attribute float aNodeSeed;

  uniform float uTime;
  uniform float uFloatAmp;

  varying vec3 vColor;
  varying float vT;
  varying float vSeed;
  varying float vWeight;
  varying float vBirth;
  varying float vDim;
  varying float vIsSimilar;

  void main() {
    vColor = aColor;
    vT = aT;
    vSeed = aSeed;
    vWeight = aWeight;
    vBirth = aBirth;
    vDim = aDim;
    vIsSimilar = aIsSimilar;
    vec3 fpos = position + float3(aNodeSeed, uTime, uFloatAmp);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(fpos, 1.0);
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
  varying float vIsSimilar;

  void main() {
    // Growth: the edge draws in from source (t=0) to target (t=1) over
    // ~0.9s starting at its birth time. Fully grown edges pay only the
    // clamp.
    float grow = clamp((uTime - vBirth) / 0.9, 0.0, 1.0);
    float drawn = 1.0 - smoothstep(grow - 0.12, grow, vT);
    if (drawn <= 0.001) discard;

    // Kind-aware weight floor: co_mention edges are the relational signal
    // so they get a higher floor (0.5). similar edges are secondary and
    // read as a calm cooler/dimmer secondary tone (floor 0.35, scale 0.55).
    float weightFloor = mix(0.5, 0.35, vIsSimilar);
    float weightScale = mix(0.5, 0.65, vIsSimilar);
    float kindAlphaScale = mix(1.0, 0.55, vIsSimilar);

    // Base strand: stronger edges are more present, but everything stays
    // legible — the pulses carry the energy. vDim implements neighborhood
    // focus: 1 = neutral, >1 = highlighted (hover), <1 = receded.
    float alpha = uBaseAlpha * (weightFloor + weightScale * vWeight) * drawn * vDim * kindAlphaScale;

    // Clamp max alpha so hub nodes with many edges don't smear into a blob.
    alpha = min(alpha, 0.72);

    // Synaptic pulse: a narrow band travels 0→1, speed and phase vary
    // per edge. Brighter + whiter at the band's core.
    // Cap: pulse never exceeds the base alpha it rides on (capped at alpha).
    float speed = 0.10 + 0.14 * vSeed;
    float p = fract(uTime * speed + vSeed * 7.31);
    float band = 1.0 - smoothstep(0.0, 0.055, abs(vT - p));
    float rawPulse = band * band * uPulseAmp * (0.45 + 0.55 * vWeight) * min(vDim, 1.6);
    float pulse = min(rawPulse, alpha);

    // similar edges get a cooler/dimmer pulse color (cooler teal-blue tone);
    // co_mention gets the warm teal-white pulse.
    vec3 coMentionPulseColor = mix(vColor, vec3(0.55, 1.0, 0.88), 0.75);
    vec3 similarPulseColor   = mix(vColor, vec3(0.45, 0.72, 0.95), 0.60);
    vec3 pulseColor = mix(coMentionPulseColor, similarPulseColor, vIsSimilar);

    vec3 color = vColor * alpha + pulseColor * pulse;

    gl_FragColor = vec4(color, min(alpha + pulse, 1.0));
  }
`

/**
 * aDim value applied to edges that are NOT connected to the hovered/selected
 * node. Decisive fade so the focal-node subgraph pops clearly.
 * Exported for unit tests.
 */
export const NON_NEIGHBOR_EDGE_DIM = 0.06

/**
 * Maps a link `kind` string to the per-vertex `aIsSimilar` shader attribute
 * value: 0.0 for co_mention (primary, brighter), 1.0 for similar (secondary,
 * dimmer/cooler). Pure function — extracted so tests can verify the mapping
 * without spinning up WebGL geometry.
 */
export function kindToIsSimilar(kind: string): number {
  return kind === "similar" ? 1.0 : 0.0
}

export function NeuralLinks({
  entities,
  links,
  animate = true,
  pulses = true,
  float = false,
  hoveredIndex = null,
  colors: lensColors,
  visibility,
  pinnedPositions,
  pinVersion = 0,
}: NeuralLinksProps) {
  const lineRef = useRef<ThreeLineSegments>(null)
  // Snapshot of pinnedPositions as of the last-applied pinVersion, so the
  // patch effect below only touches edges whose endpoint actually moved
  // rather than re-walking every link on each drag-move.
  const appliedPins = useRef<Map<string, Vec3>>(new Map())
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
    const isSimilarArr = new Float32Array(n * 2)
    const nodeSeeds = new Float32Array(n * 2)

    let maxW = 1
    for (const [, , w] of links) maxW = Math.max(maxW, w)

    let newEdgeRank = 0
    for (let i = 0; i < n; i++) {
      const [si, ti, w, kind] = links[i]
      const s = entities[si]
      const t = entities[ti]
      if (!s || !t) continue

      // A refetch rebuilds this geometry from the server-seeded entities
      // array; an endpoint still pinned by a drag keeps its dropped
      // position instead of snapping back mid-session.
      const sPin = pinnedPositions?.get(s.id)
      const tPin = pinnedPositions?.get(t.id)
      const o = i * 6
      positions[o + 0] = sPin ? sPin[0] : s.x; positions[o + 1] = sPin ? sPin[1] : s.y; positions[o + 2] = sPin ? sPin[2] : s.z
      positions[o + 3] = tPin ? tPin[0] : t.x; positions[o + 4] = tPin ? tPin[1] : t.y; positions[o + 5] = tPin ? tPin[2] : t.z

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

      // Per-vertex node seed (NOT the edge seed above) — each endpoint uses
      // its own node's hash01(id) so it floats in lock-step with that node's
      // sphere + glow, which key off the same value.
      nodeSeeds[v] = hash01(s.id); nodeSeeds[v + 1] = hash01(t.id)

      // log-normalized weight: most co-mentions are 1; the few heavy
      // pairs (e.g. SOL↔ETH at 39) should read clearly without
      // blowing out the scene.
      const wNorm = Math.log1p(w) / Math.log1p(maxW)
      weights[v] = wNorm; weights[v + 1] = wNorm

      // Kind attribute: 0 = co_mention (primary), 1 = similar (secondary/dimmer).
      const isSimilar = kindToIsSimilar(kind)
      isSimilarArr[v] = isSimilar; isSimilarArr[v + 1] = isSimilar

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
    geom.setAttribute("aIsSimilar", new BufferAttribute(isSimilarArr, 1))
    geom.setAttribute("aNodeSeed", new BufferAttribute(nodeSeeds, 1))
    // Required for frustumCulled=true: computes a tight bounding sphere over
    // all line endpoints so the renderer can skip the draw call when the graph
    // is scrolled fully off-screen.
    geom.computeBoundingSphere()
    // This build already baked in the current pinnedPositions (above) — sync
    // the applied-snapshot so the patch effect below doesn't re-diff them.
    appliedPins.current = new Map(pinnedPositions ?? [])
    return geom
  }, [entities, links, lensColors, pinnedPositions])

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
        v = (si === hoveredIndex || ti === hoveredIndex) ? Math.max(v, 1) * 3.0 : Math.min(v, NON_NEIGHBOR_EDGE_DIM)
      }
      arr[i * 2] = v
      arr[i * 2 + 1] = v
    }
    dim.needsUpdate = true
  }, [hoveredIndex, links, geometry, visibility])

  // Entity id → the (edgeIdx, endpoint-slot) pairs it's incident to, so a
  // drag-move can patch just the moved node's edges without walking the
  // full link list.
  const incidentEdges = useMemo(() => {
    const map = new Map<string, Array<{ edgeIdx: number; slot: 0 | 1 }>>()
    for (let i = 0; i < links.length; i++) {
      const [si, ti] = links[i]
      const sId = entities[si]?.id
      const tId = entities[ti]?.id
      if (sId) {
        const arr = map.get(sId) ?? []
        arr.push({ edgeIdx: i, slot: 0 })
        map.set(sId, arr)
      }
      if (tId) {
        const arr = map.get(tId) ?? []
        arr.push({ edgeIdx: i, slot: 1 })
        map.set(tId, arr)
      }
    }
    return map
  }, [entities, links])

  // Edge-follow during a drag: patch ONLY the moved node's incident
  // endpoints in the position attribute — not a full geometry rebuild.
  // pinVersion is the reactive trigger (bumped by the parent on every
  // drag-move); diffing against appliedPins finds which id(s) actually
  // moved since the last patch, bounding the work to that node's edges.
  useEffect(() => {
    if (!pinnedPositions) return
    const posAttr = geometry.getAttribute("position") as BufferAttribute | undefined
    if (!posAttr) return
    let touched = false
    for (const [id, pos] of pinnedPositions) {
      const prev = appliedPins.current.get(id)
      if (prev && prev[0] === pos[0] && prev[1] === pos[1] && prev[2] === pos[2]) continue
      const edges = incidentEdges.get(id)
      if (edges) {
        for (const { edgeIdx, slot } of edges) {
          posAttr.setXYZ(edgeIdx * 2 + slot, pos[0], pos[1], pos[2])
        }
        touched = true
      }
    }
    if (touched) posAttr.needsUpdate = true
    appliedPins.current = new Map(pinnedPositions)
    // pinVersion is the intended trigger; pinnedPositions is read (not a
    // reactive dep) since it's the same mutated-in-place Map each drag-move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinVersion])

  const material = useMemo(() => {
    return new ShaderMaterial({
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      uniforms: {
        uTime: { value: animate ? 0 : 1000 },
        // Cap at 0.7 so pulses never exceed the base alpha they ride on
        // (the fragment shader clamps pulse to alpha, but lowering uPulseAmp
        // prevents over-bright bursts on already-bright hub edges).
        uPulseAmp: { value: animate ? 0.7 : 0.0 },
        uBaseAlpha: { value: 0.28 },
        uFloatAmp: { value: 0 },
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
    material.uniforms.uPulseAmp.value = animate && pulses ? 0.7 : 0.0
  }, [material, animate, pulses])

  // Single uniform tick — the GPU does everything else.
  useFrame(({ clock }) => {
    if (!animate) return
    if (clockStart.current === null) clockStart.current = clock.elapsedTime
    matTime.current = clock.elapsedTime - clockStart.current
    material.uniforms.uTime.value = matTime.current
    const floatOn = animate && float === true
    material.uniforms.uFloatAmp.value = floatOn ? 1 : 0
  })

  if (links.length === 0) return null

  return <lineSegments ref={lineRef} args={[geometry, material]} />
}
