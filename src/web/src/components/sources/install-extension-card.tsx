// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Install browser extension card.
 *
 * Lives inside the Connectors sub-tab. Two deep-link buttons to the
 * Chrome Web Store and Firefox Add-ons. Plain card surface (not
 * Liquid Glass — reserved for the 9 hero surfaces).
 */

import { ExternalLink, Globe } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

// Store URLs are placeholders until the extension is reviewed and
// published; the operator can override via env at build time.
const CHROME_STORE_URL =
  import.meta.env.VITE_CERID_EXTENSION_CHROME_URL ||
  "https://chromewebstore.google.com/detail/cerid-ai/PLACEHOLDER"
const FIREFOX_AMO_URL =
  import.meta.env.VITE_CERID_EXTENSION_FIREFOX_URL ||
  "https://addons.mozilla.org/firefox/addon/cerid-ai/"

export function InstallExtensionCard() {
  return (
    <Card className="space-y-3 px-5 py-4">
      <div>
        <h3 className="text-sm font-medium text-foreground">Browser extension</h3>
        <p className="mt-0.5 text-label-xs text-muted-foreground">
          Capture any web page into your knowledge base with one click.
        </p>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <Button asChild className="cerid-press flex-1" variant="outline">
          <a href={CHROME_STORE_URL} target="_blank" rel="noopener noreferrer">
            <Globe className="mr-2 h-4 w-4" />
            Chrome
            <ExternalLink className="ml-1 h-3 w-3 opacity-60" />
          </a>
        </Button>
        <Button asChild className="cerid-press flex-1" variant="outline">
          <a href={FIREFOX_AMO_URL} target="_blank" rel="noopener noreferrer">
            <Globe className="mr-2 h-4 w-4" />
            Firefox
            <ExternalLink className="ml-1 h-3 w-3 opacity-60" />
          </a>
        </Button>
      </div>
    </Card>
  )
}
