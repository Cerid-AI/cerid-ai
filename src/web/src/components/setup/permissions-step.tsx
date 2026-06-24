// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// TCC permission wizard step (Phase D Day 2).
//
// Walks the user through Microphone / Calendar / Reminders / Contacts /
// Photos / Full Disk Access. Two interaction patterns:
//
// 1. Programmatic prompt (mic / cal / rem / contacts / photos) — clicking
//    "Grant" triggers the macOS system permission sheet. If the user
//    already denied, the sheet doesn't reappear — they must flip the
//    toggle in System Settings (we provide a deep-link).
//
// 2. Full Disk Access — no programmatic prompt exists. Clicking "Grant"
//    deep-links to System Settings → Privacy & Security → Full Disk Access.
//    The user adds Cerid AI to the allowed list AND relaunches the app
//    (kernel TCC cache requires this; we surface a relaunch warning).
//
// All permissions are OPTIONAL for the core setup. Each gates specific
// downstream functionality (meeting capture, Apple connectors). The
// wizard surfaces the link so users can come back later, too.

import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  Check,
  X,
  AlertTriangle,
  ChevronRight,
  Loader2,
  Mic,
  Calendar,
  ListTodo,
  Contact,
  Image as ImageIcon,
  Lock,
  ExternalLink,
} from "lucide-react"
import { cn } from "@/lib/utils"

type Category =
  | "microphone"
  | "calendar"
  | "reminders"
  | "contacts"
  | "photos"
  | "full-disk-access"

type Status =
  | "granted"
  | "denied"
  | "restricted"
  | "not-determined"
  | "limited"
  | "unknown"

interface PermissionState {
  category: Category
  status: Status
  required: boolean
  description: string
}

const ICONS: Record<Category, typeof Mic> = {
  microphone: Mic,
  calendar: Calendar,
  reminders: ListTodo,
  contacts: Contact,
  photos: ImageIcon,
  "full-disk-access": Lock,
}

const LABELS: Record<Category, string> = {
  microphone: "Microphone",
  calendar: "Calendar",
  reminders: "Reminders",
  contacts: "Contacts",
  photos: "Photos",
  "full-disk-access": "Full Disk Access",
}

// Categories that require System Settings (no programmatic prompt path
// once denied, OR no prompt at all in the case of FDA).
const NEEDS_SETTINGS_DEEP_LINK: Record<Category, string> = {
  microphone: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
  calendar: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
  reminders: "x-apple.systempreferences:com.apple.preference.security?Privacy_Reminders",
  contacts: "x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts",
  photos: "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos",
  "full-disk-access":
    "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
}

// The Window.cerid global is shared with apple-detail.tsx,
// where the appleConnectors shape lives. Both declarations merge into the
// same global Window.cerid, so we only declare the permissions surface
// here and use a local type-narrow cast when reaching into permissions.
interface CeridPermissionsBridge {
  permissions: {
    getAll: () => Promise<PermissionState[]>
    get: (category: string) => Promise<PermissionState>
    request: (category: string) => Promise<PermissionState>
  }
  app: {
    openExternal: (url: string) => Promise<{ success: boolean; error?: string }>
  }
}

function getCeridBridge(): CeridPermissionsBridge | null {
  if (typeof window === "undefined") return null
  const bridge = (window as Window & { cerid?: unknown }).cerid as
    | (CeridPermissionsBridge & Record<string, unknown>)
    | undefined
  if (!bridge || !bridge.permissions || !bridge.app) return null
  return bridge
}

interface PermissionsStepProps {
  onContinue?: () => void
  onSkip?: () => void
}

export function PermissionsStep({ onContinue, onSkip }: PermissionsStepProps) {
  const [states, setStates] = useState<PermissionState[] | null>(null)
  const [loading, setLoading] = useState<Category | null>(null)
  const [error, setError] = useState<string | null>(null)

  const bridge = getCeridBridge()
  const desktopAvailable = bridge !== null

  const refresh = useCallback(async () => {
    if (!bridge) {
      setStates([])
      return
    }
    try {
      const list = await bridge.permissions.getAll()
      setStates(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to read permissions")
    }
  }, [bridge])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    refresh()
    // Re-poll on window focus — covers FDA-granted-via-Settings + relaunch.
    const onFocus = () => refresh()
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [refresh])

  const handleGrant = useCallback(
    async (cat: Category, currentStatus: Status) => {
      if (!bridge) return
      setLoading(cat)
      setError(null)
      try {
        // If already denied, the system prompt won't reappear — go straight
        // to the Settings deep-link. Same for FDA (no prompt path ever).
        if (currentStatus === "denied" || cat === "full-disk-access") {
          await bridge.app.openExternal(NEEDS_SETTINGS_DEEP_LINK[cat])
        } else {
          await bridge.permissions.request(cat)
        }
        // Refresh after a tick so the system has time to commit the grant.
        await new Promise((r) => setTimeout(r, 300))
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "Permission request failed")
      } finally {
        setLoading(null)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally omitted dep; addition would cause infinite loop or unwanted re-fetch
    [desktopAvailable, refresh],
  )

  if (!desktopAvailable) {
    return (
      <div className="space-y-4" data-testid="permissions-step">
        <div>
          <h2 className="text-xl font-semibold">macOS Permissions</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Permission setup is only available in the Cerid AI desktop app.
            If you're running the web version, you can skip this step — your
            browser handles permission prompts inline when features need them.
          </p>
        </div>
        <div className="flex gap-2 justify-end">
          {onSkip && (
            <Button variant="outline" onClick={onSkip}>
              Skip
            </Button>
          )}
          {onContinue && <Button onClick={onContinue}>Continue</Button>}
        </div>
      </div>
    )
  }

  if (states === null) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        Reading permission state…
      </div>
    )
  }

  const grantedCount = states.filter((s) => s.status === "granted").length

  return (
    <div className="space-y-4" data-testid="permissions-step">
      <div>
        <h2 className="text-xl font-semibold">macOS Permissions</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Cerid only requests permissions for features you intend to use.
          You can grant or revoke any of these later in System Settings.
        </p>
      </div>

      {error && (
        <div className="text-sm text-red-500 p-2 rounded border border-red-500/30 bg-red-500/5" role="alert">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {states.map((s) => {
          const Icon = ICONS[s.category]
          const isGranted = s.status === "granted" || s.status === "limited"
          const isDenied = s.status === "denied" || s.status === "restricted"
          const isFda = s.category === "full-disk-access"
          const busy = loading === s.category

          return (
            <Card
              key={s.category}
              className={cn(
                "p-3",
                isGranted && "border-green-500/30 bg-green-500/5",
                isDenied && "border-amber-500/30 bg-amber-500/5",
              )}
              data-testid={`permission-row-${s.category}`}
            >
              <div className="flex items-start gap-3">
                <Icon className="w-5 h-5 text-muted-foreground flex-shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{LABELS[s.category]}</span>
                    {isGranted && (
                      <span className="inline-flex items-center gap-1 text-xs text-green-600">
                        <Check className="w-3 h-3" />
                        {s.status === "limited" ? "limited" : "granted"}
                      </span>
                    )}
                    {isDenied && (
                      <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                        <X className="w-3 h-3" />
                        denied
                      </span>
                    )}
                    {s.status === "not-determined" && (
                      <span className="text-xs text-muted-foreground">not asked yet</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">{s.description}</p>
                  {isFda && isDenied && (
                    <p className="text-xs text-amber-600 flex items-center gap-1 pt-1">
                      <AlertTriangle className="w-3 h-3" />
                      You'll need to relaunch Cerid after granting Full Disk Access.
                    </p>
                  )}
                </div>
                {!isGranted && (
                  <Button
                    variant={isDenied || isFda ? "outline" : "default"}
                    size="sm"
                    onClick={() => handleGrant(s.category, s.status)}
                    disabled={busy}
                    data-testid={`permission-grant-${s.category}`}
                  >
                    {busy ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : isDenied || isFda ? (
                      <>
                        Open Settings <ExternalLink className="w-3 h-3 ml-1" />
                      </>
                    ) : (
                      <>
                        Grant <ChevronRight className="w-3 h-3 ml-1" />
                      </>
                    )}
                  </Button>
                )}
              </div>
            </Card>
          )
        })}
      </div>

      <div className="text-xs text-muted-foreground text-center">
        {grantedCount} of {states.length} granted · all are optional
      </div>

      <div className="flex gap-2 justify-end pt-2">
        {onSkip && (
          <Button variant="outline" onClick={onSkip}>
            Skip
          </Button>
        )}
        {onContinue && <Button onClick={onContinue}>Continue</Button>}
      </div>
    </div>
  )
}
