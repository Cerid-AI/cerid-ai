// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Renderer-agnostic drag+heal controller (Cycle 4 Amendment A1).
//
// createHealController wires up drag-follow with neighbor falloff tug and
// a critically-damped lerp-home on release. Deliberately no d3-force, no
// client FA2 — home is the authoritative server position.
//
// Implements the HealController / CreateHealController contract from
// lib/graph/cycle4-contracts.ts. The sigma 2D adapter mounts this in
// CartographerMap.tsx (only v1 consumer); R3F 3D is v1.1.

import type { HealControllerOptions, HealController } from "@/lib/graph/cycle4-contracts"

const LERP_ALPHA = 0.12 as const
const SPRING_OVERSHOOT = 0.08 as const
const FALLOFF_SCALE = 1.5 as const
const SETTLE_THRESHOLD = 0.002 as const

// ---------------------------------------------------------------------------
// Math helpers — exported for unit tests
// ---------------------------------------------------------------------------

/**
 * Neighbor displacement falloff. dist=1 (direct neighbor) → maximal tug;
 * dist=2+ → decays exponentially. Clamped to [0, 1].
 */
export function neighborFalloff(dist: number): number {
  if (dist <= 0) return 1
  return Math.max(0, Math.min(1, Math.exp(-dist * FALLOFF_SCALE) * 0.6))
}

/**
 * Pure lerp-home spring step. Critically-damped: each frame moves
 * LERP_ALPHA of the remaining distance toward home plus a carry-over
 * velocity term that damps via SPRING_OVERSHOOT. The position always
 * moves strictly toward home each step (no overshoot) because the
 * velocity damp keeps the combined step ≤ the remaining distance.
 *
 * Returns next position and velocity. Used in unit tests to verify
 * convergence without running rAF.
 */
export function lerpHomeStep(
  cur: number,
  home: number,
  vel: number,
): { pos: number; vel: number } {
  const dist = home - cur
  // Velocity damps independently; lerp provides the proportional pull.
  // The step is clipped so it never overshoots home.
  const raw = dist * LERP_ALPHA + vel * (1 - SPRING_OVERSHOOT)
  // Clip to the remaining gap: ensures strict monotone convergence.
  const step = Math.abs(raw) > Math.abs(dist) ? dist : raw
  return { pos: cur + step, vel: step }
}

/**
 * Simulate N settle steps from startPos toward home.
 * Returns position after N frames — used in unit tests.
 * Converges to home within ~60 frames at LERP_ALPHA=0.12.
 */
export function simulateSettle(
  startPos: number,
  home: number,
  frames: number,
): number {
  let pos = startPos
  let vel = 0
  for (let i = 0; i < frames; i++) {
    const r = lerpHomeStep(pos, home, vel)
    pos = r.pos
    vel = r.vel
  }
  return pos
}

// ---------------------------------------------------------------------------
// createHealController — implements the contract's CreateHealController type
// ---------------------------------------------------------------------------

interface DragState {
  nodeId: string
  homeX: number
  homeY: number
}

/**
 * Creates a renderer-agnostic drag+heal controller implementing the
 * HealController contract from cycle4-contracts.ts.
 *
 * Call startDrag() on pointerdown, moveDrag() on pointermove,
 * endDrag() on pointerup (pass pin:true for Shift-drop).
 * Call cancel() to discard any in-flight heal.
 */
export function createHealController(
  opts: HealControllerOptions<{ x: number; y: number }>,
): HealController & { dispose: () => void } {
  const { getHome, getPos, setPos, neighbors, onSettle, reducedMotion } = opts

  const rafHandles = new Map<string, number>()
  let activeDrag: DragState | null = null

  function _cancelHeal(id: string): void {
    const h = rafHandles.get(id)
    if (h !== undefined) {
      cancelAnimationFrame(h)
      rafHandles.delete(id)
    }
  }

  function _cancelAllHeals(): void {
    for (const [, h] of rafHandles) cancelAnimationFrame(h)
    rafHandles.clear()
  }

  function _getNeighborHomes(nodeId: string): Map<string, { x: number; y: number }> {
    const map = new Map<string, { x: number; y: number }>()
    for (const nbId of neighbors(nodeId)) {
      const h = getHome(nbId)
      if (h) map.set(nbId, h)
    }
    return map
  }

  function startDrag(nodeId: string): void {
    const home = getHome(nodeId)
    if (!home) return
    // Cancel any in-flight heal for this node
    _cancelHeal(nodeId)
    for (const nbId of neighbors(nodeId)) _cancelHeal(nbId)
    activeDrag = { nodeId, homeX: home.x, homeY: home.y }
  }

  function moveDrag(nodeId: string, pos: { x: number; y: number }): void {
    if (!activeDrag || activeDrag.nodeId !== nodeId) return
    setPos(nodeId, pos)

    // Neighbor tug: pull 1-hop neighbors toward the dragged node by a
    // falloff fraction of the drag displacement from home.
    const dx = pos.x - activeDrag.homeX
    const dy = pos.y - activeDrag.homeY

    for (const nbId of neighbors(nodeId)) {
      const nbHome = getHome(nbId)
      if (!nbHome) continue
      const falloff = neighborFalloff(1) // all direct neighbors are dist=1
      setPos(nbId, { x: nbHome.x + dx * falloff, y: nbHome.y + dy * falloff })
    }
  }

  function endDrag(nodeId: string, endOpts?: { pin?: boolean }): void {
    if (!activeDrag || activeDrag.nodeId !== nodeId) return

    const { homeX, homeY } = activeDrag
    activeDrag = null

    if (endOpts?.pin) {
      return
    }

    const nbHomes = _getNeighborHomes(nodeId)
    const settlingIds = new Set<string>([nodeId, ...nbHomes.keys()])

    if (reducedMotion) {
      setPos(nodeId, { x: homeX, y: homeY })
      for (const [nbId, nbHome] of nbHomes) {
        setPos(nbId, nbHome)
      }
      onSettle()
      return
    }

    const settled = new Set<string>()

    function markSettled(id: string) {
      settled.add(id)
      if (settled.size === settlingIds.size) onSettle()
    }

    function healOne(id: string, hx: number, hy: number) {
      _cancelHeal(id)
      let velX = 0
      let velY = 0

      function step() {
        const curr = getPos(id)
        if (!curr) {
          rafHandles.delete(id)
          markSettled(id)
          return
        }

        const distX = hx - curr.x
        const distY = hy - curr.y

        if (Math.abs(distX) < SETTLE_THRESHOLD && Math.abs(distY) < SETTLE_THRESHOLD) {
          setPos(id, { x: hx, y: hy })
          rafHandles.delete(id)
          markSettled(id)
          return
        }

        const rawX = distX * LERP_ALPHA + velX * (1 - SPRING_OVERSHOOT)
        const rawY = distY * LERP_ALPHA + velY * (1 - SPRING_OVERSHOOT)
        // Clip to remaining gap: strictly monotone convergence, no overshoot.
        velX = Math.abs(rawX) > Math.abs(distX) ? distX : rawX
        velY = Math.abs(rawY) > Math.abs(distY) ? distY : rawY

        setPos(id, { x: curr.x + velX, y: curr.y + velY })

        const handle = requestAnimationFrame(step)
        rafHandles.set(id, handle)
      }

      const handle = requestAnimationFrame(step)
      rafHandles.set(id, handle)
    }

    healOne(nodeId, homeX, homeY)
    for (const [nbId, nbHome] of nbHomes) {
      healOne(nbId, nbHome.x, nbHome.y)
    }
  }

  function cancel(): void {
    activeDrag = null
    _cancelAllHeals()
  }

  function dispose(): void {
    cancel()
  }

  return { startDrag, moveDrag, endDrag, cancel, dispose }
}
