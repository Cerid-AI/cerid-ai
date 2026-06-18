// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * First-paint reveal sequence.
 *
 * 0-1100ms total: navy fill → Cerid "C" mark fades in with teal shine
 * sweep → mark dissolves → content underneath rises into place. After
 * the first visit, a sessionStorage flag suppresses the sequence so
 * the user only sees it once per session.
 *
 * Honors `prefers-reduced-motion` — the sequence becomes a single 1f
 * fade-out (still renders so the underlying app gets the same mount
 * order, but visually instant).
 *
 * The actual content fade-in is wired via the `.cerid-content-rise`
 * class which AppLayout (or the first pane) opts into on mount —
 * this component owns the overlay only.
 */

import { useEffect, useState } from "react"

const SESSION_KEY = "cerid:opening-sequence-played"

export function OpeningSequence() {
  const [phase, setPhase] = useState<"playing" | "fading" | "done">(() => {
    if (typeof window === "undefined") return "done"
    if (sessionStorage.getItem(SESSION_KEY) === "1") return "done"
    const prefersReduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    if (prefersReduced) {
      sessionStorage.setItem(SESSION_KEY, "1")
      return "done"
    }
    return "playing"
  })

  useEffect(() => {
    if (phase !== "playing") return
    // Mark animation ends ~1100ms; fade the overlay over the next 200ms.
    const fadeAt = window.setTimeout(() => setPhase("fading"), 900)
    const doneAt = window.setTimeout(() => {
      sessionStorage.setItem(SESSION_KEY, "1")
      setPhase("done")
    }, 1300)
    return () => {
      window.clearTimeout(fadeAt)
      window.clearTimeout(doneAt)
    }
  }, [phase])

  if (phase === "done") return null

  return (
    <div
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        background: "oklch(0.16 0.03 240)", // navy deep
        opacity: phase === "fading" ? 0 : 1,
        transition: "opacity 280ms cubic-bezier(0.16, 1, 0.3, 1)",
        pointerEvents: "none",
        display: "grid",
        placeItems: "center",
      }}
    >
      <div className="cerid-mark-reveal" style={{ position: "relative" }}>
        <svg
          width="120"
          height="120"
          viewBox="0 0 120 120"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* Outer ring (vault rim — metallic gold) */}
          <circle
            cx="60"
            cy="60"
            r="50"
            fill="none"
            stroke="oklch(0.78 0.12 85)"
            strokeWidth="1.5"
            opacity="0.7"
          />
          {/* Inner shield (navy fill with teal glow) */}
          <circle
            cx="60"
            cy="60"
            r="44"
            fill="oklch(0.20 0.04 240)"
            stroke="oklch(0.82 0.16 178)"
            strokeWidth="2"
          />
          {/* The "C" — opens to the right, the bioluminescent gap */}
          <path
            d="M 78 38 A 28 28 0 1 0 78 82"
            fill="none"
            stroke="oklch(0.82 0.16 178)"
            strokeWidth="4"
            strokeLinecap="round"
          />
          {/* Inner teal glow */}
          <circle
            cx="60"
            cy="60"
            r="28"
            fill="none"
            stroke="oklch(0.82 0.16 178)"
            strokeWidth="1"
            opacity="0.35"
          />
        </svg>
      </div>
    </div>
  )
}
