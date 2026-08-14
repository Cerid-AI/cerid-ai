// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Apple-connectors discovery card (UX-27).
 *
 * The six Apple connectors only exist as rows when the desktop bridge is
 * present (macOS desktop app). A web-only user got no hint the flagship
 * feature existed at all — this card advertises them and says exactly
 * what is required. Rendered only when the bridge is absent; on desktop
 * the real rows take its place.
 */

import { Download, Laptop } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

// Env-overridable like the extension-store URLs (install-extension-card).
const DESKTOP_DOWNLOAD_URL =
  import.meta.env.VITE_CERID_DESKTOP_DOWNLOAD_URL ||
  "https://github.com/Cerid-AI/cerid-ai/releases/latest"

const APPLE_CONNECTOR_NAMES = [
  "Notes",
  "Mail",
  "iMessage",
  "Calendar",
  "Photos",
  "Reminders",
] as const

export function AppleDesktopCard() {
  return (
    <Card className="space-y-3 px-5 py-4" data-testid="apple-desktop-card">
      <div>
        <h3 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <Laptop className="h-4 w-4" aria-hidden="true" />
          Apple connectors
        </h3>
        <p className="mt-0.5 text-label-xs text-muted-foreground">
          {APPLE_CONNECTOR_NAMES.join(", ")} — six connectors that read your
          Apple data locally. Available in the Cerid AI desktop app for macOS.
        </p>
      </div>

      <Button asChild className="cerid-press w-full" variant="outline">
        <a href={DESKTOP_DOWNLOAD_URL} target="_blank" rel="noopener noreferrer">
          <Download className="mr-2 h-4 w-4" />
          Get the desktop app
        </a>
      </Button>
    </Card>
  )
}
