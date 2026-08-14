// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Server connection panel — desktop-only (rendered from the System settings
// category). Lets the desktop client run the Cerid stack locally or connect to
// a remote instance on the LAN (e.g. service on a Mac Pro, client on a Mac
// Mini). Renders nothing in the browser build, where the backend is fixed.
//
// The form itself lives in server-connection-form.tsx because the setup wizard
// mounts the same thing: onboarding used to have no way to supply a
// credential, so a fresh desktop install stalled on step 0 against an
// auth-enabled server.

import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { getConnectionBridge } from "@/lib/cerid-bridge"
import { ServerConnectionForm } from "@/components/settings/server-connection-form"

export function ConnectionSection() {
  // Browser build (no desktop bridge): nothing to configure. Checked here as
  // well as in the form so the empty Card never renders.
  if (!getConnectionBridge()) return null

  return (
    <Card data-testid="connection-section">
      <CardHeader className="pb-2">
        <span className="text-label-xs uppercase tracking-wider text-muted-foreground">
          Server Connection
        </span>
      </CardHeader>
      <CardContent>
        <ServerConnectionForm>
          <p className="text-xs text-muted-foreground">
            Run the Cerid stack on this Mac, or connect to a Cerid instance on another machine on
            your network.
          </p>
        </ServerConnectionForm>
      </CardContent>
    </Card>
  )
}
