// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Atlas right-click context menu. Sigma's WebGL canvas can't host
// shadcn's ContextMenu component directly (no DOM target per node),
// so we listen for sigma's `rightClickNode` event, capture viewport
// coordinates, and render a portal-positioned floating menu.
//
// Menu items:
//   - Cite in chat — composeChat({ text: "@EntityName " })
//   - Open in Wiki — goTo("subjects", { mode: "wiki", entity })
//   - Copy entity id — clipboard

import { useEffect, useRef } from "react"
import { Quote, BookOpen, Copy, X } from "lucide-react"

export interface AtlasContextMenuTarget {
  entityId: string
  entityName: string
  /** Page-relative position (clientX/Y from the click event) */
  x: number
  y: number
}

export interface AtlasContextMenuProps {
  target: AtlasContextMenuTarget | null
  onClose: () => void
  onCite: (entityId: string, entityName: string) => void
  onOpenWiki: (entityId: string) => void
}

export function AtlasContextMenu({ target, onClose, onCite, onOpenWiki }: AtlasContextMenuProps) {
  const menuRef = useRef<HTMLDivElement | null>(null)

  // Close on outside click + Escape
  useEffect(() => {
    if (!target) return
    function onDocClick(e: MouseEvent) {
      const m = menuRef.current
      if (!m) return
      if (!m.contains(e.target as Node)) onClose()
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("mousedown", onDocClick)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDocClick)
      document.removeEventListener("keydown", onKey)
    }
  }, [target, onClose])

  if (!target) return null

  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(target.entityId).catch(() => {
        /* clipboard denied — silent; user can re-try */
      })
    }
    onClose()
  }

  // Pin to viewport edge if too close to right/bottom
  const menuWidth = 220
  const menuHeight = 160
  const left = Math.min(target.x, window.innerWidth - menuWidth - 8)
  const top = Math.min(target.y, window.innerHeight - menuHeight - 8)

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label={`Actions for ${target.entityName}`}
      style={{ left, top }} // drift-allowed: viewport-clamped position derived from anchor DOM measurements
      className="fixed z-50 w-[220px] rounded-lg border border-border bg-popover py-1 text-sm shadow-xl" // drift-allowed: context menu pinned width
    >
      <div className="flex items-center gap-2 border-b px-3 py-2 text-label-xs uppercase tracking-wide text-muted-foreground">
        <span className="grow truncate" title={target.entityName}>
          {target.entityName}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-0.5 text-muted-foreground hover:bg-accent/40"
          aria-label="Close menu"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onCite(target.entityId, target.entityName)
          onClose()
        }}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent/40"
      >
        <Quote className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <span>Cite in chat</span>
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onOpenWiki(target.entityId)
          onClose()
        }}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent/40"
      >
        <BookOpen className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <span>Open in Wiki</span>
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={handleCopy}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent/40"
      >
        <Copy className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <span>Copy entity id</span>
      </button>
    </div>
  )
}
