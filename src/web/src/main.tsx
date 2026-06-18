// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClientProvider } from "@tanstack/react-query"
import { Toaster } from "sonner"
import App from "./App"
import { queryClient } from "@/lib/query-client"
import { initSentry } from "@/lib/sentry"
import { applyPersistedAppearance } from "@/hooks/use-theme"
import "./index.css"

// FOUC guard — apply persisted theme/density/motion to <html> before the
// first React render (and therefore before first paint).
applyPersistedAppearance()

// Fire-and-forget Sentry init (no-ops when VITE_SENTRY_DSN_WEB is unset
// or we're in DEV mode). Doesn't block render — the rest of the app
// can mount before the SDK chunk loads.
void initSentry()

// Stale-chunk recovery. Lazy-loaded panes (Sources/Subjects/Settings) are
// code-split; after a deploy the chunk hashes change, so a tab still running
// the old app shell will 404 when it lazily imports a now-missing chunk and the
// pane fails to load. Vite fires `vite:preloadError` on that failure — reload
// once to pull the fresh index + chunk hashes. Guarded by a 10s timestamp so a
// genuinely-broken chunk (not merely stale) can't cause a reload loop.
window.addEventListener("vite:preloadError", () => {
  const KEY = "cerid:last-chunk-reload"
  const last = Number(sessionStorage.getItem(KEY) ?? 0)
  if (Date.now() - last < 10_000) return
  sessionStorage.setItem(KEY, String(Date.now()))
  window.location.reload()
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster richColors closeButton position="top-right" />
    </QueryClientProvider>
  </StrictMode>
)