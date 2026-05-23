// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClientProvider } from "@tanstack/react-query"
import { Toaster } from "sonner"
import App from "./App"
import { queryClient } from "@/lib/query-client"
import { initSentry } from "@/lib/sentry"
import "./index.css"

// Fire-and-forget Sentry init (no-ops when VITE_SENTRY_DSN_WEB is unset
// or we're in DEV mode). Doesn't block render — the rest of the app
// can mount before the SDK chunk loads.
void initSentry()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster richColors closeButton position="top-right" />
    </QueryClientProvider>
  </StrictMode>
)