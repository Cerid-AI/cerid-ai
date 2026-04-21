// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { logSwallowedError } from "@/lib/log-swallowed"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { MessageSquare, Database, Settings, Brain, Shield, ChevronRight, ChevronLeft, Sparkles } from "lucide-react"
import { useUIMode } from "@/contexts/ui-mode-context"
import { cn } from "@/lib/utils"

const STEPS = [
  {
    title: "Welcome to Cerid AI",
    icon: Sparkles,
    content: (
      <div className="space-y-3 text-center">
        <p className="text-sm text-muted-foreground">
          Your privacy-first AI knowledge companion. Cerid connects your personal
          knowledge base to powerful language models — all data stays local.
        </p>
        <div className="mx-auto flex max-w-xs flex-col gap-2 text-left text-xs text-muted-foreground">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-brand">✦</span>
            <span>Chat with AI using your own documents as context</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-brand">✦</span>
            <span>Verify responses against your knowledge base</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-brand">✦</span>
            <span>All processing happens on your machine</span>
          </div>
        </div>
      </div>
    ),
  },
  {
    title: "Navigate with the Sidebar",
    icon: MessageSquare,
    content: (
      <div className="space-y-4">
        <p className="text-center text-sm text-muted-foreground">
          The sidebar gives you quick access to every part of Cerid.
        </p>
        <div className="mx-auto max-w-xs space-y-2.5">
          {[
            { icon: MessageSquare, label: "Chat", desc: "Converse with AI using your knowledge" },
            { icon: Database, label: "Knowledge", desc: "Browse and manage your documents" },
            { icon: Brain, label: "Memories", desc: "Facts Cerid remembers about you" },
            { icon: Settings, label: "Settings", desc: "Configure features and pipeline" },
          ].map(({ icon: Icon, label, desc }) => (
            <div key={label} className="flex items-center gap-3 rounded-lg border bg-card p-2.5">
              <Icon className="h-4 w-4 shrink-0 text-brand" />
              <div>
                <p className="text-sm font-medium">{label}</p>
                <p className="text-xs text-muted-foreground">{desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    ),
  },
  {
    title: "Chat & Knowledge",
    icon: Database,
    content: (
      <div className="space-y-4">
        <p className="text-center text-sm text-muted-foreground">
          Cerid automatically enriches your conversations with relevant documents.
        </p>
        <div className="mx-auto max-w-xs space-y-3">
          <div className="rounded-lg border bg-card p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Database className="h-4 w-4 text-brand" />
              KB Injection
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              When you ask a question, Cerid finds relevant documents and includes them as context
              for the AI — giving you answers grounded in your own data.
            </p>
          </div>
          <div className="rounded-lg border bg-card p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Shield className="h-4 w-4 text-brand" />
              Verification
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Enable response verification to check AI claims against your knowledge base.
              Flagged claims show inline markers you can click for details.
            </p>
          </div>
        </div>
      </div>
    ),
  },
]

interface OnboardingDialogProps {
  open: boolean
  onComplete: () => void
}

export function OnboardingDialog({ open, onComplete }: OnboardingDialogProps) {
  const [step, setStep] = useState(0)
  const { setMode } = useUIMode()
  const [selectedMode, setSelectedMode] = useState<"simple" | "advanced">("simple")

  const isLastStep = step === STEPS.length
  const totalSteps = STEPS.length + 1 // +1 for mode selection step

  const handleFinish = () => {
    setMode(selectedMode)
    try { localStorage.setItem("cerid-onboarding-complete", "true") } catch (err) { logSwallowedError(err, "localStorage.setItem", { key: "cerid-onboarding-complete" }) }
    onComplete()
  }

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent
        className="max-w-md gap-0 p-0 [&>button]:hidden"
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogTitle className="sr-only">Welcome to Cerid AI</DialogTitle>

        <div className="p-6">
          {step < STEPS.length ? (
            <>
              {/* Step content */}
              <div className="mb-2 flex items-center justify-center">
                {(() => {
                  const Icon = STEPS[step].icon
                  return (
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                      <Icon className="h-5 w-5 text-brand" />
                    </div>
                  )
                })()}
              </div>
              <h3 className="mb-4 text-center text-lg font-semibold">{STEPS[step].title}</h3>
              {STEPS[step].content}
            </>
          ) : (
            /* Mode selection step */
            <>
              <div className="mb-2 flex items-center justify-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
                  <Sparkles className="h-5 w-5 text-brand" />
                </div>
              </div>
              <h3 className="mb-2 text-center text-lg font-semibold">Choose Your Mode</h3>
              <p className="mb-4 text-center text-sm text-muted-foreground">
                You can change this anytime from the sidebar.
              </p>
              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => setSelectedMode("simple")}
                  className={cn(
                    "w-full rounded-lg border p-3 text-left transition-colors",
                    selectedMode === "simple"
                      ? "border-brand bg-brand/5"
                      : "border-muted hover:border-muted-foreground/30",
                  )}
                >
                  <p className="text-sm font-medium">☕ Simple</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Clean chat interface — KB toggles, verification, and analytics hidden.
                    Perfect for everyday use.
                  </p>
                </button>
                <button
                  type="button"
                  onClick={() => setSelectedMode("advanced")}
                  className={cn(
                    "w-full rounded-lg border p-3 text-left transition-colors",
                    selectedMode === "advanced"
                      ? "border-brand bg-brand/5"
                      : "border-muted hover:border-muted-foreground/30",
                  )}
                >
                  <p className="text-sm font-medium">🔧 Advanced</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Full control — KB panel, verification, smart routing, feedback loop,
                    and all pipeline settings visible.
                  </p>
                </button>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t px-6 py-3">
          {/* Step dots */}
          <div className="flex gap-1.5">
            {Array.from({ length: totalSteps }, (_, i) => (
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
            {step > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setStep(step - 1)}>
                <ChevronLeft className="mr-1 h-3 w-3" />
                Back
              </Button>
            )}
            {isLastStep ? (
              <Button size="sm" onClick={handleFinish} className="bg-brand text-brand-foreground hover:bg-brand/90">
                Get Started
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            ) : (
              <Button size="sm" onClick={() => setStep(step + 1)}>
                Next
                <ChevronRight className="ml-1 h-3 w-3" />
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
