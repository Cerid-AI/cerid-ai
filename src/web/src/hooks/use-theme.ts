// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Appearance store — theme triad (light / dark / system), density, and
 * reduced-motion override. Module-level so every consumer (App init,
 * sidebar theme button, Settings → Appearance) reads and writes the SAME
 * state: the sidebar button is a declared mirror of the Appearance rows
 * (registry `mirrors: ["sidebar-footer"]`).
 *
 * `applyPersistedAppearance()` is called from `main.tsx` before the first
 * React render so the persisted theme/density/motion attributes hit
 * `<html>` before first paint (FOUC guard).
 */

import { useCallback, useEffect, useSyncExternalStore } from "react"
import type { Theme } from "@/lib/types"
import { logSwallowedError } from "@/lib/log-swallowed"

export type ThemePreference = "light" | "dark" | "system"
export type Density = "comfortable" | "compact"
export type MotionPreference = "system" | "reduce"

const THEME_KEY = "cerid-theme"
const DENSITY_KEY = "cerid-density"
const MOTION_KEY = "cerid-motion"

const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch (err) {
    logSwallowedError(err, "localStorage.setItem", { key })
  }
}

export function getThemePreference(): ThemePreference {
  const stored = readStorage(THEME_KEY)
  return stored === "light" || stored === "dark" || stored === "system" ? stored : "system"
}

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
  } catch {
    return true
  }
}

export function getResolvedTheme(): Theme {
  const pref = getThemePreference()
  if (pref === "light" || pref === "dark") return pref
  return systemPrefersDark() ? "dark" : "light"
}

export function getDensity(): Density {
  return readStorage(DENSITY_KEY) === "compact" ? "compact" : "comfortable"
}

export function getMotionPreference(): MotionPreference {
  return readStorage(MOTION_KEY) === "reduce" ? "reduce" : "system"
}

/** Apply all three appearance attributes to <html>. Safe to call repeatedly. */
export function applyPersistedAppearance() {
  const root = document.documentElement
  root.classList.toggle("dark", getResolvedTheme() === "dark")
  root.setAttribute("data-density", getDensity())
  root.setAttribute("data-motion", getMotionPreference())
}

export function setThemePreference(pref: ThemePreference) {
  writeStorage(THEME_KEY, pref)
  applyPersistedAppearance()
  emit()
}

export function setDensity(density: Density) {
  writeStorage(DENSITY_KEY, density)
  applyPersistedAppearance()
  emit()
}

export function setMotionPreference(motion: MotionPreference) {
  writeStorage(MOTION_KEY, motion)
  applyPersistedAppearance()
  emit()
}

// Snapshot strings are value-equal across calls when nothing changed, so
// useSyncExternalStore's Object.is comparison short-circuits correctly.
function themeSnapshot(): string {
  return `${getThemePreference()}|${getResolvedTheme()}`
}

export function useTheme() {
  const snapshot = useSyncExternalStore(subscribe, themeSnapshot, themeSnapshot)
  const [preference, resolved] = snapshot.split("|") as [ThemePreference, Theme]
  const density = useSyncExternalStore(subscribe, getDensity, getDensity)
  const motion = useSyncExternalStore(subscribe, getMotionPreference, getMotionPreference)

  // Keep <html> in sync on mount and track live OS theme changes while the
  // preference is "system".
  useEffect(() => {
    applyPersistedAppearance()
    let mq: MediaQueryList | null = null
    const onChange = () => {
      applyPersistedAppearance()
      emit()
    }
    try {
      mq = window.matchMedia("(prefers-color-scheme: dark)")
      mq.addEventListener("change", onChange)
    } catch {
      mq = null
    }
    return () => {
      mq?.removeEventListener("change", onChange)
    }
  }, [])

  const toggleTheme = useCallback(() => {
    setThemePreference(getResolvedTheme() === "dark" ? "light" : "dark")
  }, [])

  return {
    /** Resolved theme actually in effect ("light" | "dark"). */
    theme: resolved,
    /** Stored preference, including "system". */
    preference,
    setPreference: setThemePreference,
    toggleTheme,
    density,
    setDensity,
    motion,
    setMotion: setMotionPreference,
  }
}
