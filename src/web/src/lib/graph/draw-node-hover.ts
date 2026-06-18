// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Shared sigma hover renderer for the Meridian identity pipeline.
//
// sigma v3's default `drawDiscNodeHover` fills the label plate with
// hardcoded `#FFF`, producing blank white tags when the theme's label
// color is near-white (dark mode).  This module replaces that with a
// token-aware version that uses the card/background token for the plate
// and the foreground token for text — both pre-resolved by
// `resolveMapTokens` so no raw `oklch()` or `var()` ever reaches the
// canvas 2D API.
//
// Usage:
//   import { makeDrawNodeHover } from "@/lib/graph/draw-node-hover"
//   const sigma = new Sigma(graph, container, {
//     ...settings,
//     defaultDrawNodeHover: makeDrawNodeHover(tokens),
//   })
//
// Agent B contract: call `makeDrawNodeHover(tokens)` and pass the result
// to `defaultDrawNodeHover` in Atlas.tsx's Sigma constructor and whenever
// `currentTokens` is pushed via `sigma.setSetting(...)` on theme change.
// Function signature matches `NodeHoverDrawingFunction` from "sigma/rendering":
//   (context: CanvasRenderingContext2D, data: ..., settings: ...) => void
//   Return type: NodeHoverDrawingFunction (re-exported below for Agent B's use)

import type { NodeHoverDrawingFunction } from "sigma/rendering"
import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"

// Re-export so Agent B can type the return value without a direct sigma/rendering import.
export type { NodeHoverDrawingFunction }

// Resolve the computed font-family from the live document root, avoiding
// `var(--font-sans)` literal strings that canvas 2D silently ignores.
// Falls back to "system-ui, sans-serif" in SSR / test environments.
export function resolveCanvasFont(root: Element): string {
  if (typeof getComputedStyle === "undefined") return "system-ui, sans-serif"
  const raw = getComputedStyle(root).getPropertyValue("--font-sans").trim()
  if (!raw) return "system-ui, sans-serif"
  // CSS custom-property values may themselves contain var() if chained;
  // strip surrounding quotes if present (CSS returns them for string values).
  return raw.replace(/^['"]|['"]$/g, "") || "system-ui, sans-serif"
}

/**
 * Returns a `NodeHoverDrawingFunction` bound to the supplied tokens.
 * Call once per token snapshot and pass as `defaultDrawNodeHover` to Sigma.
 *
 * Contract for Agent B (Atlas.tsx integration):
 *   import: `import { makeDrawNodeHover } from "@/lib/graph/draw-node-hover"`
 *   usage:
 *     // On Sigma construction:
 *     defaultDrawNodeHover: makeDrawNodeHover(currentTokens)
 *     // On theme change (inside the setSetting loop that updates labelColor):
 *     sigma.setSetting("defaultDrawNodeHover", makeDrawNodeHover(currentTokens))
 */
export function makeDrawNodeHover(tokens: MapTokens): NodeHoverDrawingFunction {
  return function drawNodeHover(context, data, settings) {
    const size = settings.labelSize
    const font = settings.labelFont
    const weight = settings.labelWeight

    // Use resolved font-family (not var()). Prefer tokens.fontSans (already
    // resolved at mount via resolveMapTokens) over sigma's labelFont setting.
    const fontFamily = tokens.fontSans ?? font ?? "system-ui, sans-serif"
    context.font = `${weight} ${size}px ${fontFamily}`

    // Plate background: background token (already hex-normalised by normalizeColor)
    const plateBg = tokens.background
    const plateText = tokens.foreground

    const PADDING = 2

    if (typeof data.label === "string") {
      const textWidth = context.measureText(data.label).width
      const boxWidth = Math.round(textWidth + 5)
      const boxHeight = Math.round(size + 2 * PADDING)
      const radius = Math.max(data.size, size / 2) + PADDING
      const angleRadian = Math.asin(Math.min(1, boxHeight / 2 / radius))
      const xDeltaCoord = Math.sqrt(Math.abs(radius ** 2 - (boxHeight / 2) ** 2))

      context.shadowOffsetX = 0
      context.shadowOffsetY = 1
      context.shadowBlur = 6
      context.shadowColor = "rgba(0,0,0,0.35)"

      context.beginPath()
      context.moveTo(data.x + xDeltaCoord, data.y + boxHeight / 2)
      context.lineTo(data.x + radius + boxWidth, data.y + boxHeight / 2)
      context.lineTo(data.x + radius + boxWidth, data.y - boxHeight / 2)
      context.lineTo(data.x + xDeltaCoord, data.y - boxHeight / 2)
      context.arc(data.x, data.y, radius, angleRadian, -angleRadian)
      context.closePath()

      context.fillStyle = plateBg
      context.fill()
    } else {
      context.shadowOffsetX = 0
      context.shadowOffsetY = 1
      context.shadowBlur = 6
      context.shadowColor = "rgba(0,0,0,0.35)"

      context.beginPath()
      context.arc(data.x, data.y, data.size + PADDING, 0, Math.PI * 2)
      context.closePath()
      context.fillStyle = plateBg
      context.fill()
    }

    context.shadowOffsetX = 0
    context.shadowOffsetY = 0
    context.shadowBlur = 0

    // Subtle border at 20% opacity
    context.strokeStyle = plateText + "33"
    context.lineWidth = 0.5
    context.stroke()

    // Label text
    context.fillStyle = plateText
    context.textAlign = "left"
    context.textBaseline = "middle"
    if (typeof data.label === "string") {
      const radius = Math.max(data.size, size / 2) + PADDING
      const xDeltaCoord = Math.sqrt(
        Math.abs(radius ** 2 - ((size + 2 * PADDING) / 2) ** 2),
      )
      context.fillText(data.label, data.x + xDeltaCoord + 3, data.y)
    }
  }
}
