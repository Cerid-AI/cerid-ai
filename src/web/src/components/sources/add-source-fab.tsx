// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * F2 — Add Source floating action button.
 *
 * Bottom-right FAB. Click reveals a radial menu of the 9 family
 * groupings (.cerid-radial-stagger petal cascade). Click on a family
 * passes upward so the parent surface can launch the F3 wizard with
 * that family pre-selected.
 *
 * Hotkey: ⌘⇧S — focus + open the radial menu.
 */

import { useEffect, useRef, useState } from "react"
import {
  Calendar,
  Files,
  Library,
  Mail,
  MessageCircle,
  Mic,
  Plug,
  Plus,
  Rss,
  Webhook,
  X,
  type LucideIcon,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { useHotkey } from "@/hooks/use-hotkey"

export type SourceFamily =
  | "files"
  | "feeds"
  | "chat"
  | "mail"
  | "calendar"
  | "media"
  | "webhook"
  | "adapter"
  | "pack"

interface FamilyMeta {
  family: SourceFamily
  label: string
  icon: LucideIcon
  angle: number // degrees, 0 = east, clockwise on screen (180 = left, 270 = up)
  radius: number // px from the FAB's centre
}

// The FAB sits in the bottom-right corner, so the only room is up and to the
// left: every petal stays between 180° and 270°. Nine 44 px petals do not fit
// on one arc of that quadrant without overlapping, hence two rings.
const INNER_PX = 110
const OUTER_PX = 170

const FAMILY_PETALS: FamilyMeta[] = [
  { family: "files", label: "Files", icon: Files, angle: 180, radius: INNER_PX },
  { family: "feeds", label: "Feeds", icon: Rss, angle: 210, radius: INNER_PX },
  { family: "mail", label: "Mail", icon: Mail, angle: 240, radius: INNER_PX },
  { family: "calendar", label: "Calendar", icon: Calendar, angle: 270, radius: INNER_PX },
  { family: "chat", label: "Chat", icon: MessageCircle, angle: 180, radius: OUTER_PX },
  { family: "media", label: "Media", icon: Mic, angle: 202.5, radius: OUTER_PX },
  { family: "webhook", label: "Webhook", icon: Webhook, angle: 225, radius: OUTER_PX },
  { family: "adapter", label: "Adapter", icon: Plug, angle: 247.5, radius: OUTER_PX },
  { family: "pack", label: "Packs", icon: Library, angle: 270, radius: OUTER_PX },
]

interface AddSourceFabProps {
  onSelectFamily: (family: SourceFamily) => void
}

export function AddSourceFab({ onSelectFamily }: AddSourceFabProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Click-away handler
  useEffect(() => {
    if (!open) return
    const onClickAway = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    window.addEventListener("mousedown", onClickAway)
    return () => window.removeEventListener("mousedown", onClickAway)
  }, [open])

  // Hotkey: ⌘⇧S — toggle the radial menu.
  useHotkey("meta+shift+s", () => setOpen((v) => !v))
  // Escape closes.
  useHotkey("escape", () => setOpen(false), { enabled: open })

  return (
    <div
      ref={containerRef}
      className="pointer-events-none fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center"
    >
      {/* Petals */}
      {open &&
        FAMILY_PETALS.map((p, i) => {
          const rad = (p.angle * Math.PI) / 180
          const dx = Math.cos(rad) * p.radius
          const dy = Math.sin(rad) * p.radius
          const Icon = p.icon
          return (
            <button
              key={p.family}
              type="button"
              onClick={() => {
                onSelectFamily(p.family)
                setOpen(false)
              }}
              className={cn(
                "pointer-events-auto absolute flex h-11 w-11 flex-col items-center justify-center rounded-full border border-border bg-card text-foreground shadow-md transition-colors",
                "cerid-radial-stagger cerid-press hover:bg-accent",
              )}
              style={
                {
                  // The radial offset is consumed by the `cerid-radial-emerge`
                  // keyframe (`to { translate(var(--petal-x), var(--petal-y)) }`)
                  // and the reduced-motion fallback. An inline `transform` here
                  // would be clobbered by that animation, collapsing every petal
                  // onto the FAB centre — expose the offsets as the CSS vars the
                  // keyframe expects instead.
                  "--petal-x": `${dx.toFixed(2)}px`,
                  "--petal-y": `${dy.toFixed(2)}px`,
                  "--petal-index": i,
                } as React.CSSProperties
              }
              aria-label={`Add ${p.label} source`}
              title={p.label}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
            </button>
          )
        })}

      {/* FAB */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close add source menu" : "Add a new source"}
        aria-expanded={open}
        className={cn(
          "pointer-events-auto cerid-press flex h-14 w-14 items-center justify-center rounded-full bg-brand text-brand-foreground shadow-lg transition-transform",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          open && "rotate-45",
        )}
      >
        {open ? (
          <X className="h-6 w-6" aria-hidden="true" />
        ) : (
          <Plus className="h-6 w-6" aria-hidden="true" />
        )}
      </button>
    </div>
  )
}
