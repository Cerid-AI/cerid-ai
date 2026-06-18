// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SVG filter defs for the `.liquid-glass` utility. Mount once at App
 * root. Reused by every glass surface via `filter: url(#cerid-liquid-glass)`.
 *
 * Subtle refraction (feTurbulence + feDisplacementMap at scale=6)
 * gives the surface a hint of physical material without distorting
 * the content behind enough to hurt readability.
 */

export function LiquidGlassDefs() {
  return (
    <svg
      aria-hidden="true"
      width="0"
      height="0"
      style={{ position: "absolute", pointerEvents: "none" }}
    >
      <defs>
        <filter id="cerid-liquid-glass" x="0%" y="0%" width="100%" height="100%">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.012 0.012"
            numOctaves="2"
            seed="3"
            result="noise"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="noise"
            scale="6"
            xChannelSelector="R"
            yChannelSelector="G"
            result="displaced"
          />
          <feGaussianBlur in="displaced" stdDeviation="0.4" result="softened" />
          <feMerge>
            <feMergeNode in="softened" />
          </feMerge>
        </filter>
      </defs>
    </svg>
  )
}
