// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// 3D community-collapse LOD — the R3F sibling of the 2D map's
// useSuperNodeLayer (map/community-supernodes.ts).
//
//   CollapseLOD  — must be a child of <Canvas>. Samples camera distance
//                  every frame but only calls back into React state when
//                  the hysteresis-driven collapsed level actually changes
//                  (mirrors the bucketed sampler in hub-labels.tsx — no
//                  per-frame reconciliation). Fires once unconditionally on
//                  its first frame so a fresh Canvas mount (view-mode
//                  toggle back into 3D) resyncs the parent's collapsedLevel
//                  even if it happens to already equal the freshly-mounted
//                  local ref's initial value.
//
//   SuperNodes3D — renders the resulting super-node set as one instanced
//                  sphere layer, sized by member count and colored by
//                  community, with billboarded member-count labels.

import { useEffect, useMemo, useRef, useState } from "react"
import { useFrame } from "@react-three/fiber"
import { Billboard, Text } from "@react-three/drei"
import { Color, InstancedMesh, MeshStandardMaterial, Object3D, SphereGeometry } from "three"
import { collapsedLevelForDistance, type SuperNode3D } from "./supernodes-3d"
import { communityRgb } from "./palette"
import { LABEL_HEX, SURFACE_HEX } from "@/theme/shader-tokens"

export interface CollapseLODProps {
  /** Deepest Leiden level available (ancestorIx.maxLevel); -1 = hierarchy not loaded yet, never collapses. */
  maxLevel: number
  onLevelChange: (level: number | null) => void
}

export function CollapseLOD({ maxLevel, onLevelChange }: CollapseLODProps) {
  const levelRef = useRef<number | null>(null)
  const syncedRef = useRef(false)
  useFrame(({ camera }) => {
    const distance = camera.position.length()
    const next = collapsedLevelForDistance(distance, levelRef.current, maxLevel)
    if (!syncedRef.current || next !== levelRef.current) {
      syncedRef.current = true
      levelRef.current = next
      onLevelChange(next)
    }
  })
  return null
}

export interface SuperNodes3DProps {
  supers: SuperNode3D[]
  /** Fires with the clicked super-node's community id (B4.4 drill-down). */
  onSelect?: (communityId: string) => void
}

export function SuperNodes3D({ supers, onSelect }: SuperNodes3DProps) {
  const meshRef = useRef<InstancedMesh>(null)
  const geometry = useMemo(() => new SphereGeometry(1, 16, 16), [])
  const material = useMemo(
    () => new MeshStandardMaterial({ roughness: 0.55, metalness: 0.15, emissiveIntensity: 0.3 }),
    [],
  )
  const dummy = useMemo(() => new Object3D(), [])
  const tmpColor = useMemo(() => new Color(), [])

  useEffect(() => {
    const mesh = meshRef.current
    if (!mesh) return
    for (let i = 0; i < supers.length; i++) {
      const s = supers[i]
      dummy.position.set(s.x, s.y, s.z)
      dummy.scale.setScalar(s.radius)
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)
      const [r, g, b] = communityRgb(s.id)
      tmpColor.setRGB(r, g, b)
      mesh.setColorAt(i, tmpColor)
    }
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    mesh.computeBoundingSphere()
  }, [supers, dummy, tmpColor])

  useEffect(() => {
    return () => {
      geometry.dispose()
      material.dispose()
    }
  }, [geometry, material])

  // Mirrors InstancedNodes' click-target affordance: cursor flips to pointer
  // while hovering a super-node so the drill-down click target is discoverable.
  // Routed through state + an effect (not set directly in the pointer
  // handlers) so the cleanup fires on unmount too — a click here unmounts
  // this component immediately (the parent switches to the member view),
  // which would otherwise leave the cursor stuck on "pointer" since the
  // trailing pointerout never arrives.
  const [hovering, setHovering] = useState(false)
  useEffect(() => {
    if (!hovering) return
    document.body.style.cursor = "pointer"
    return () => {
      document.body.style.cursor = ""
    }
  }, [hovering])

  const handlePointerOver = (e: ThreePointerEvent) => {
    e.stopPropagation()
    setHovering(true)
  }
  const handlePointerOut = () => {
    setHovering(false)
  }
  const handleClick = (e: ThreePointerEvent) => {
    e.stopPropagation()
    const id = e.instanceId
    if (typeof id !== "number" || id < 0 || id >= supers.length) return
    onSelect?.(supers[id].id)
  }

  if (supers.length === 0) return null

  return (
    <group>
      <instancedMesh
        ref={meshRef}
        args={[geometry, material, supers.length]}
        onClick={handleClick}
        onPointerOver={handlePointerOver}
        onPointerOut={handlePointerOut}
      />
      {supers.map((s) => (
        <Billboard key={s.id} position={[s.x, s.y + s.radius + 0.3, s.z]}>
          <Text
            fontSize={0.42}
            color={LABEL_HEX.default}
            outlineWidth={0.014}
            outlineColor={SURFACE_HEX.vaultDeep}
            anchorX="center"
            anchorY="bottom"
            fillOpacity={0.92}
          >
            {s.count.toLocaleString()}
          </Text>
        </Billboard>
      ))}
    </group>
  )
}

// Minimal pointer-event shape — mirrors instanced-nodes.tsx's own local type
// rather than pulling R3F's full ThreeEvent into this file for one method.
interface ThreePointerEvent {
  stopPropagation: () => void
  instanceId?: number
}
