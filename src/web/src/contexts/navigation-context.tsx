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

export interface ChatSeed {
  /** The text to drop into the chat composer. */
  text: string
  /** Monotonic counter so duplicate text values still trigger a re-seed. */
  nonce: number
}

interface NavigationContextValue {
  activePane: Pane
  goTo: (pane: Pane) => void
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
    (pane: Pane) => {
      onPaneChange(pane)
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

export function useNavigation(): NavigationContextValue {
  return useContext(NavigationContext)
}
