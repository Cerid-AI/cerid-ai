// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import App from "./App"
import "./index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 30s stale-time prevents the refetch storm that used to hit the
      // backend whenever a user flipped between Chat/KB/Health panes
      // in rapid succession. Individual hooks can override with their
      // own staleTime for faster-moving data (health polling = 10s,
      // credits = 60s).
      staleTime: 30_000,
      retry: 1,
      // refetchOnWindowFocus defaults to true in TanStack Query v5 which
      // triggers a cascade of refetches every time the user Cmd-Tabs
      // back to the tab. We already have refetchInterval set on the
      // hooks that actually need live updates, so turning focus refetch
      // off cuts redundant traffic without losing freshness.
      refetchOnWindowFocus: false,
      // Same reasoning: refetchOnMount defaults to true, which causes
      // a new GET on every pane switch even when the cached data is
      // under staleTime. "always" → "true" respects staleTime semantics.
      refetchOnMount: true,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
)