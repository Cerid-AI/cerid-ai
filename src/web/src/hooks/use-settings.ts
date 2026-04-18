// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useEffect, useRef } from "react"
import { fetchSettings, updateSettings, syncPreferences, fetchUserState, fetchPrivateMode, enablePrivateMode, disablePrivateMode } from "@/lib/api"
import type { RagMode, RoutingMode, SettingsUpdate } from "@/lib/types"

function readBool(key: string): boolean {
  try { return localStorage.getItem(key) === "true" } catch { return false }
}

function readFloat(key: string, fallback: number): number {
  try {
    const v = localStorage.getItem(key)
    if (v !== null) { const n = parseFloat(v); if (!isNaN(n)) return n }
  } catch { /* noop */ }
  return fallback
}

function persist(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* noop */ }
}

// ── Version-vector reconciliation (audit F-7) ───────────────────────────────
// The server and each machine stamp `updatedAt` on every settings/preferences
// write.  On hydrate we compare the local stamp against the server stamp and
// pick the newer record; without this, any local write permanently shadows
// changes from other machines.

const SETTINGS_UPDATED_AT_KEY = "cerid-settings-updated-at"

/** Read local settings revision as epoch ms. Returns 0 if unset. */
function readSettingsUpdatedAt(): number {
  try {
    const v = localStorage.getItem(SETTINGS_UPDATED_AT_KEY)
    if (v !== null) {
      const n = parseInt(v, 10)
      if (!isNaN(n) && n > 0) return n
    }
  } catch { /* noop */ }
  return 0
}

/** Bump the local settings revision to now (call after any local write). */
function bumpSettingsUpdatedAt(): void {
  persist(SETTINGS_UPDATED_AT_KEY, String(Date.now()))
}

/** Parse an ISO-8601 or epoch-ms timestamp to epoch ms, 0 on failure. */
function parseServerUpdatedAt(v: unknown): number {
  if (typeof v === "number" && v > 0) return v
  if (typeof v === "string" && v) {
    const n = Date.parse(v)
    if (!isNaN(n)) return n
  }
  return 0
}

/** Boolean setting with localStorage persistence + server sync. */
function useSyncedToggle(
  localKey: string,
  serverKey: keyof SettingsUpdate,
): [boolean, () => void, (v: boolean, force?: boolean) => void] {
  const [value, setValue] = useState(() => readBool(localKey))

  const toggle = useCallback(() => {
    setValue((prev) => {
      const next = !prev
      persist(localKey, String(next))
      bumpSettingsUpdatedAt()
      updateSettings({ [serverKey]: next }).catch(() => { /* noop */ })
      return next
    })
  }, [localKey, serverKey])

  /**
   * Accept value from server hydration.
   *
   * First-time setup (no local value yet): always accept.
   *
   * Audit F-7: when `force=true`, caller has determined the server record is
   * strictly newer than the local one via `updated_at` comparison, so we
   * override the write-once guard and replace the local value.  Without this
   * override, the first local write permanently shadowed cross-machine
   * updates.
   */
  const hydrate = useCallback((v: boolean, force: boolean = false) => {
    try {
      if (!force && localStorage.getItem(localKey) !== null) return
    } catch { /* noop */ }
    setValue(v)
    persist(localKey, String(v))
  }, [localKey])

  return [value, toggle, hydrate]
}

export function useSettings() {
  const [feedbackLoop, toggleFeedbackLoop, hydrateFeedback] = useSyncedToggle(
    "cerid-feedback-loop", "enable_feedback_loop",
  )
  const [autoInject, toggleAutoInject, hydrateAutoInject] = useSyncedToggle(
    "cerid-auto-inject", "enable_auto_inject",
  )
  const [hallucinationEnabled, toggleHallucinationEnabled, hydrateHallucination] = useSyncedToggle(
    "cerid-hallucination-check", "enable_hallucination_check",
  )
  const [memoryExtraction, toggleMemoryExtraction, hydrateMemory] = useSyncedToggle(
    "cerid-memory-extraction", "enable_memory_extraction",
  )

  const [showDashboard, setShowDashboard] = useState(() => readBool("cerid-show-dashboard"))
  const [routingMode, setRoutingModeState] = useState<RoutingMode>(() => {
    try {
      const v = localStorage.getItem("cerid-routing-mode")
      if (v === "manual" || v === "recommend" || v === "auto") return v
      const old = localStorage.getItem("cerid-auto-model-switch")
      if (old === "true") return "recommend"
    } catch { /* noop */ }
    return "manual"
  })
  const [autoInjectThreshold, setAutoInjectThresholdState] = useState(() => readFloat("cerid-auto-inject-threshold", 0.55))

  const [inlineMarkups, setInlineMarkupsState] = useState(() => {
    try { const v = localStorage.getItem("cerid-inline-markups"); return v === null ? true : v === "true" } catch { return true }
  })
  const toggleInlineMarkups = useCallback(() => {
    setInlineMarkupsState((prev) => {
      const next = !prev
      persist("cerid-inline-markups", String(next))
      bumpSettingsUpdatedAt()
      syncPreferences({ inline_markups: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  const [expertVerification, setExpertVerificationState] = useState(() => readBool("cerid-expert-verification"))
  const toggleExpertVerification = useCallback(() => {
    setExpertVerificationState((prev) => {
      const next = !prev
      persist("cerid-expert-verification", String(next))
      bumpSettingsUpdatedAt()
      syncPreferences({ expert_verification: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  const [ragMode, setRagModeState] = useState<RagMode>(() => {
    try {
      const v = localStorage.getItem("cerid-rag-mode")
      if (v === "manual" || v === "smart" || v === "custom_smart") return v
    } catch { /* noop */ }
    return "manual"
  })

  const [costSensitivity, setCostSensitivity] = useState<"low" | "medium" | "high">(() => {
    try {
      const v = localStorage.getItem("cerid-cost-sensitivity")
      return v === "low" || v === "medium" || v === "high" ? v : "medium"
    } catch { return "medium" }
  })

  // Private Mode state
  const [privateModeEnabled, setPrivateModeEnabled] = useState(() => readBool("cerid-private-mode"))
  const [privateModeLevel, setPrivateModeLevel] = useState(() => {
    try {
      const v = localStorage.getItem("cerid-private-mode-level")
      if (v !== null) { const n = parseInt(v, 10); if (!isNaN(n) && n >= 0 && n <= 4) return n }
    } catch { /* noop */ }
    return 0
  })

  // Hydrate from server on mount (non-blocking, localStorage is immediate fallback)
  const hydratedRef = useRef(false)
  useEffect(() => {
    if (hydratedRef.current) return
    hydratedRef.current = true

    // Fetch runtime settings + full user-state in parallel. We need both
    // because `fetchSettings()` surfaces the live server values and
    // `fetchUserState().settings.updated_at` is the cross-machine revision
    // stamp used for version-vector reconciliation (audit F-7).
    Promise.all([
      fetchSettings().catch(() => null),
      fetchUserState().catch(() => null),
    ]).then(([s, state]) => {
      // ── Version-vector reconciliation ────────────────────────────────────
      // Compare local `cerid-settings-updated-at` against server
      // `settings.updated_at`.  If server is strictly newer, force-replace
      // local values (normally the per-key hydrate skips when localStorage
      // already has a value).  If local is strictly newer, push current
      // local toggle + scalar state back to the server.
      const serverStamp = parseServerUpdatedAt(state?.settings?.updated_at)
      const localStamp = readSettingsUpdatedAt()
      const serverWins = serverStamp > 0 && serverStamp > localStamp
      const localWins = localStamp > 0 && localStamp > serverStamp

      if (s) {
        if (s.enable_feedback_loop !== undefined) hydrateFeedback(s.enable_feedback_loop, serverWins)
        if (s.cost_sensitivity) {
          const v = s.cost_sensitivity as "low" | "medium" | "high"
          if (v === "low" || v === "medium" || v === "high") {
            if (serverWins || !localStorage.getItem("cerid-cost-sensitivity")) {
              setCostSensitivity(v)
              persist("cerid-cost-sensitivity", v)
            }
          }
        }
        if (s.enable_auto_inject !== undefined) hydrateAutoInject(s.enable_auto_inject, serverWins)
        if (s.auto_inject_threshold !== undefined) {
          if (serverWins || !localStorage.getItem("cerid-auto-inject-threshold")) {
            setAutoInjectThresholdState(s.auto_inject_threshold)
            persist("cerid-auto-inject-threshold", String(s.auto_inject_threshold))
          }
        }
        if (s.enable_hallucination_check !== undefined) hydrateHallucination(s.enable_hallucination_check, serverWins)
        if (s.enable_memory_extraction !== undefined) hydrateMemory(s.enable_memory_extraction, serverWins)
        if (s.rag_mode && (serverWins || !localStorage.getItem("cerid-rag-mode"))) {
          const rm = s.rag_mode as string
          if (rm === "manual" || rm === "smart" || rm === "custom_smart") {
            setRagModeState(rm as RagMode)
            persist("cerid-rag-mode", rm)
          }
        }
        if (s.enable_model_router !== undefined) {
          const current = localStorage.getItem("cerid-routing-mode")
          // Preserve explicit "auto" unless server is authoritatively newer.
          if (serverWins || current !== "auto") {
            const mode: RoutingMode = s.enable_model_router ? "recommend" : "manual"
            setRoutingModeState(mode)
            persist("cerid-routing-mode", mode)
          }
        }

        if (serverWins) {
          // We replaced local state with server state — adopt the server
          // revision stamp so the next hydrate is a clean no-op.
          persist(SETTINGS_UPDATED_AT_KEY, String(serverStamp))
        } else if (localWins) {
          // Push the divergent local toggle state to the server. We don't
          // need to push scalar setters (threshold/rag_mode/etc.) because
          // the write-path already bumps the timestamp — if they disagree
          // a follow-up setter call will reconcile naturally.
          const reconcile: SettingsUpdate = {}
          const pushBool = (
            localKey: string,
            serverVal: boolean | undefined,
            assign: (r: SettingsUpdate, v: boolean) => void,
          ) => {
            try {
              const stored = localStorage.getItem(localKey)
              if (stored !== null && (stored === "true") !== serverVal) {
                assign(reconcile, stored === "true")
              }
            } catch { /* noop */ }
          }
          pushBool("cerid-feedback-loop", s.enable_feedback_loop, (r, v) => { r.enable_feedback_loop = v })
          pushBool("cerid-auto-inject", s.enable_auto_inject, (r, v) => { r.enable_auto_inject = v })
          pushBool("cerid-hallucination-check", s.enable_hallucination_check, (r, v) => { r.enable_hallucination_check = v })
          pushBool("cerid-memory-extraction", s.enable_memory_extraction, (r, v) => { r.enable_memory_extraction = v })
          if (Object.keys(reconcile).length > 0) {
            updateSettings(reconcile).catch(() => { /* best-effort */ })
          }
        }
      }

      // ── UI preferences (part of the same reconciliation window) ────────
      // The server preferences file shares the same `updated_at` semantics as
      // settings.json — re-use `serverWins` so a stale local machine can't
      // shadow cross-machine updates to routing_mode / expert_verification /
      // inline_markups.
      const p = (state?.preferences ?? {}) as Record<string, unknown>
      if (p.routing_mode && (serverWins || !localStorage.getItem("cerid-routing-mode"))) {
        const m = p.routing_mode as string
        if (m === "manual" || m === "recommend" || m === "auto") {
          setRoutingModeState(m as RoutingMode)
          persist("cerid-routing-mode", m)
        }
      }
      if (p.expert_verification !== undefined && (serverWins || localStorage.getItem("cerid-expert-verification") === null)) {
        const v = Boolean(p.expert_verification)
        setExpertVerificationState(v)
        persist("cerid-expert-verification", String(v))
      }
      if (p.inline_markups !== undefined && (serverWins || localStorage.getItem("cerid-inline-markups") === null)) {
        const v = Boolean(p.inline_markups)
        setInlineMarkupsState(v)
        persist("cerid-inline-markups", String(v))
      }
    }).catch(() => { /* Server unavailable — use localStorage values */ })

    // Hydrate private mode from server
    fetchPrivateMode()
      .then((pm) => {
        if (localStorage.getItem("cerid-private-mode") === null) {
          setPrivateModeEnabled(pm.enabled)
          persist("cerid-private-mode", String(pm.enabled))
        }
        if (localStorage.getItem("cerid-private-mode-level") === null) {
          setPrivateModeLevel(pm.level)
          persist("cerid-private-mode-level", String(pm.level))
        }
      })
      .catch(() => { /* Server unavailable — use localStorage values */ })
  }, [hydrateFeedback, hydrateAutoInject, hydrateHallucination, hydrateMemory])

  const toggleDashboard = useCallback(() => {
    setShowDashboard((prev) => {
      const next = !prev
      persist("cerid-show-dashboard", String(next))
      return next
    })
  }, [])

  const setRoutingMode = useCallback((mode: RoutingMode) => {
    setRoutingModeState(mode)
    persist("cerid-routing-mode", mode)
    bumpSettingsUpdatedAt()
    updateSettings({ enable_model_router: mode !== "manual" }).catch(() => { /* noop */ })
    syncPreferences({ routing_mode: mode }).catch(() => { /* fire-and-forget */ })
  }, [])

  const cycleRoutingMode = useCallback(() => {
    setRoutingModeState((prev) => {
      const next: RoutingMode = prev === "manual" ? "recommend" : prev === "recommend" ? "auto" : "manual"
      persist("cerid-routing-mode", next)
      bumpSettingsUpdatedAt()
      updateSettings({ enable_model_router: next !== "manual" }).catch(() => { /* noop */ })
      syncPreferences({ routing_mode: next }).catch(() => { /* fire-and-forget */ })
      return next
    })
  }, [])

  const setAutoInjectThreshold = useCallback((value: number) => {
    setAutoInjectThresholdState(value)
    persist("cerid-auto-inject-threshold", String(value))
    bumpSettingsUpdatedAt()
    updateSettings({ auto_inject_threshold: value }).catch(() => { /* noop */ })
  }, [])

  const setRagMode = useCallback((mode: RagMode) => {
    setRagModeState(mode)
    persist("cerid-rag-mode", mode)
    bumpSettingsUpdatedAt()
    updateSettings({ rag_mode: mode }).catch(() => { /* noop */ })
  }, [])

  const updateCostSensitivity = useCallback((value: "low" | "medium" | "high") => {
    setCostSensitivity(value)
    persist("cerid-cost-sensitivity", value)
    bumpSettingsUpdatedAt()
    updateSettings({ cost_sensitivity: value }).catch(() => { /* noop */ })
  }, [])

  const togglePrivateMode = useCallback(() => {
    setPrivateModeEnabled((prev) => {
      const next = !prev
      if (next) {
        const level = 1
        setPrivateModeLevel(level)
        persist("cerid-private-mode", "true")
        persist("cerid-private-mode-level", String(level))
        enablePrivateMode(level).catch(() => { /* noop */ })
      } else {
        setPrivateModeLevel(0)
        persist("cerid-private-mode", "false")
        persist("cerid-private-mode-level", "0")
        disablePrivateMode(false).catch(() => { /* noop */ })
      }
      return next
    })
  }, [])

  const changePrivateModeLevel = useCallback((level: number) => {
    setPrivateModeLevel(level)
    setPrivateModeEnabled(level > 0)
    persist("cerid-private-mode", String(level > 0))
    persist("cerid-private-mode-level", String(level))
    if (level > 0) {
      enablePrivateMode(level).catch(() => { /* noop */ })
    } else {
      disablePrivateMode(false).catch(() => { /* noop */ })
    }
  }, [])

  return {
    feedbackLoop, toggleFeedbackLoop,
    showDashboard, toggleDashboard,
    ragMode, setRagMode,
    routingMode, setRoutingMode, cycleRoutingMode,
    autoInject, toggleAutoInject,
    autoInjectThreshold, setAutoInjectThreshold,
    costSensitivity, updateCostSensitivity,
    hallucinationEnabled, toggleHallucinationEnabled,
    memoryExtraction, toggleMemoryExtraction,
    inlineMarkups, toggleInlineMarkups,
    expertVerification, toggleExpertVerification,
    privateModeEnabled, privateModeLevel, togglePrivateMode, changePrivateModeLevel,
  }
}
