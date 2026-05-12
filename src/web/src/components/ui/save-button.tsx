// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SaveButton — a Save-flavored Button that briefly swaps its leading icon
 * to a Check on a successful save before reverting.
 *
 * Usage: pass an async `onSave` that resolves on success / rejects on
 * failure. The component flips to its "saved" pose (Check icon, label
 * override) for {@link SAVED_DURATION_MS} after a successful save.
 *
 * Motion: icon swap uses `animate-in zoom-in-50 duration-150` (Phase 9
 * M-A.8) — same vocabulary as the chat-message copy-confirm chip so the
 * micro-interaction reads as a shared "value committed" gesture.
 *
 * Accessibility: button text changes too, so screen readers narrate the
 * success ("Saved" → "Save changes") without relying on the icon swap.
 * `prefers-reduced-motion: reduce` collapses the duration via the global
 * `index.css` block — no per-component handling required.
 */

import * as React from "react"
import { Check, Save } from "lucide-react"

import { Button, type buttonVariants } from "@/components/ui/button"
import type { VariantProps } from "class-variance-authority"

const SAVED_DURATION_MS = 1_200

type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }

export interface SaveButtonProps extends Omit<ButtonProps, "onClick"> {
  /** Async save handler. Resolved → flip to "saved"; rejected → stay idle. */
  onSave: () => void | Promise<void>
  /** Label for the idle pose. Default: "Save changes". */
  idleLabel?: React.ReactNode
  /** Label for the saved pose. Default: "Saved". */
  savedLabel?: React.ReactNode
}

export function SaveButton({
  onSave,
  idleLabel = "Save changes",
  savedLabel = "Saved",
  disabled,
  ...buttonProps
}: SaveButtonProps) {
  const [saved, setSaved] = React.useState(false)
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  React.useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleClick = React.useCallback(async () => {
    try {
      await onSave()
      setSaved(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setSaved(false), SAVED_DURATION_MS)
    } catch {
      // Caller surfaces the error elsewhere; we just stay in the idle pose.
    }
  }, [onSave])

  return (
    <Button {...buttonProps} disabled={disabled || saved} onClick={handleClick}>
      <span
        key={saved ? "check" : "save"}
        className="inline-flex animate-in zoom-in-50 duration-150"
      >
        {saved ? <Check aria-hidden="true" /> : <Save aria-hidden="true" />}
      </span>
      {saved ? savedLabel : idleLabel}
    </Button>
  )
}
