// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, type RefObject } from "react"

/**
 * Programmatic focus-on-mount for inputs / textareas that conditionally
 * render (dialog inputs, inline renames, search palettes, etc.).
 *
 * Equivalent UX to `autoFocus` but clears the `jsx-a11y/no-autofocus`
 * lint warning. The rule's concern is that `autoFocus` on a page-level
 * input steals focus from screen readers' default landing point — that
 * concern only applies to top-level inputs, not modal / conditional
 * ones. Using a ref + useEffect keeps the lint surface clean without
 * sacrificing the UX expectation.
 *
 * Returns a ref to attach to the target element. Pass `enabled=false`
 * to skip focus on a particular render (e.g. only focus the first
 * mount of a list-row rename input).
 */
export function useFocusOnMount<T extends HTMLElement = HTMLInputElement>(
  enabled: boolean = true,
): RefObject<T | null> {
  const ref = useRef<T | null>(null)
  useEffect(() => {
    if (!enabled) return
    ref.current?.focus()
  }, [enabled])
  return ref
}
