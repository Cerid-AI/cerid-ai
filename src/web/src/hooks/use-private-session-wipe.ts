// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useRef } from "react"
import { useConversationsContext } from "@/contexts/conversations-context"
import { wipePrivateSession } from "@/lib/api/settings"
import { logSwallowedError } from "@/lib/log-swallowed"

const TAB_ID_KEY = "cerid-l4-tab-id"
const LEVEL_KEY = "cerid-private-mode-level"

/** One id per browser tab, kept for the tab's lifetime. */
export function getTabSessionId(): string {
  try {
    const existing = sessionStorage.getItem(TAB_ID_KEY)
    if (existing) return existing
    const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    sessionStorage.setItem(TAB_ID_KEY, id)
    return id
  } catch (err) {
    logSwallowedError(err, "private-session.tab_id")
    return `tab-${Date.now()}`
  }
}

function currentLevel(): number {
  try { return Number(localStorage.getItem(LEVEL_KEY) ?? "0") } catch { return 0 }
}

/** At Level 4, closing the tab forgets its private conversations on the
 *  server. Mount once per tab (App): the level and the conversations are read
 *  when the tab closes, so one listener covers every level change. */
export function usePrivateSessionWipe(): void {
  const { conversations } = useConversationsContext()
  const privateIds = useRef<string[]>([])
  useEffect(() => {
    privateIds.current = conversations.filter((c) => c.private).map((c) => c.id)
  }, [conversations])

  useEffect(() => {
    const onBeforeUnload = () => {
      if (currentLevel() !== 4) return
      try {
        wipePrivateSession({ sessionId: getTabSessionId(), conversationIds: privateIds.current })
      } catch (err) {
        logSwallowedError(err, "private-session.wipe")
      }
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [])
}

/** Renders nothing; mounts the wipe listener inside the conversations provider. */
export function PrivateSessionWipe(): null {
  usePrivateSessionWipe()
  return null
}
