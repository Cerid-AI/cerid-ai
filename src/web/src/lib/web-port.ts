// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// What the web container's proxy tells the app about the port it is served
// on. There are some 340 fetch call sites and no shared client, so the two
// signals are read where every response passes: fetch itself.

import { toast } from "sonner"

const SENT_KEY = "cerid:sign-in-sent"

let portAccess: string | null = null
const listeners = new Set<() => void>()

export function getPortAccess(): string | null {
  return portAccess
}

export function subscribePortAccess(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

interface WatchOptions {
  target?: { fetch: typeof fetch }
  navigate?: (url: string) => void
}

export function watchWebPort({
  target = window,
  navigate = (url) => window.location.assign(url),
}: WatchOptions = {}): void {
  const upstream = target.fetch
  let leaving = false

  const signIn = () => {
    if (leaving) return
    leaving = true
    sessionStorage.setItem(SENT_KEY, "1")
    const here = window.location.pathname + window.location.search + window.location.hash
    navigate(`/__auth/login?next=${encodeURIComponent(here)}`)
  }

  target.fetch = async (...args: Parameters<typeof fetch>) => {
    const res = await upstream.apply(target, args)

    const access = res.headers.get("X-Cerid-Port-Access")
    if (access && access !== portAccess) {
      portAccess = access
      listeners.forEach((listener) => listener())
    }

    if (res.status === 401 && res.headers.get("X-Cerid-Auth") === "sign-in-required") {
      // Already sent once and still refused: the cookie did not stick.
      // Offer the page instead of bouncing between it and here.
      if (sessionStorage.getItem(SENT_KEY)) {
        toast.error("Sign in to use Cerid on this port.", {
          id: SENT_KEY,
          duration: Infinity,
          action: { label: "Sign in", onClick: signIn },
        })
      } else {
        signIn()
      }
    } else if (res.ok && access) {
      sessionStorage.removeItem(SENT_KEY)
    }
    return res
  }
}
