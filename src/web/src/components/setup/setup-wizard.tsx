// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Sparkles, Key, CheckCircle2, Activity, ChevronRight, ChevronLeft, Loader2, Check } from "lucide-react"
import { cn } from "@/lib/utils"
import { ApiKeyInput } from "@/components/setup/api-key-input"
import { HealthDashboard } from "@/components/setup/health-dashboard"
import { applySetupConfig, fetchProviderCredits } from "@/lib/api"
import type { ProviderCredits } from "@/lib/types"

interface SetupWizardProps {
  open: boolean
  onComplete: () => void
}

interface ProviderKey {
  key: string
  valid: boolean
}

const STEP_TRANSITION_MS = 800
const TOTAL_STEPS = 4

export function SetupWizard({ open, onComplete }: SetupWizardProps) {
  const [step, setStep] = useState(0)
  const [keys, setKeys] = useState<Record<string, ProviderKey>>({
    openrouter: { key: "", valid: false },
    openai: { key: "", valid: false },
    anthropic: { key: "", valid: false },
  })
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applied, setApplied] = useState(false)
  const [allHealthy, setAllHealthy] = useState(false)
  const [credits, setCredits] = useState<ProviderCredits | null>(null)

  const handleKeyValidated = useCallback((provider: string) => (key: string, valid: boolean) => {
    setKeys((prev) => ({ ...prev, [provider]: { key, valid } }))
    // After OpenRouter key is validated, fetch credit balance
    if (provider === "openrouter" && valid) {
      fetchProviderCredits().then(setCredits).catch(() => {})
    }
  }, [])

  const canProceedFromKeys = keys.openrouter.valid

  const handleApply = useCallback(async () => {
    setApplying(true)
    setApplyError(null)
    try {
      const config: Record<string, string> = {}
      for (const [provider, { key, valid }] of Object.entries(keys)) {
        if (valid && key) config[provider] = key
      }
      const result = await applySetupConfig({ keys: config })
      if (result.success) {
        setApplied(true)
        // Auto-advance to health step after a short delay
        setTimeout(() => setStep(3), STEP_TRANSITION_MS)
      } else {
        setApplyError("Configuration failed — check backend logs")
      }
    } catch {
      setApplyError("Connection failed — is the backend running?")
    } finally {
      setApplying(false)
    }
  }, [keys])

  const handleFinish = useCallback(() => {
    onComplete()
  }, [onComplete])

  const handleAllHealthy = useCallback(() => {
    setAllHealthy(true)
  }, [])

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent
        className="max-w-lg gap-0 p-0 [&>button]:hidden"
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogTitle className="sr-only">Cerid AI Setup</DialogTitle>

        <div className="p-6">
          {/* Step 0: Welcome */}
          {step === 0 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Sparkles className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-2 text-center text-lg font-semibold">
                Let&apos;s get you set up
              </h3>
              <div className="space-y-3 text-center">
                <p className="text-sm text-muted-foreground">
                  Cerid needs an LLM provider API key to work. You&apos;ll need at minimum
                  an OpenRouter account — it gives you access to all major models through
                  a single key.
                </p>
                <div className="mx-auto flex max-w-xs flex-col gap-2 text-left text-xs text-muted-foreground">
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 text-brand">✦</span>
                    <span>OpenRouter key is required (routes to 100+ models)</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 text-brand">✦</span>
                    <span>OpenAI and Anthropic keys are optional direct providers</span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 text-brand">✦</span>
                    <span>Keys are stored locally and never leave your machine</span>
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Step 1: API Keys */}
          {step === 1 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Key className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">
                API Keys
              </h3>
              <div className="space-y-4">
                <ApiKeyInput
                  provider="openrouter"
                  label="OpenRouter API Key"
                  required
                  helpUrl="https://openrouter.ai/keys"
                  onKeyValidated={handleKeyValidated("openrouter")}
                />
                {!keys.openrouter.valid && (
                  <div className="rounded-lg border bg-muted/30 px-3 py-2.5">
                    <p className="mb-1 text-xs font-medium text-muted-foreground">
                      Don&apos;t have an OpenRouter account?
                    </p>
                    <ol className="ml-4 list-decimal space-y-0.5 text-xs text-muted-foreground">
                      <li>
                        <a href="https://openrouter.ai/auth" target="_blank" rel="noopener noreferrer" className="text-brand underline hover:text-brand/80">
                          Create account at openrouter.ai
                        </a>
                      </li>
                      <li>Add credits ($5 minimum recommended)</li>
                      <li>Copy your API key from the dashboard</li>
                    </ol>
                  </div>
                )}
                {keys.openrouter.valid && credits?.configured && credits.balance != null && (
                  <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-3 py-2">
                    <span className="text-xs text-green-600 dark:text-green-400">
                      OpenRouter balance
                    </span>
                    <span className="text-sm font-semibold tabular-nums text-green-600 dark:text-green-400">
                      ${credits.balance.toFixed(2)}
                    </span>
                  </div>
                )}
                <ApiKeyInput
                  provider="openai"
                  label="OpenAI API Key"
                  helpUrl="https://platform.openai.com/api-keys"
                  onKeyValidated={handleKeyValidated("openai")}
                />
                <ApiKeyInput
                  provider="anthropic"
                  label="Anthropic API Key"
                  helpUrl="https://console.anthropic.com/settings/keys"
                  onKeyValidated={handleKeyValidated("anthropic")}
                />
              </div>
            </>
          )}

          {/* Step 2: Review & Apply */}
          {step === 2 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <CheckCircle2 className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">
                Review &amp; Apply
              </h3>
              <div className="space-y-3">
                <p className="text-center text-sm text-muted-foreground">
                  The following providers will be configured:
                </p>
                <div className="space-y-1.5">
                  {Object.entries(keys).map(([provider, { valid }]) => (
                    <div
                      key={provider}
                      className="flex items-center justify-between rounded-lg border bg-card px-3 py-2"
                    >
                      <span className="text-sm font-medium capitalize">{provider}</span>
                      {valid ? (
                        <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                          <Check className="h-3 w-3" />
                          Ready
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">Not configured</span>
                      )}
                    </div>
                  ))}
                </div>

                {!applied && (
                  <Button
                    className="w-full bg-brand text-brand-foreground hover:bg-brand/90"
                    onClick={handleApply}
                    disabled={applying || !canProceedFromKeys}
                  >
                    {applying ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Applying Configuration...
                      </>
                    ) : (
                      "Apply Configuration"
                    )}
                  </Button>
                )}

                {applied && (
                  <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-3 text-center text-sm text-green-600 dark:text-green-400">
                    Configuration applied successfully
                  </div>
                )}

                {applyError && (
                  <p className="text-center text-xs text-destructive">{applyError}</p>
                )}
              </div>
            </>
          )}

          {/* Step 3: Health Monitor */}
          {step === 3 && (
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Activity className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">
                Service Health
              </h3>
              <HealthDashboard
                polling
                interval={2000}
                onAllHealthy={handleAllHealthy}
              />
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t px-6 py-3">
          {/* Step dots */}
          <div className="flex gap-1.5">
            {Array.from({ length: TOTAL_STEPS }, (_, i) => (
              <div
                key={i}
                className={cn(
                  "h-1.5 w-1.5 rounded-full transition-colors",
                  i === step ? "bg-brand" : "bg-muted-foreground/30",
                )}
              />
            ))}
          </div>

          <div className="flex gap-2">
            {step > 0 && step < 3 && (
              <Button variant="ghost" size="sm" onClick={() => setStep(step - 1)}>
                <ChevronLeft className="mr-1 h-3 w-3" />
                Back
              </Button>
            )}

            {step === 0 && (
              <Button size="sm" onClick={() => setStep(1)}>
                Get Started
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            )}

            {step === 1 && (
              <Button
                size="sm"
                onClick={() => setStep(2)}
                disabled={!canProceedFromKeys}
              >
                Next
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            )}

            {step === 2 && applied && (
              <Button size="sm" onClick={() => setStep(3)}>
                Next
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            )}

            {step === 3 && (
              <Button
                size="sm"
                onClick={handleFinish}
                disabled={!allHealthy}
                className="bg-brand text-brand-foreground hover:bg-brand/90"
              >
                Open Cerid AI
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
