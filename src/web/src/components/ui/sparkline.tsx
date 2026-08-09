// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useMemo, type CSSProperties } from "react"
import { cn } from "@/lib/utils"

/**
 * Zero-dependency SVG sparkline. Used by the Sources pane F9
 * Knowledge Stats hero card (one per metric, 60×16 px) and by
 * per-source detail panes for `artifacts_24h` trends.
 *
 * Design from the 2026-05-24 Ingestion Experience plan §6.1 + F9:
 * - Cerid teal at 60% opacity for the line
 * - Gold endpoint dot at the most-recent value
 * - Tweens on update via the `cerid-sparkline-pulse` utility (just a
 *   CSS transition on the SVG path's `d` attribute — the browser
 *   interpolates linearly between path strings of equal command count)
 *
 * Honors `prefers-reduced-motion` — the right-edge tween is skipped
 * and the value snaps to the new point.
 */

interface SparklineProps {
  /** Data points, oldest → newest. Length determines x-axis density. */
  values: number[]
  /** Width in pixels. Default 60. */
  width?: number
  /** Height in pixels. Default 16. */
  height?: number
  /** Whether to render the gold endpoint dot. Default true. */
  endpointDot?: boolean
  /** Optional explicit y-range. When omitted, auto-fits to min..max of values. */
  yMin?: number
  yMax?: number
  /** Optional accessibility label — narrated by screen readers. */
  label?: string
  /** CSS class on the SVG root. */
  className?: string
  /** Inline style override (caller responsibility to not break sizing). */
  style?: CSSProperties
}

const DEFAULT_W = 60
const DEFAULT_H = 16
const PADDING = 1.5

export function Sparkline({
  values,
  width = DEFAULT_W,
  height = DEFAULT_H,
  endpointDot = true,
  yMin,
  yMax,
  label,
  className,
  style,
}: SparklineProps) {
  const path = useMemo(() => buildPath(values, width, height, yMin, yMax), [
    values,
    width,
    height,
    yMin,
    yMax,
  ])

  const last = values.length > 0 ? values[values.length - 1] : 0
  const endpoint = useMemo(() => buildEndpoint(values, width, height, yMin, yMax), [
    values,
    width,
    height,
    yMin,
    yMax,
  ])

  // Empty / single-point dataset — render a baseline so the layout doesn't jump.
  if (values.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className={cn("inline-block", className)}
        style={style}
        role={label ? "img" : "presentation"}
        aria-label={label}
      >
        <line
          x1={PADDING}
          y1={height / 2}
          x2={width - PADDING}
          y2={height / 2}
          stroke="oklch(0.82 0.16 178 / 0.30)"
          strokeWidth={1.25}
          strokeLinecap="round"
        />
      </svg>
    )
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("inline-block cerid-sparkline-pulse", className)}
      style={style}
      role={label ? "img" : "presentation"}
      aria-label={label ?? `Trend ending at ${last}`}
    >
      <path
        d={path}
        fill="none"
        stroke="oklch(0.82 0.16 178 / 0.60)"
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {endpointDot && endpoint && (
        <circle
          cx={endpoint.x}
          cy={endpoint.y}
          r={1.75}
          fill="oklch(0.78 0.12 85)"
        />
      )}
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Path builders — pure functions, easy to unit-test independently
// ---------------------------------------------------------------------------

function buildPath(
  values: number[],
  width: number,
  height: number,
  yMinOverride?: number,
  yMaxOverride?: number,
): string {
  if (values.length < 2) return ""
  const { yMin, yMax } = resolveYRange(values, yMinOverride, yMaxOverride)
  const xStep = (width - PADDING * 2) / (values.length - 1)
  const yRange = Math.max(yMax - yMin, 1) // guard against zero range
  const points = values.map((v, i) => {
    const x = PADDING + i * xStep
    const yNorm = (v - yMin) / yRange
    const y = height - PADDING - yNorm * (height - PADDING * 2)
    return [x, y] as const
  })
  // Use M for the first, L for the rest — keeps the path-string command
  // count stable across renders so the CSS `d`-attr transition can
  // interpolate cleanly.
  return points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(" ")
}

function buildEndpoint(
  values: number[],
  width: number,
  height: number,
  yMinOverride?: number,
  yMaxOverride?: number,
): { x: number; y: number } | null {
  if (values.length < 2) return null
  const { yMin, yMax } = resolveYRange(values, yMinOverride, yMaxOverride)
  const xStep = (width - PADDING * 2) / (values.length - 1)
  const yRange = Math.max(yMax - yMin, 1)
  const last = values[values.length - 1]
  const x = PADDING + (values.length - 1) * xStep
  const yNorm = (last - yMin) / yRange
  const y = height - PADDING - yNorm * (height - PADDING * 2)
  return { x, y }
}

function resolveYRange(
  values: number[],
  yMinOverride?: number,
  yMaxOverride?: number,
): { yMin: number; yMax: number } {
  const yMin = yMinOverride ?? Math.min(...values)
  const yMax = yMaxOverride ?? Math.max(...values)
  return { yMin, yMax }
}
