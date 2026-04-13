// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Crown,
  Check,
  X,
  Key,
  Loader2,
  Mail,
  ShieldCheck,
  ShieldOff,
} from "lucide-react"
import { MCP_BASE, mcpHeaders } from "@/lib/api"

interface ProSectionProps {
  featureTier: string
  featureFlags: Record<string, boolean>
  onRefresh?: () => void
}

interface BillingStatus {
  active: boolean
  tier: string
  source?: string
  activated_at?: number
  key_masked?: string
}

const PRO_FEATURES = [
  { key: "audio_transcription", label: "Audio Transcription", description: "Transcribe meeting notes and audio files via Whisper" },
  { key: "image_understanding", label: "Vision Analysis", description: "Analyze images and diagrams using LLM vision" },
  { key: "metamorphic_verification", label: "Metamorphic Verification", description: "Advanced claim verification with rephrased queries" },
  { key: "custom_smart_rag", label: "Custom Smart RAG", description: "Per-source weight tuning for retrieval orchestration" },
  { key: "advanced_analytics", label: "Advanced Analytics", description: "Extended observability metrics and quality scoring" },
] as const

const COMMUNITY_FEATURES = [
  { key: "ocr_parsing", label: "OCR Text Extraction", description: "Extract text from scanned PDFs and images" },
  { key: "semantic_dedup", label: "Semantic Deduplication", description: "Detect near-duplicate content using embeddings" },
] as const

const ENTERPRISE_FEATURES = [
  { key: "multi_user", label: "Multi-User Auth", description: "JWT-based multi-user with per-user API keys" },
  { key: "sso_saml", label: "SSO / SAML", description: "SAML 2.0 SP with IdP metadata import" },
  { key: "audit_logging", label: "Audit Logging", description: "Comprehensive audit trail for compliance" },
  { key: "priority_support", label: "Priority Support", description: "SLA-backed support and incident response" },
] as const

export function ProSection({ featureTier, featureFlags, onRefresh }: ProSectionProps) {
  const [billingStatus, setBillingStatus] = useState<BillingStatus | null>(null)
  const [licenseKey, setLicenseKey] = useState("")
  const [keyError, setKeyError] = useState("")
  const [keySuccess, setKeySuccess] = useState("")
  const [validating, setValidating] = useState(false)
  const [deactivating, setDeactivating] = useState(false)
  const [upgradeError, setUpgradeError] = useState("")

  // Waitlist state
  const [waitlistEmail, setWaitlistEmail] = useState("")
  const [waitlistSubmitting, setWaitlistSubmitting] = useState(false)
  const [waitlistResult, setWaitlistResult] = useState<{ status: string; position?: number } | null>(null)

  useEffect(() => {
    fetch(`${MCP_BASE}/billing/status`, { headers: mcpHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setBillingStatus(data) })
      .catch(() => {})
  }, [])

  const handleValidateKey = useCallback(async () => {
    if (!licenseKey.trim()) return
    setValidating(true)
    setKeyError("")
    setKeySuccess("")

    try {
      const res = await fetch(`${MCP_BASE}/billing/validate-key`, {
        method: "POST",
        headers: mcpHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ key: licenseKey.trim() }),
      })
      if (res.ok) {
        const data = await res.json()
        setKeySuccess(data.message || "License activated")
        setBillingStatus({ active: true, tier: "pro", source: "manual", key_masked: undefined })
        setLicenseKey("")
        onRefresh?.()
      } else {
        const err = await res.json().catch(() => ({ detail: "Validation failed" }))
        setKeyError(err.detail || "Invalid license key")
      }
    } catch {
      setKeyError("Failed to validate key")
    } finally {
      setValidating(false)
    }
  }, [licenseKey, onRefresh])

  const handleDeactivate = useCallback(async () => {
    setDeactivating(true)
    try {
      const res = await fetch(`${MCP_BASE}/billing/license`, {
        method: "DELETE",
        headers: mcpHeaders(),
      })
      if (res.ok) {
        setBillingStatus({ active: false, tier: "community", source: "default" })
        onRefresh?.()
      }
    } catch {
      // silent
    } finally {
      setDeactivating(false)
    }
  }, [onRefresh])

  const handleUpgrade = useCallback(async () => {
    setUpgradeError("")
    try {
      const res = await fetch(`${MCP_BASE}/billing/create-checkout`, {
        method: "POST",
        headers: mcpHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({}),
      })
      if (res.ok) {
        const data = await res.json()
        if (data.checkout_url) {
          window.open(data.checkout_url, "_blank")
        }
      } else {
        setUpgradeError("Pro upgrade is only available with a license key. Enter your key below.")
      }
    } catch {
      setUpgradeError("Pro upgrade is only available with a license key. Enter your key below.")
    }
  }, [])

  const handleWaitlist = useCallback(async () => {
    if (!waitlistEmail.trim()) return
    setWaitlistSubmitting(true)
    setWaitlistResult(null)
    try {
      const res = await fetch(`${MCP_BASE}/billing/waitlist`, {
        method: "POST",
        headers: mcpHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ email: waitlistEmail.trim() }),
      })
      if (res.ok) {
        const data = await res.json()
        setWaitlistResult({ status: "joined", position: data.position })
        setWaitlistEmail("")
      } else {
        setWaitlistResult({ status: "error" })
      }
    } catch {
      setWaitlistResult({ status: "error" })
    } finally {
      setWaitlistSubmitting(false)
    }
  }, [waitlistEmail])

  const isPro = featureTier === "pro" || featureTier === "enterprise"

  return (
    <div className="space-y-6">
      {/* Current Plan */}
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Crown className={isPro ? "h-5 w-5 text-amber-500" : "h-5 w-5 text-muted-foreground"} />
            <div>
              <h3 className="text-sm font-semibold">
                Current Plan:{" "}
                {featureTier === "enterprise" ? "Vault" : featureTier === "pro" ? "Pro" : "Core"}
              </h3>
              {billingStatus?.source && (
                <p className="text-xs text-muted-foreground">
                  Activated via {billingStatus.source}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {isPro ? (
              <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                <ShieldCheck className="mr-1 h-3 w-3" />
                Pro Active
              </Badge>
            ) : (
              <Button size="sm" onClick={handleUpgrade} className="bg-amber-600 hover:bg-amber-700">
                <Crown className="mr-1 h-3 w-3" />
                Upgrade to Pro
              </Button>
            )}
          </div>
        </div>

        {upgradeError && (
          <p className="mt-2 text-xs text-destructive">{upgradeError}</p>
        )}

        {/* Masked key display when Pro */}
        {isPro && billingStatus?.key_masked && (
          <div className="mt-3 flex items-center justify-between rounded border border-muted bg-muted/30 px-3 py-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Key className="h-3 w-3" />
              <span className="font-mono">{billingStatus.key_masked}</span>
            </div>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-xs text-muted-foreground hover:text-destructive"
              onClick={handleDeactivate}
              disabled={deactivating}
            >
              {deactivating ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <>
                  <ShieldOff className="mr-1 h-3 w-3" />
                  Deactivate
                </>
              )}
            </Button>
          </div>
        )}
      </div>

      {/* License Key Entry (Community only) */}
      {!isPro && (
        <div className="rounded-lg border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Key className="h-4 w-4" />
            License Key
          </div>
          <div className="flex gap-2">
            <Input
              type="text"
              placeholder="CERID-PRO-XXXX-XXXX-XXXX-XXXX-XXXX"
              value={licenseKey}
              onChange={(e) => setLicenseKey(e.target.value)}
              className="font-mono text-sm"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={handleValidateKey}
              disabled={validating || !licenseKey.trim()}
            >
              {validating ? <Loader2 className="h-3 w-3 animate-spin" /> : "Activate"}
            </Button>
          </div>
          {keyError && <p className="text-xs text-destructive">{keyError}</p>}
          {keySuccess && <p className="text-xs text-green-600 dark:text-green-400">{keySuccess}</p>}
        </div>
      )}

      {/* Waitlist (Community only, interim before Stripe) */}
      {!isPro && (
        <div className="rounded-lg border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Mail className="h-4 w-4" />
            Join the Pro Waitlist
          </div>
          <p className="text-xs text-muted-foreground">
            Get notified when Cerid Pro is available for purchase.
          </p>
          <div className="flex gap-2">
            <Input
              type="email"
              placeholder="you@example.com"
              value={waitlistEmail}
              onChange={(e) => setWaitlistEmail(e.target.value)}
              className="text-sm"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={handleWaitlist}
              disabled={waitlistSubmitting || !waitlistEmail.trim()}
            >
              {waitlistSubmitting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Join"}
            </Button>
          </div>
          {waitlistResult?.status === "joined" && (
            <p className="text-xs text-green-600 dark:text-green-400">
              You're on the list! Position #{waitlistResult.position}
            </p>
          )}
          {waitlistResult?.status === "error" && (
            <p className="text-xs text-destructive">Failed to join waitlist. Try again.</p>
          )}
        </div>
      )}

      {/* Pro Features */}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400">Pro</Badge>
          Features
        </h3>
        <div className="space-y-1">
          {PRO_FEATURES.map((f) => (
            <div key={f.key} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{f.label}</div>
                <div className="text-xs text-muted-foreground truncate">{f.description}</div>
              </div>
              <div className="ml-3 shrink-0">
                {featureFlags[f.key] ? (
                  <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                    <Check className="mr-1 h-3 w-3" />Enabled
                  </Badge>
                ) : (
                  <Badge variant="outline" className="border-muted-foreground/30 text-muted-foreground">
                    <X className="mr-1 h-3 w-3" />Locked
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Community Features (always enabled, shown for completeness) */}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Badge variant="outline" className="border-zinc-500/30 bg-zinc-500/10 text-zinc-600 dark:text-zinc-400">Core</Badge>
          Included in All Plans
        </h3>
        <div className="space-y-1">
          {COMMUNITY_FEATURES.map((f) => (
            <div key={f.key} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{f.label}</div>
                <div className="text-xs text-muted-foreground truncate">{f.description}</div>
              </div>
              <div className="ml-3 shrink-0">
                <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                  <Check className="mr-1 h-3 w-3" />Enabled
                </Badge>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Enterprise Features */}
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Badge variant="outline" className="border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400">Vault</Badge>
          Enterprise Features
        </h3>
        <div className="space-y-1">
          {ENTERPRISE_FEATURES.map((f) => (
            <div key={f.key} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{f.label}</div>
                <div className="text-xs text-muted-foreground truncate">{f.description}</div>
              </div>
              <div className="ml-3 shrink-0">
                {featureFlags[f.key] ? (
                  <Badge variant="outline" className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400">
                    <Check className="mr-1 h-3 w-3" />Enabled
                  </Badge>
                ) : (
                  <Badge variant="outline" className="border-muted-foreground/30 text-muted-foreground">
                    <X className="mr-1 h-3 w-3" />Locked
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
