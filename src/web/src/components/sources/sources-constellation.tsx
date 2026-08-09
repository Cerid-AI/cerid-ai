// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Sources Constellation — tier-1 overview.
 *
 * Central anchor (the Cerid mark) with source nodes orbiting at radius
 * proportional to recency. Tier-2 (artifact shells per source) and
 * tier-3 (inter-entity edges + particle stream) wire alongside the
 * SSE activity stream when needed.
 *
 * Reuses the vendor-r3f chunk already loaded for Subjects →
 * Constellation, so no new bundle cost.
 */

import { Suspense, useMemo, useRef } from "react"
import { Canvas, useFrame, type ThreeElements } from "@react-three/fiber"
import { OrbitControls } from "@react-three/drei"
import { Loader2 } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import * as THREE from "three"
import { listSources, type SourceRecord } from "@/lib/api/sources"
import { SOURCE_FAMILY_HEX } from "@/theme/shader-tokens"

// Source family → color (gold→teal palette, oklch).
const FAMILY_COLORS: Record<string, string> = SOURCE_FAMILY_HEX

const ANCHOR_COLOR = SOURCE_FAMILY_HEX.anchor
const RING_RADIUS = 3.5

interface SourcesConstellationProps {
  onSourceClick?: (sourceId: string) => void
}

export function SourcesConstellation({ onSourceClick }: SourcesConstellationProps) {
  const { data: sources, isLoading } = useQuery<SourceRecord[]>({
    queryKey: ["sources"],
    queryFn: () => listSources(),
    staleTime: 60_000,
  })

  if (isLoading || !sources) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Composing constellation…
      </div>
    )
  }

  if (sources.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
        <span>No sources connected yet.</span>
        <span className="text-label-xs">Press ⌘⇧S to add your first source.</span>
      </div>
    )
  }

  return (
    <div className="h-full w-full">
      <Canvas
        camera={{ position: [0, 1, 9], fov: 50 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
      >
        <Suspense fallback={null}>
          <ambientLight intensity={0.4} />
          <pointLight position={[0, 4, 4]} intensity={1.2} color={ANCHOR_COLOR} />

          <Anchor />
          <SourceOrbit sources={sources} onSourceClick={onSourceClick} />

          <OrbitControls
            enablePan={false}
            enableZoom={true}
            minDistance={5}
            maxDistance={20}
            autoRotate
            autoRotateSpeed={0.4}
          />
        </Suspense>
      </Canvas>
    </div>
  )
}

function Anchor() {
  const ref = useRef<THREE.Mesh>(null!)
  useFrame((state) => {
    if (ref.current) {
      // Subtle pulse: scale 1.0 → 1.05 over a 3s sine
      const t = state.clock.elapsedTime
      const s = 1 + Math.sin(t * 2) * 0.025
      ref.current.scale.set(s, s, s)
    }
  })
  const props: ThreeElements["mesh"] = { ref }
  return (
    <mesh {...props}>
      <sphereGeometry args={[0.55, 32, 32]} />
      <meshStandardMaterial
        color={ANCHOR_COLOR}
        emissive={ANCHOR_COLOR}
        emissiveIntensity={0.5}
        roughness={0.3}
      />
    </mesh>
  )
}

function SourceOrbit({
  sources,
  onSourceClick,
}: {
  sources: SourceRecord[]
  onSourceClick?: (id: string) => void
}) {
  const positions = useMemo(() => {
    // Sort by created_at desc so recently-added sources sit at top.
    const sorted = [...sources].sort((a, b) =>
      (b.created_at ?? "").localeCompare(a.created_at ?? ""),
    )
    const n = sorted.length
    return sorted.map((src, i) => {
      const angle = (i / n) * Math.PI * 2
      // Recency-driven radial offset: newer sources slightly closer.
      const recencyShrink = 1 - Math.min(0.25, i / Math.max(n * 2, 1))
      const r = RING_RADIUS * recencyShrink
      return {
        src,
        x: Math.cos(angle) * r,
        y: Math.sin(angle * 0.4) * 0.6,
        z: Math.sin(angle) * r,
      }
    })
  }, [sources])

  return (
    <>
      {positions.map(({ src, x, y, z }) => (
        <SourceNode
          key={src.id}
          source={src}
          position={[x, y, z]}
          onClick={() => onSourceClick?.(src.id)}
        />
      ))}
    </>
  )
}

function SourceNode({
  source,
  position,
  onClick,
}: {
  source: SourceRecord
  position: [number, number, number]
  onClick?: () => void
}) {
  const color = FAMILY_COLORS[source.family] ?? SOURCE_FAMILY_HEX.adapter
  // Size by total_artifacts (log scale so a 10x corpus doesn't crush
  // smaller sources).
  const size = useMemo(() => {
    const v = source.total_artifacts ?? 0
    return 0.18 + Math.log10(v + 1) * 0.04
  }, [source.total_artifacts])

  return (
    <mesh position={position} onClick={onClick}>
      <sphereGeometry args={[size, 16, 16]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={0.25}
        roughness={0.5}
      />
    </mesh>
  )
}
