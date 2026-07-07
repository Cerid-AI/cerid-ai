// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Task 3.7 — mobile-minimum bottom tab bar. Persistent <md navigation
// exposing exactly Chat / Capture / Menu so the companion loop (ask +
// capture) is reachable on a phone-sized viewport without the full
// desktop sidebar. Capture is wired via a decoupled custom event so the
// FAB (which owns its own open state) doesn't need prop threading.

import { MessageSquare, Plus, Menu } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavigation } from "@/contexts/navigation-context"

interface BottomTabBarProps {
  /** Opens the sidebar Sheet — app-layout owns `sidebarSheetOpen`. */
  onOpenMenu: () => void
}

const TAB_CLASS =
  "cerid-press flex min-h-11 min-w-11 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 text-label-xs font-medium"

// Panes where App.tsx unmounts QuickCaptureFab (BETA-001 gating — see the
// comment above its render in App.tsx). Capture must be visibly disabled
// here too, or the button looks live but silently no-ops.
const CAPTURE_UNAVAILABLE_PANES = new Set(["sources", "subjects", "briefs"])

export function BottomTabBar({ onOpenMenu }: BottomTabBarProps) {
  const { activePane, goTo } = useNavigation()
  const chatActive = activePane === "chat"
  const captureDisabled = CAPTURE_UNAVAILABLE_PANES.has(activePane)

  return (
    <nav
      aria-label="Primary"
      className="safe-area-bottom fixed inset-x-0 bottom-0 z-40 flex border-t bg-background md:hidden"
    >
      <button
        type="button"
        onClick={() => goTo("chat")}
        aria-label="Chat"
        aria-current={chatActive ? "page" : undefined}
        className={cn(TAB_CLASS, chatActive ? "text-brand" : "text-muted-foreground")}
      >
        <MessageSquare className="h-5 w-5" aria-hidden="true" />
        Chat
      </button>
      <button
        type="button"
        onClick={() => window.dispatchEvent(new Event("cerid:quick-capture"))}
        aria-label="Capture"
        disabled={captureDisabled}
        aria-disabled={captureDisabled ? "true" : undefined}
        className={cn(
          TAB_CLASS,
          "text-muted-foreground",
          captureDisabled && "opacity-50 pointer-events-none",
        )}
      >
        <Plus className="h-5 w-5" aria-hidden="true" />
        Capture
      </button>
      <button
        type="button"
        onClick={onOpenMenu}
        aria-label="Menu"
        className={cn(TAB_CLASS, "text-muted-foreground")}
      >
        <Menu className="h-5 w-5" aria-hidden="true" />
        Menu
      </button>
    </nav>
  )
}
