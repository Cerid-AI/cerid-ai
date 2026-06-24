// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// InstancedMesh node renderer — Phase B Day 5, upgraded for the living
// neural-net redesign. Still N entities in ONE draw call, plus:
//
//   - Organic growth: each node scales in with an ease-out-back pop,
//     staggered per node. Diff-aware — when a corpus refetch adds
//     entities, only the NEW ones grow in (Obsidian-style); existing
//     nodes hold steady. Matrix updates run ONLY during an active
//     growth window, so the static-scene perf win is preserved.
//   - Glow halos: a second draw call (THREE.Points + radial-falloff
//     shader, additive) gives every node a soft community-colored
//     bloom without a postprocessing dependency — keeps the bundle
//     inside the CI cap and avoids fullscreen passes on AMD-Mac/ANGLE.
//   - Breathing: halo size oscillates a few percent per node in the
//     vertex shader (zero CPU cost). Disabled under reduced motion.

import { useEffect, useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  InstancedMesh,
  MeshStandardMaterial,
  Object3D,
  type Points as ThreePoints,
  ShaderMaterial,
  SphereGeometry,
} from "three"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"
import { communityRgb, degreeRadius, hash01 } from "./palette"

const GROW_DURATION_S = 0.9

// ease-out-back: overshoots ~10% then settles — the organic "pop".
function easeOutBack(x: number): number {
  const c1 = 1.70158
  const c3 = c1 + 1
  const t = x - 1
  return 1 + c3 * t * t * t + c1 * t * t
}

// ---------------------------------------------------------------------------
// Glow halo shaders (Points layer)
// ---------------------------------------------------------------------------

const GLOW_VERTEX = /* glsl */ `
  attribute vec3 aColor;
  attribute float aSize;
  attribute float aSeed;
  attribute float aBirth;
  attribute float aDim;

  uniform float uTime;
  uniform float uBreatheAmp;

  varying vec3 vColor;
  varying float vAlive;
  varying float vDim;

  void main() {
    vColor = aColor;
    vDim = aDim;
    float grow = clamp((uTime - aBirth) / ${GROW_DURATION_S.toFixed(1)}, 0.0, 1.0);
    vAlive = grow;
    float breathe = 1.0 + uBreatheAmp * 0.07 * sin(uTime * 1.3 + aSeed * 6.2831);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    // Clamp: an unclamped attenuated point size explodes to a
    // screen-filling wash when the camera passes near a node.
    gl_PointSize = min(aSize * grow * breathe * (160.0 / max(1.0, -mv.z)), 64.0);
    gl_Position = projectionMatrix * mv;
  }
`

const GLOW_FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vAlive;
  varying float vDim;

  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv) * 2.0;
    if (d > 1.0) discard;
    // Soft radial falloff with a hot core
    float halo = pow(1.0 - d, 2.6);
    float core = pow(max(0.0, 1.0 - d * 3.2), 2.0);
    vec3 color = vColor * halo * 0.55 + vec3(0.85, 1.0, 0.95) * core * 0.5;
    gl_FragColor = vec4(color * vAlive * vDim, halo * 0.5 * vAlive * min(vDim, 1.0));
  }
`

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface InstancedNodesProps {
  entities: EntityEmbedding3D[]
  onSelect?: (entityId: string) => void
  /** When false (reduced motion), nodes render full-size and halos hold still. */
  animate?: boolean
  /** Quality toggle: render the additive glow-halo layer. */
  glow?: boolean
  /** Quality toggle: halo breathing animation. */
  pulses?: boolean
  /** Hovered entity index, or null. Drives neighborhood focus dimming. */
  hoveredIndex?: number | null
  /** Adjacency (entity idx → neighbor idx set) for neighborhood focus. */
  neighbors?: Map<number, Set<number>>
  /** Per-entity connection degree — node size encodes centrality. */
  degrees?: Float32Array
  /** Lens colors (n×3 RGB). Falls back to community colors when absent. */
  colors?: Float32Array
  /** Per-entity visibility (1 = shown, ~0.06 = filtered out by a lens). */
  visibility?: Float32Array
  /** Pointer entered/moved over a node: (index, clientX, clientY). */
  onHover?: (index: number | null, clientX?: number, clientY?: number) => void
}

export function InstancedNodes({
  entities,
  onSelect,
  animate = true,
  glow = false,
  pulses = false,
  hoveredIndex = null,
  neighbors,
  degrees,
  colors: lensColors,
  visibility,
  onHover,
}: InstancedNodesProps) {
  const meshRef = useRef<InstancedMesh>(null)
  const glowRef = useRef<ThreePoints>(null)

  // Birth times per entity id — survives refetches so existing nodes
  // never re-animate; cleared only on unmount.
  const births = useRef<Map<string, number>>(new Map())
  const matTime = useRef(0)
  const clockStart = useRef<number | null>(null)
  const growthEndsAt = useRef(0)

  // Stable geometry + material for the sphere layer.
  const geometry = useMemo(() => new SphereGeometry(1, 12, 12), [])
  const material = useMemo(() => {
    return new MeshStandardMaterial({
      vertexColors: false,
      roughness: 0.5,
      metalness: 0.2,
      emissive: new Color(0.18, 0.22, 0.28),
      emissiveIntensity: 0.4,
    })
  }, [])

  // Assign birth times for newly seen entities; anchor to the current
  // shader clock so mid-session arrivals grow in the present.
  useMemo(() => {
    let newRank = 0
    for (const ent of entities) {
      if (!births.current.has(ent.id)) {
        const birth = animate
          ? matTime.current + 0.1 + newRank * 0.0006 + hash01(ent.id) * 0.6
          : -10
        births.current.set(ent.id, birth)
        newRank++
      }
    }
    if (newRank > 0) {
      growthEndsAt.current = Math.max(
        growthEndsAt.current,
        matTime.current + 0.7 + newRank * 0.0006 + GROW_DURATION_S,
      )
    }
  }, [entities, animate])

  // Glow layer geometry + material — rebuilt with the entity list.
  const glowGeometry = useMemo(() => {
    const n = entities.length
    const geom = new BufferGeometry()
    const positions = new Float32Array(n * 3)
    const colors = new Float32Array(n * 3)
    const sizes = new Float32Array(n)
    const seeds = new Float32Array(n)
    const birthsAttr = new Float32Array(n)
    for (let i = 0; i < n; i++) {
      const ent = entities[i]
      positions[i * 3 + 0] = ent.x
      positions[i * 3 + 1] = ent.y
      positions[i * 3 + 2] = ent.z
      if (lensColors) {
        colors[i * 3 + 0] = lensColors[i * 3]
        colors[i * 3 + 1] = lensColors[i * 3 + 1]
        colors[i * 3 + 2] = lensColors[i * 3 + 2]
      } else {
        const [r, g, b] = communityRgb(ent.community)
        colors[i * 3 + 0] = r; colors[i * 3 + 1] = g; colors[i * 3 + 2] = b
      }
      sizes[i] = degreeRadius(degrees?.[i] ?? 0) * 10.0
      seeds[i] = hash01(ent.id)
      birthsAttr[i] = births.current.get(ent.id) ?? -10
    }
    geom.setAttribute("position", new BufferAttribute(positions, 3))
    geom.setAttribute("aColor", new BufferAttribute(colors, 3))
    geom.setAttribute("aSize", new BufferAttribute(sizes, 1))
    geom.setAttribute("aSeed", new BufferAttribute(seeds, 1))
    geom.setAttribute("aBirth", new BufferAttribute(birthsAttr, 1))
    geom.setAttribute("aDim", new BufferAttribute(new Float32Array(n).fill(1), 1))
    return geom
  }, [entities, degrees, lensColors])

  // Glow-side neighborhood focus + lens visibility — mirrors the sphere
  // brightness pass.
  useEffect(() => {
    const dim = glowGeometry.getAttribute("aDim") as BufferAttribute | undefined
    if (!dim) return
    const arr = dim.array as Float32Array
    const hood = hoveredIndex !== null && hoveredIndex !== undefined
      ? neighbors?.get(hoveredIndex)
      : undefined
    for (let i = 0; i < entities.length; i++) {
      let v = visibility?.[i] ?? 1
      if (hoveredIndex !== null && hoveredIndex !== undefined) {
        v = i === hoveredIndex ? 1.8 : hood?.has(i) ? Math.min(v * 1.2, 1.2) : Math.min(v, 0.4)
      }
      arr[i] = v
    }
    dim.needsUpdate = true
  }, [hoveredIndex, neighbors, entities, glowGeometry, visibility])

  const glowMaterial = useMemo(() => {
    return new ShaderMaterial({
      vertexShader: GLOW_VERTEX,
      fragmentShader: GLOW_FRAGMENT,
      uniforms: {
        uTime: { value: animate ? 0 : 1000 },
        uBreatheAmp: { value: animate ? 1.0 : 0.0 },
      },
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
    })
    // animate captured at creation — an OS-level toggle re-mounts the canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    return () => {
      geometry.dispose()
      material.dispose()
      glowGeometry.dispose()
      glowMaterial.dispose()
    }
  }, [geometry, material, glowGeometry, glowMaterial])

  // Quality tier can flip breathing without remounting.
  useEffect(() => {
    glowMaterial.uniforms.uBreatheAmp.value = animate && pulses ? 1.0 : 0.0
  }, [glowMaterial, animate, pulses])

  const dummy = useMemo(() => new Object3D(), [])
  const tmpColor = useMemo(() => new Color(), [])

  // Write the full matrix + color set for the current scale state.
  // Hover focus folds into the same pass: the hovered node + its
  // neighbors keep full color (hovered slightly enlarged); the rest
  // darken toward the background so the active neighborhood pops.
  const writeInstances = (t: number) => {
    const mesh = meshRef.current
    if (!mesh) return
    const hood = hoveredIndex !== null && hoveredIndex !== undefined
      ? neighbors?.get(hoveredIndex)
      : undefined
    for (let i = 0; i < entities.length; i++) {
      const ent = entities[i]
      const radius = degreeRadius(degrees?.[i] ?? 0)
      const birth = births.current.get(ent.id) ?? -10
      const grow = animate ? Math.min(1, Math.max(0, (t - birth) / GROW_DURATION_S)) : 1
      let scale = radius * (grow >= 1 ? 1 : Math.max(0.0001, easeOutBack(grow)))
      let brightness = visibility?.[i] ?? 1
      if (hoveredIndex !== null && hoveredIndex !== undefined) {
        if (i === hoveredIndex) scale *= 1.35
        else if (!hood?.has(i)) brightness = Math.min(brightness, 0.4)
      }
      dummy.position.set(ent.x, ent.y, ent.z)
      dummy.scale.set(scale, scale, scale)
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)

      let r: number, g: number, b: number
      if (lensColors) {
        r = lensColors[i * 3]; g = lensColors[i * 3 + 1]; b = lensColors[i * 3 + 2]
      } else {
        ;[r, g, b] = communityRgb(ent.community)
      }
      tmpColor.setRGB(r * brightness, g * brightness, b * brightness)
      mesh.setColorAt(i, tmpColor)
    }
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  }

  // Initial population, on entity-list change, and on hover-focus change.
  useEffect(() => {
    writeInstances(matTime.current)
    // writeInstances reads only refs + the props captured here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entities, hoveredIndex, neighbors, degrees, lensColors, visibility])

  // Per-frame: tick the glow clock (uniform only) and, while a growth
  // window is active, re-write sphere matrices. Outside the window the
  // scene is static and this is a two-comparison no-op.
  useFrame(({ clock }) => {
    if (!animate) return
    if (clockStart.current === null) clockStart.current = clock.elapsedTime
    matTime.current = clock.elapsedTime - clockStart.current
    glowMaterial.uniforms.uTime.value = matTime.current
    if (matTime.current <= growthEndsAt.current) {
      writeInstances(matTime.current)
    }
  })

  const handleClick = (e: ThreePointerEvent) => {
    e.stopPropagation()
    // Walk intersections: first visible instance wins, skipping dimmed occluders
    const candidates = e.intersections ?? [e]
    for (const hit of candidates) {
      const id = hit.instanceId
      if (typeof id !== "number" || id < 0 || id >= entities.length) continue
      const vis = visibility?.[id] ?? 1
      if (vis < 0.15) continue
      onSelect?.(entities[id].id)
      return
    }
  }

  const handlePointerMove = (e: ThreePointerEvent) => {
    e.stopPropagation()
    const id = e.instanceId
    if (typeof id === "number" && id >= 0 && id < entities.length) {
      onHover?.(id, e.nativeEvent?.clientX, e.nativeEvent?.clientY)
    }
  }

  const handlePointerOut = (e: ThreePointerEvent) => {
    e.stopPropagation()
    onHover?.(null)
  }

  // Affordance: the spheres are click targets.
  useEffect(() => {
    if (hoveredIndex === null || hoveredIndex === undefined) return
    document.body.style.cursor = "pointer"
    return () => {
      document.body.style.cursor = ""
    }
  }, [hoveredIndex])

  return (
    <group>
      <instancedMesh
        ref={meshRef}
        args={[geometry, material, entities.length]}
        onClick={handleClick}
        onPointerMove={handlePointerMove}
        onPointerOut={handlePointerOut}
        frustumCulled={false}
      />
      {glow && <points ref={glowRef} args={[glowGeometry, glowMaterial]} frustumCulled={false} />}
    </group>
  )
}

// Minimal pointer-event shape — R3F's full ThreeEvent type isn't worth
// pulling into this file just for three methods.
interface ThreePointerEvent {
  stopPropagation: () => void
  instanceId?: number
  nativeEvent?: { clientX: number; clientY: number }
  /** All sorted intersections from the raycast (nearest first). */
  intersections?: Array<{ instanceId?: number }>
}
