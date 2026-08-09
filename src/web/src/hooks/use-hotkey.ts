// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useEffect, useRef } from "react"

/**
 * Lightweight global hotkey registration. Bind once per mounted
 * component; the hook removes the listener on unmount.
 *
 * The ``combo`` string follows the Cerid hotkey convention:
 *
 *   * ``"meta+shift+s"`` — Cmd+Shift+S on Mac, Ctrl+Shift+S elsewhere
 *   * ``"meta+1"``       — Cmd+1 on Mac, Ctrl+1 elsewhere
 *   * ``"?"``            — single key (Shift+/, matches `?`)
 *   * ``"escape"``       — Esc
 *
 * ``meta`` is the platform-appropriate primary modifier (Cmd on
 * Apple, Ctrl elsewhere). Use ``ctrl+...`` if you specifically
 * need Ctrl on Mac.
 *
 * The handler doesn't fire when an input / textarea / contenteditable
 * is focused, *unless* ``allowInInput=true`` is passed. This stops
 * single-letter shortcuts from breaking typing.
 */
interface HotkeyOptions {
  allowInInput?: boolean
  preventDefault?: boolean
  enabled?: boolean
}

export function useHotkey(
  combo: string,
  handler: (event: KeyboardEvent) => void,
  options: HotkeyOptions = {},
): void {
  const handlerRef = useRef(handler)
  // Sync handler on every render via effect to keep the React
  // Compiler purity rule satisfied (no ref writes during render).
  useEffect(() => {
    handlerRef.current = handler
  }, [handler])

  useEffect(() => {
    if (options.enabled === false) return
    const parsed = parseCombo(combo)
    const isMac = typeof navigator !== "undefined" && navigator.platform.startsWith("Mac")

    function onKeyDown(e: KeyboardEvent) {
      // Modifier check
      const meta = isMac ? e.metaKey : e.ctrlKey
      if (parsed.meta !== meta) return
      if (parsed.ctrl !== undefined && parsed.ctrl !== e.ctrlKey) return
      if (parsed.shift !== e.shiftKey) return
      if (parsed.alt !== e.altKey) return

      // Key check — case-insensitive on the letter / digit
      const key = e.key.toLowerCase()
      if (key !== parsed.key) return

      // Input-focus guard
      if (!options.allowInInput) {
        const target = e.target as HTMLElement | null
        if (target && isInteractive(target)) return
      }

      if (options.preventDefault !== false) e.preventDefault()
      handlerRef.current(e)
    }

    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [combo, options.allowInInput, options.preventDefault, options.enabled])
}

interface ParsedCombo {
  meta: boolean
  ctrl?: boolean
  shift: boolean
  alt: boolean
  key: string
}

function parseCombo(combo: string): ParsedCombo {
  const parts = combo.toLowerCase().split("+").map((p) => p.trim())
  const out: ParsedCombo = { meta: false, shift: false, alt: false, key: "" }
  for (const part of parts) {
    if (part === "meta" || part === "cmd") out.meta = true
    else if (part === "ctrl") out.ctrl = true
    else if (part === "shift") out.shift = true
    else if (part === "alt" || part === "opt" || part === "option") out.alt = true
    else out.key = part
  }
  return out
}

function isInteractive(el: HTMLElement): boolean {
  const tag = el.tagName.toLowerCase()
  if (tag === "input" || tag === "textarea" || tag === "select") return true
  if (el.isContentEditable) return true
  // Inside a CodeMirror / contentEditable child?
  const closest = el.closest('[contenteditable="true"], input, textarea')
  return closest !== null
}
