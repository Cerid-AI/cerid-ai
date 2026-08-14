// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect } from "react"
import { useNavigation } from "@/contexts/navigation-context"
import { logSwallowedError } from "@/lib/log-swallowed"

/** The bridge the Electron preload installs, or null in a browser build. */
interface DeepLinkBridge {
  consume: () => Promise<Array<{ kind: string; id: string }>>
  onAvailable: (cb: () => void) => () => void
}

function deepLinkBridge(): DeepLinkBridge | null {
  if (typeof window === "undefined") return null
  return (window as unknown as { cerid?: { deepLinks?: DeepLinkBridge } }).cerid?.deepLinks ?? null
}

/**
 * Route `cerid://` links into the app.
 *
 * Every item Cerid donates to Spotlight carries a `cerid://kb/<artifact-id>`
 * contentURL, and until this existed clicking one only raised the window —
 * which looks exactly like a link that worked, on whatever pane the user
 * happened to leave open.
 *
 * Drain-on-mount is the load-bearing half, not the subscription: on a cold
 * launch the link is delivered to the main process before this bundle has
 * parsed, so there is nothing to notify. The main process queues it and this
 * takes it. The subscription only covers the app-already-running case.
 *
 * Mounted once, at the app root. Two mounts would both drain, and whichever
 * lost the race would navigate nowhere.
 */
export function useDeepLinks(): void {
  const { goTo } = useNavigation()

  useEffect(() => {
    const bridge = deepLinkBridge()
    if (bridge === null) return

    let cancelled = false

    const drain = () => {
      bridge
        .consume()
        .then((links) => {
          if (cancelled || links.length === 0) return
          // Last wins. Someone who clicked several results while the app was
          // starting means the most recent one; replaying all of them would
          // flash through the rest on the way there.
          const last = links[links.length - 1]
          if (last.kind === "artifact" && last.id) {
            // "knowledge" is a legacy pane target: NavigationProvider redirects
            // it to Sources with ?sources_mode=library, which is where
            // KnowledgePane — and the artifact preview — actually render.
            goTo("knowledge", { artifact: last.id })
          }
        })
        .catch((err: unknown) => {
          logSwallowedError(err, "deepLinks.consume")
        })
    }

    drain()
    const unsubscribe = bridge.onAvailable(drain)
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [goTo])
}
