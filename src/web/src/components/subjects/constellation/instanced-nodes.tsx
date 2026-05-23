// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// InstancedMesh node renderer — Phase B Day 5. Renders N entities with
// ONE draw call (vs N draw calls in the v1 per-entity mesh path).
// Required for 2K-node 60fps on AMD Mac (validated upstream).
//
// Architecture:
//   - One shared sphereGeometry (low poly: 12x12 segments — adequate at
//     screen-projected sizes 8-40px)
//   - One shared MeshStandardMaterial with vertexColors enabled
//   - Per-instance: matrix (position + scale) and color via
//     setColorAt() — both updated in a single useLayoutEffect after the
//     entity list arrives, then frozen until the list changes.
//
// What we skip in v1:
//   - Custom GLSL bloom shader (drei's <EffectComposer> + <Bloom> pass
//     gives equivalent post-process bloom at lower implementation cost
//     and works with InstancedMesh). Layered on top in a follow-up if
//     the rim-light shader is desired for the brand look.
//   - Picking via raycasting against per-instance IDs (sigma-style
//     pickingBuffer not available in R3F by default). Day 5 ships
//     visual; click handling stays on per-instance level via the
//     onClick prop sigma's instanceId stamps on events.

import { useEffect, useMemo, useRef } from "react"
import { Color, InstancedMesh, MeshStandardMaterial, Object3D, SphereGeometry } from "three"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"

// ---------------------------------------------------------------------------
// Visual encoding — must mirror the per-entity helpers in Constellation.tsx
// ---------------------------------------------------------------------------

const COMMUNITY_PALETTE_RGB = [
  [0.898, 0.518, 0.478], [0.898, 0.659, 0.478], [0.898, 0.784, 0.478], [0.831, 0.686, 0.216],
  [0.784, 0.898, 0.478], [0.659, 0.898, 0.478], [0.478, 0.898, 0.784], [0.478, 0.784, 0.898],
  [0.478, 0.659, 0.898], [0.659, 0.478, 0.898], [0.784, 0.478, 0.898], [0.898, 0.478, 0.784],
] as const

const GRAPHITE: [number, number, number] = [0.36, 0.40, 0.50]

function communityRgb(communityId: string | null): [number, number, number] {
  if (!communityId) return GRAPHITE
  let h = 0
  for (let i = 0; i < communityId.length; i++) {
    h = ((h << 5) - h) + communityId.charCodeAt(i)
    h |= 0
  }
  const idx = Math.abs(h) % COMMUNITY_PALETTE_RGB.length
  const [r, g, b] = COMMUNITY_PALETTE_RGB[idx]
  return [r, g, b]
}

function nodeRadius(mentionCount: number): number {
  return 0.4 + Math.log1p(Math.max(0, mentionCount)) * 0.15
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface InstancedNodesProps {
  entities: EntityEmbedding3D[]
  onSelect?: (entityId: string) => void
}

export function InstancedNodes({ entities, onSelect }: InstancedNodesProps) {
  const meshRef = useRef<InstancedMesh>(null)

  // Stable geometry + material — recreated only if entities array identity
  // changes. Geometry is shared across all instances; material is shared.
  const geometry = useMemo(() => new SphereGeometry(1, 12, 12), [])
  const material = useMemo(() => {
    return new MeshStandardMaterial({
      vertexColors: false,
      roughness: 0.5,
      metalness: 0.2,
      emissive: new Color(0.0, 0.0, 0.0),
      emissiveIntensity: 0.4,
    })
  }, [])

  // Cleanup three.js objects on unmount — prevents GPU leaks on re-mount.
  useEffect(() => {
    return () => {
      geometry.dispose()
      material.dispose()
    }
  }, [geometry, material])

  // Reusable scratch — avoid GC churn during per-instance setup.
  const dummy = useMemo(() => new Object3D(), [])
  const tmpColor = useMemo(() => new Color(), [])

  // Populate per-instance matrices + colors. Runs once on entity-list
  // change. After this, the mesh is essentially static until the list
  // changes — which is what gives us the perf win.
  useEffect(() => {
    const mesh = meshRef.current
    if (!mesh) return
    for (let i = 0; i < entities.length; i++) {
      const ent = entities[i]
      const radius = nodeRadius(ent.mention_count)
      dummy.position.set(ent.x, ent.y, ent.z)
      dummy.scale.set(radius, radius, radius)
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)

      const [r, g, b] = communityRgb(ent.community)
      tmpColor.setRGB(r, g, b)
      mesh.setColorAt(i, tmpColor)
    }
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  }, [entities, dummy, tmpColor])

  // Click → entity lookup via instanceId
  const handleClick = (e: ThreeClickEvent) => {
    e.stopPropagation()
    const id = e.instanceId
    if (typeof id === "number" && id >= 0 && id < entities.length) {
      onSelect?.(entities[id].id)
    }
  }

  return (
    <instancedMesh
      ref={meshRef}
      args={[geometry, material, entities.length]}
      onClick={handleClick}
      // Frustum culling — at distance, entities cluster tightly; culling
      // per-instance via bounding sphere is unreliable. Disable for
      // correctness; perf cost is negligible at our N.
      frustumCulled={false}
    />
  )
}

// Minimal click-event shape — R3F's full ThreeEvent type isn't worth
// pulling into this file just for one method.
interface ThreeClickEvent {
  stopPropagation: () => void
  instanceId?: number
}
