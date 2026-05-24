// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Cross-pane navigation context.
 *
 * Lets any child component request a pane change, optionally seeding
 * state on the destination pane. Two operations are supported today:
 *
 *   - ``goTo(pane)`` — switch the active pane.
 *   - ``composeChat({ text })`` — switch to the chat pane and seed the
 *     input box with the supplied text. ChatPanel reads the latest seed
 *     via ``useChatComposerSeed()`` and clears it after consuming.
 *
 * This file is intentionally small and free of business logic — it owns
 * just enough state to let leaf components avoid prop drilling through
 * the AppLayout's render-prop boundary.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"

import type { Pane } from "@/components/layout/sidebar"

/**
 * Phase A Day 9 — Subjects consolidation. The Wiki / Communities / Memories
 * panes were folded into the new Subjects pane (Atlas / Constellation /
 * Timeline / Wiki modes). Legacy callsites that still call `goTo("wiki")`
 * are transparently routed to Subjects with the right mode + URL param.
 *
 * Map shape: legacy pane → { newPane, modeForSubjects }. The mode value is
 * written to `?mode=` so SubjectsPane lands on the right tab; the existing
 * `?entity=` convention from wiki-pane.tsx is preserved.
 */
const LEGACY_PANE_REDIRECTS: Partial<Record<Pane, { pane: Pane; mode: string }>> = {
  wiki: { pane: "subjects", mode: "wiki" },
  communities: { pane: "subjects", mode: "atlas" },
  memories: { pane: "subjects", mode: "atlas" },
  // Phase B Day 9 — knowledge consolidates into Sources.
  knowledge: { pane: "sources", mode: "library" },
  // Phase C Day 2 — Monitoring / Audit / Agents into Settings → Diagnostics.
  monitoring: { pane: "settings", mode: "status" },
  audit: { pane: "settings", mode: "analytics" },
  agents: { pane: "settings", mode: "activity" },
}

// Each destination pane has its own URL param key so legacy redirects
// don't collide on a shared ?mode= slot.
const PARAM_BY_PANE: Partial<Record<Pane, string>> = {
  sources: "sources_mode",
  settings: "diagnostics_tab",
  subjects: "mode",
}

function applyRedirect(target: Pane): Pane {
  const redirect = LEGACY_PANE_REDIRECTS[target]
  if (!redirect) return target
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search)
    const paramName = PARAM_BY_PANE[redirect.pane] ?? "mode"
    params.set(paramName, redirect.mode)
    // When redirecting to settings/Diagnostics, also pre-select the
    // settings tab so the user lands on Diagnostics, not Essentials.
    if (redirect.pane === "settings") {
      try { localStorage.setItem("cerid-settings-tab", "diagnostics") } catch { /* SSR */ }
    }
    const next = params.toString()
    const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
    window.history.replaceState({}, "", url)
  }
  return redirect.pane
}

export interface ChatSeed {
  /** The text to drop into the chat composer. */
  text: string
  /** Monotonic counter so duplicate text values still trigger a re-seed. */
  nonce: number
}

/**
 * Optional state to bundle with a `goTo()` call. Each field maps to
 * a Subjects-pane URL param (`?mode=` / `?entity=`) and is written
 * before the pane change, so SubjectsPane's mount-time URL read
 * lands on the right tab + focal entity.
 */
export interface NavigationOptions {
  /** Subjects pane mode: atlas / constellation / timeline / wiki */
  mode?: string
  /** Focal entity canonical id (a.k.a. wiki slug) */
  entity?: string
}

interface NavigationContextValue {
  activePane: Pane
  goTo: (pane: Pane, options?: NavigationOptions) => void
  composeChat: (input: { text: string }) => void
  /** Consume the latest pending seed; returns null if none. */
  consumeChatSeed: () => ChatSeed | null
}

// Default value used when no NavigationProvider is present (tests, isolated
// renders). All operations are no-ops; consumers should still call them safely.
const NULL_NAVIGATION: NavigationContextValue = {
  activePane: "chat",
  goTo: () => {},
  composeChat: () => {},
  consumeChatSeed: () => null,
}

function writeNavigationUrl(mode: string | undefined, entity: string | undefined) {
  if (typeof window === "undefined") return
  const params = new URLSearchParams(window.location.search)
  if (mode !== undefined) {
    if (mode) params.set("mode", mode)
    else params.delete("mode")
  }
  if (entity !== undefined) {
    if (entity) params.set("entity", entity)
    else params.delete("entity")
  }
  const next = params.toString()
  const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
  window.history.replaceState({}, "", url)
}

const NavigationContext = createContext<NavigationContextValue>(NULL_NAVIGATION)

interface NavigationProviderProps {
  activePane: Pane
  onPaneChange: (pane: Pane) => void
  children: ReactNode
}

export function NavigationProvider({
  activePane,
  onPaneChange,
  children,
}: NavigationProviderProps) {
  const seedRef = useRef<ChatSeed | null>(null)
  const [, force] = useState(0)

  const goTo = useCallback(
    (pane: Pane, options?: NavigationOptions) => {
      // Resolve legacy panes first. The redirect map may itself set ?mode=;
      // explicit options below override that.
      const resolved = applyRedirect(pane)
      if (options) {
        writeNavigationUrl(options.mode, options.entity)
      }
      onPaneChange(resolved)
    },
    [onPaneChange],
  )

  const composeChat = useCallback(
    ({ text }: { text: string }) => {
      const nonce = (seedRef.current?.nonce ?? 0) + 1
      seedRef.current = { text, nonce }
      onPaneChange("chat")
      // Trigger consumers that subscribe via consumeChatSeed in a layout
      // effect after the pane change settles.
      force((n) => n + 1)
    },
    [onPaneChange],
  )

  const consumeChatSeed = useCallback((): ChatSeed | null => {
    const seed = seedRef.current
    seedRef.current = null
    return seed
  }, [])

  const value = useMemo<NavigationContextValue>(
    () => ({ activePane, goTo, composeChat, consumeChatSeed }),
    [activePane, goTo, composeChat, consumeChatSeed],
  )

  return <NavigationContext.Provider value={value}>{children}</NavigationContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components -- consumer hook exported alongside the Provider component (standard React context pattern)
export function useNavigation(): NavigationContextValue {
  return useContext(NavigationContext)
}
