// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Desktop setup — the part of onboarding the SERVER cannot know about.
//
// The setup wizard is gated on `setup_required` / `onboarding_complete`, both
// of which describe the SERVER. Connect a brand-new desktop client to a server
// that was configured months ago from a browser and both are false, so the
// client goes straight to the main app having never asked for a TCC grant,
// never mentioned the Apple connectors, and never told the user where they
// live. The server's onboarding flag is not a statement about this Mac.
//
// This is deliberately NOT the nine-step wizard. Re-running API keys, storage
// and LLM choice against an already-configured server would be wrong and
// destructive. It is the three things that are per-machine:
//
//   1. the connection (already done by the time this shows, but shown so it can
//      be changed)
//   2. macOS permissions — PermissionsStep, which until now was written,
//      tested, and mounted NOWHERE: no user could reach it
//   3. where the connectors actually are, because "Sources → Connectors" is not
//      guessable
//
// Completion is recorded per-machine in localStorage, not on the server, for
// the same reason this exists: the server's flag answers a different question.

import { useCallback, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Laptop, ArrowRight } from "lucide-react"
import { PermissionsStep } from "@/components/setup/permissions-step"
import { ServerConnectionForm } from "@/components/settings/server-connection-form"
import { logSwallowedError } from "@/lib/log-swallowed"

export const DESKTOP_SETUP_KEY = "cerid-desktop-setup-complete"

/** True when this MACHINE still needs desktop setup.
 *
 *  Deliberately independent of the server's `onboarding_complete`: a configured
 *  server says nothing about whether this Mac has granted Full Disk Access.
 *  Pure + exported so the gate can be tested without a bridge or a server. */
export function needsDesktopSetup(opts: {
  hasDesktopBridge: boolean
  completedFlag: string | null
}): boolean {
  if (!opts.hasDesktopBridge) return false // browser build — nothing per-machine
  return opts.completedFlag !== "true"
}

export function markDesktopSetupComplete(): void {
  try {
    localStorage.setItem(DESKTOP_SETUP_KEY, "true")
  } catch (err) {
    logSwallowedError(err, "localStorage.setItem", { key: DESKTOP_SETUP_KEY })
  }
}

export function readDesktopSetupFlag(): string | null {
  try {
    return localStorage.getItem(DESKTOP_SETUP_KEY)
  } catch {
    return null
  }
}

interface DesktopSetupProps {
  open: boolean
  onDone: () => void
  /** Jump to Sources → Connectors, where the Apple rows live. */
  onOpenConnectors: () => void
}

export function DesktopSetup({ open, onDone, onOpenConnectors }: DesktopSetupProps) {
  const [step, setStep] = useState(0)

  const finish = useCallback(() => {
    markDesktopSetupComplete()
    onDone()
  }, [onDone])

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent
        className="max-w-xl gap-0 overflow-hidden p-0 [&>button]:hidden flex flex-col max-h-[85vh] bg-circuit"
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogTitle className="sr-only">Set up this Mac</DialogTitle>
        <DialogDescription className="sr-only">
          Permissions and connectors for this machine.
        </DialogDescription>

        <div className="overflow-y-auto p-5 density-stack">
          <div className="mb-2 flex items-center justify-center">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10 glow-teal">
              <Laptop className="h-5 w-5 text-brand" />
            </div>
          </div>
          <h3 className="mb-1 text-center text-lg font-semibold">Set up this Mac</h3>
          <p className="mb-4 text-center text-label-xs text-muted-foreground">
            Your server is already configured. These settings are specific to this computer.
          </p>

          {step === 0 && (
            <div className="density-stack" data-testid="desktop-setup-connection">
              <ServerConnectionForm saveLabel="Save & reconnect">
                <p className="text-xs text-muted-foreground">
                  Where this app sends its requests.
                </p>
              </ServerConnectionForm>
              <Button className="w-full" onClick={() => setStep(1)} data-testid="desktop-setup-next">
                Next — permissions <ArrowRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          )}

          {step === 1 && (
            <div className="density-stack" data-testid="desktop-setup-permissions">
              <PermissionsStep />
              <Button className="w-full" onClick={() => setStep(2)} data-testid="desktop-setup-next">
                Next — connectors <ArrowRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          )}

          {step === 2 && (
            <div className="density-stack" data-testid="desktop-setup-connectors">
              <Card>
                <CardContent className="py-3 text-label-xs text-muted-foreground density-stack">
                  <p>
                    Apple Notes, Mail, iMessage, Calendar and Photos each read from this Mac and
                    send the results to your server. They live in{" "}
                    <span className="font-medium text-foreground">Sources → Connectors</span>.
                  </p>
                  <p>
                    Open one, press <span className="font-medium text-foreground">Rescan</span>, then{" "}
                    <span className="font-medium text-foreground">Ingest</span>. If a row says
                    &ldquo;needs permission&rdquo;, the grant is missing rather than the data.
                  </p>
                </CardContent>
              </Card>
              <Button
                className="w-full"
                onClick={() => {
                  markDesktopSetupComplete()
                  onOpenConnectors()
                }}
                data-testid="desktop-setup-open-connectors"
              >
                Take me to Connectors <ArrowRight className="ml-1 h-4 w-4" />
              </Button>
              <Button variant="ghost" className="w-full" onClick={finish}>
                I&rsquo;ll do this later
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
