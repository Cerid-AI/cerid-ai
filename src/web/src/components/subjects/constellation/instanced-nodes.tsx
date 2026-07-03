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

import { useEffect, useLayoutEffect, useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  InstancedBufferAttribute,
  InstancedMesh,
  MeshStandardMaterial,
  Object3D,
  type Points as ThreePoints,
  ShaderMaterial,
  SphereGeometry,
} from "three"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"
import { planeIntersect, type Vec3 } from "./drag-plane"
import { FLOAT3_GLSL } from "./float3"
import { communityRgb, degreeRadius, hash01 } from "./palette"

const GROW_DURATION_S = 0.9

// Drag-vector helpers, kept local (mirrors drag-plane.ts's own internal
// dot/sub) — not worth a shared vec-util module for three one-line ops.
function toVec3(v: { x: number; y: number; z: number }): Vec3 {
  return [v.x, v.y, v.z]
}
function vSub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
function vNormalize(a: Vec3): Vec3 {
  const len = Math.hypot(a[0], a[1], a[2]) || 1
  return [a[0] / len, a[1] / len, a[2] / len]
}

/**
 * Opacity multiplier applied to a non-neighbor node (sphere + glow) when
 * another node is hovered/selected. Decisive fade so the local subgraph pops.
 * Exported for unit tests.
 */
export const NON_NEIGHBOR_NODE_DIM = 0.12

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
  ${FLOAT3_GLSL}

  attribute vec3 aColor;
  attribute float aSize;
  attribute float aSeed;
  attribute float aBirth;
  attribute float aDim;

  uniform float uTime;
  uniform float uBreatheAmp;
  uniform float uFloatAmp;

  varying vec3 vColor;
  varying float vAlive;
  varying float vDim;

  void main() {
    vColor = aColor;
    vDim = aDim;
    float grow = clamp((uTime - aBirth) / ${GROW_DURATION_S.toFixed(1)}, 0.0, 1.0);
    vAlive = grow;
    float breathe = 1.0 + uBreatheAmp * 0.07 * sin(uTime * 1.3 + aSeed * 6.2831);
    vec3 fpos = position + float3(aSeed, uTime, uFloatAmp);
    vec4 mv = modelViewMatrix * vec4(fpos, 1.0);
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
  /** Quality toggle: organic per-node float around the fixed UMAP seed. */
  float?: boolean
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
  /** Fires when a press-drag crosses the click-vs-drag threshold, and again on drop. Caller suspends OrbitControls while true. */
  onDragStateChange?: (dragging: boolean) => void
  /** Fires on every drag-move with the dragged entity's new world position (transient — not persisted). */
  onNodeMoved?: (entityId: string, pos: Vec3) => void
}

export function InstancedNodes({
  entities,
  onSelect,
  animate = true,
  glow = false,
  pulses = false,
  float = false,
  hoveredIndex = null,
  neighbors,
  degrees,
  colors: lensColors,
  visibility,
  onHover,
  onDragStateChange,
  onNodeMoved,
}: InstancedNodesProps) {
  const meshRef = useRef<InstancedMesh>(null)
  const glowRef = useRef<ThreePoints>(null)

  // Drag state. dragId/dragMoved/downXY track a potential drag from
  // pointerdown; dragOverrides holds transient per-entity position
  // overrides (id-keyed — survives a refetch reordering entities, unlike
  // index, and is reset on unmount) applied on top of the server-seeded
  // entities[i].x/y/z. Named to avoid colliding with Constellation.tsx's
  // unrelated `pinned` hover-lock state.
  const dragId = useRef<number | null>(null)
  const dragMoved = useRef(false)
  const downXY = useRef<[number, number]>([0, 0])
  const dragOverrides = useRef<Map<string, Vec3>>(new Map())
  // A completed drag's trailing pointerup is followed by a native click on
  // the same target; this flag skips that one click so a drag never also
  // fires a select.
  const suppressClick = useRef(false)

  // Birth times per entity id — survives refetches so existing nodes
  // never re-animate; cleared only on unmount.
  const births = useRef<Map<string, number>>(new Map())
  const matTime = useRef(0)
  const clockStart = useRef<number | null>(null)
  const growthEndsAt = useRef(0)

  // Stable geometry + material for the sphere layer.
  const geometry = useMemo(() => new SphereGeometry(1, 12, 12), [])
  // Shared uniform objects so the per-frame tick (useFrame) reaches the
  // shader after it's compiled by onBeforeCompile below.
  const sphereFloatUniforms = useMemo(() => ({ uTime: { value: 0 }, uFloatAmp: { value: 0 } }), [])
  const material = useMemo(() => {
    const mat = new MeshStandardMaterial({
      vertexColors: false,
      roughness: 0.5,
      metalness: 0.2,
      emissive: new Color(0.18, 0.22, 0.28),
      emissiveIntensity: 0.4,
    })
    // Inject the organic float AFTER the instance matrix (world space) so
    // spheres float in lock-step with the glow halos (GLOW_VERTEX) and
    // edge endpoints (neural-links.tsx), which both displace world-space
    // position — not into object-space `transformed`, which would get
    // multiplied by the per-instance scale (node radius) and desync.
    //
    // This anchors ONLY to `#include <project_vertex>` — three's stable
    // public shader-chunk marker — and owns the full expansion itself
    // (rather than patching `ShaderChunk.project_vertex`'s private text),
    // so a future three.js reformat of that internal chunk can't silently
    // no-op this replace and revert the sphere to object-space (non-floating)
    // desync with nothing to catch it. Mesh is a plain InstancedMesh with no
    // batching/morphing, so reproducing just the instancing branch is complete.
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.uTime = sphereFloatUniforms.uTime
      shader.uniforms.uFloatAmp = sphereFloatUniforms.uFloatAmp
      shader.vertexShader =
        "attribute float instanceSeed;\nuniform float uTime;\nuniform float uFloatAmp;\n" +
        FLOAT3_GLSL + "\n" +
        shader.vertexShader.replace(
          "#include <project_vertex>",
          [
            "vec4 mvPosition = vec4( transformed, 1.0 );",
            "#ifdef USE_INSTANCING",
            "  mvPosition = instanceMatrix * mvPosition;",
            "#endif",
            "mvPosition.xyz += float3( instanceSeed, uTime, uFloatAmp );",
            "mvPosition = modelViewMatrix * mvPosition;",
            "gl_Position = projectionMatrix * mvPosition;",
          ].join("\n"),
        )
    }
    return mat
    // sphereFloatUniforms is a stable ref-identity object (created once above);
    // omitted from deps since onBeforeCompile only needs to close over it once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      // A refetch rebuilds this geometry from the server-seeded entities
      // array; a still-pinned (dragged) node keeps its dropped position
      // instead of snapping back to the server seed mid-session. Keyed by
      // entity id (not index) since /graph/embeddings/3d has no ORDER BY
      // and a refetch can reorder entities between requests.
      const pin = dragOverrides.current.get(ent.id)
      positions[i * 3 + 0] = pin ? pin[0] : ent.x
      positions[i * 3 + 1] = pin ? pin[1] : ent.y
      positions[i * 3 + 2] = pin ? pin[2] : ent.z
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
    // Required for frustumCulled=true: without a bounding sphere the
    // renderer cannot determine visibility and culls the draw call by default.
    geom.computeBoundingSphere()
    return geom
  }, [entities, degrees, lensColors])

  // Per-instance seed for the sphere float displacement — same hash01(id)
  // as the glow layer's aSeed, so sphere + halo float in lock-step.
  const instanceSeeds = useMemo(() => {
    const arr = new Float32Array(entities.length)
    for (let i = 0; i < entities.length; i++) arr[i] = hash01(entities[i].id)
    return arr
  }, [entities])

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
        v = i === hoveredIndex ? 1.8 : hood?.has(i) ? Math.min(v * 1.2, 1.2) : Math.min(v, NON_NEIGHBOR_NODE_DIM)
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
        uFloatAmp: { value: 0 },
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
  const writeInstances = (t: number, recomputeBounds = false) => {
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
        else if (!hood?.has(i)) brightness = Math.min(brightness, NON_NEIGHBOR_NODE_DIM)
      }
      // A dragged-and-dropped node keeps its transient pinned position
      // through any subsequent whole-scene rewrite (hover/visibility/lens
      // changes all call writeInstances) instead of snapping back. Keyed
      // by entity id — see glowGeometry above for why.
      const pin = dragOverrides.current.get(ent.id)
      dummy.position.set(pin ? pin[0] : ent.x, pin ? pin[1] : ent.y, pin ? pin[2] : ent.z)
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
    // Recompute the instanced bounding sphere so frustum culling is correct.
    // Called on entity-list changes and after growth completes (not every frame,
    // since positions are fixed; scale changes during growth don't affect
    // culling correctness because the sphere is conservatively large).
    if (recomputeBounds) {
      mesh.computeBoundingSphere()
    }
  }

  // Initial population, on entity-list change, and on hover-focus change.
  // recomputeBounds=true so the instanced bounding sphere is fresh after each
  // entity-list change; hover/visibility changes don't shift positions so
  // bounds don't need recomputing on those paths.
  useEffect(() => {
    writeInstances(matTime.current, true)
    // writeInstances reads only refs + the props captured here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entities, hoveredIndex, neighbors, degrees, lensColors, visibility])

  // instanceSeed only depends on the entity list — set once per entity-list
  // change, not on every hover/visibility re-render. useLayoutEffect (not
  // useEffect) so the attribute is bound synchronously before the first
  // WebGL read — otherwise the first frame(s) read 0 for every instance,
  // giving all spheres the same float3(0, uTime, uFloatAmp) offset while
  // the glow/edge layers already show per-node offsets.
  useLayoutEffect(() => {
    const mesh = meshRef.current
    if (!mesh) return
    mesh.geometry.setAttribute("instanceSeed", new InstancedBufferAttribute(instanceSeeds, 1))
  }, [instanceSeeds])

  // Per-frame: tick the glow clock (uniform only) and, while a growth
  // window is active, re-write sphere matrices. Outside the window the
  // scene is static and this is a two-comparison no-op.
  // Recompute the instanced bounding sphere once at the end of the growth
  // window so culling accounts for the final node positions/scales.
  const growthBoundsComputedAt = useRef(-1)
  useFrame(({ clock }) => {
    if (!animate) return
    if (clockStart.current === null) clockStart.current = clock.elapsedTime
    matTime.current = clock.elapsedTime - clockStart.current
    glowMaterial.uniforms.uTime.value = matTime.current
    const floatOn = animate && float === true
    glowMaterial.uniforms.uFloatAmp.value = floatOn ? 1 : 0
    sphereFloatUniforms.uTime.value = matTime.current
    sphereFloatUniforms.uFloatAmp.value = floatOn ? 1 : 0
    if (matTime.current <= growthEndsAt.current) {
      writeInstances(matTime.current)
    } else if (growthBoundsComputedAt.current < growthEndsAt.current) {
      // Growth just finished — do one final bounds recompute so culling is
      // accurate at the settled node scales.
      writeInstances(matTime.current, true)
      growthBoundsComputedAt.current = growthEndsAt.current
    }
  })

  const handleClick = (e: ThreePointerEvent) => {
    e.stopPropagation()
    if (suppressClick.current) {
      // Tail end of a completed drag — the drop already placed the node;
      // don't also treat the trailing click as a select.
      suppressClick.current = false
      return
    }
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

  // Imperative single-instance reposition during an active drag: updates
  // the sphere matrix + glow attribute for node `i` only, then reports the
  // new position up so the parent can patch just that node's incident
  // edges in NeuralLinks.
  const repositionNode = (i: number, pos: Vec3) => {
    const mesh = meshRef.current
    if (mesh) {
      const r = degreeRadius(degrees?.[i] ?? 0)
      dummy.position.set(pos[0], pos[1], pos[2])
      dummy.scale.set(r, r, r)
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)
      mesh.instanceMatrix.needsUpdate = true
    }
    const posAttr = glowGeometry.getAttribute("position") as BufferAttribute | undefined
    if (posAttr) {
      posAttr.setXYZ(i, pos[0], pos[1], pos[2])
      posAttr.needsUpdate = true
    }
    onNodeMoved?.(entities[i].id, pos)
  }

  const handlePointerDown = (e: ThreePointerEvent) => {
    const id = e.instanceId
    if (typeof id !== "number" || id < 0 || id >= entities.length) return
    e.stopPropagation()
    dragId.current = id
    dragMoved.current = false
    downXY.current = [e.nativeEvent?.clientX ?? 0, e.nativeEvent?.clientY ?? 0]
    // Capture the pointer to this mesh so subsequent pointermove/pointerup
    // keep delivering here via R3F's capture map instead of re-raycasting —
    // without this, once the dragged node moves off its instance footprint
    // the drag silently stops tracking the cursor.
    if (typeof e.pointerId === "number") {
      e.target?.setPointerCapture?.(e.pointerId)
    }
  }

  const handlePointerMove = (e: ThreePointerEvent) => {
    e.stopPropagation()
    const draggingId = dragId.current
    if (draggingId !== null) {
      const dx = (e.nativeEvent?.clientX ?? 0) - downXY.current[0]
      const dy = (e.nativeEvent?.clientY ?? 0) - downXY.current[1]
      if (!dragMoved.current && Math.hypot(dx, dy) > 4) {
        dragMoved.current = true
        onDragStateChange?.(true) // suspend OrbitControls
      }
      if (dragMoved.current && e.ray && e.camera) {
        const node = entities[draggingId]
        const nodePos: Vec3 = dragOverrides.current.get(node.id) ?? [node.x, node.y, node.z]
        // Plane through the node, facing the camera.
        const normal = vNormalize(vSub(toVec3(e.camera.position), nodePos))
        const hit = planeIntersect(toVec3(e.ray.origin), toVec3(e.ray.direction), nodePos, normal)
        if (hit) {
          dragOverrides.current.set(node.id, hit)
          repositionNode(draggingId, hit)
        }
      }
      return // don't treat a drag as a hover
    }
    const id = e.instanceId
    if (typeof id === "number" && id >= 0 && id < entities.length) {
      onHover?.(id, e.nativeEvent?.clientX, e.nativeEvent?.clientY)
    }
  }

  const handlePointerUp = (e: ThreePointerEvent) => {
    e.stopPropagation()
    if (dragId.current !== null) {
      const wasDrag = dragMoved.current
      dragId.current = null
      dragMoved.current = false
      if (typeof e.pointerId === "number") {
        e.target?.releasePointerCapture?.(e.pointerId)
      }
      if (wasDrag) {
        suppressClick.current = true
        onDragStateChange?.(false) // re-enable OrbitControls
        // Node stays where dropped — dragOverrides.current keeps the position.
        return
      }
    }
    // Not a drag → fall through to the native click that follows, which
    // runs handleClick's normal select path.
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
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerOut={handlePointerOut}
      />
      {glow && <points ref={glowRef} args={[glowGeometry, glowMaterial]} />}
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
  /** The picking ray in world space (three.js Ray — Vector3 fields, not tuples). */
  ray?: { origin: { x: number; y: number; z: number }; direction: { x: number; y: number; z: number } }
  /** The active camera (three.js Camera — Vector3 position, not a tuple). */
  camera?: { position: { x: number; y: number; z: number } }
  /** The native PointerEvent's pointerId, needed for setPointerCapture/releasePointerCapture. */
  pointerId?: number
  /** R3F's event target — exposes the capture methods documented for the pointer-capture drag pattern. */
  target?: {
    setPointerCapture?: (id: number) => void
    releasePointerCapture?: (id: number) => void
  }
}
