import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Pricing — Cerid AI",
  description: "Three tiers — Cerid Core (free, open source), Cerid Pro (commercial plugins), and Cerid Vault (enterprise with SSO, audit, and SLA).",
}

import { Check, ArrowRight } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { BrandShield } from "@/components/brand-shield"

const PLANS = [
  {
    name: "Cerid Core",
    variant: "core" as const,
    tierImage: "/core-icon.jpg",
    price: "Free",
    period: "forever",
    description: "Smart. Extensible. Private.",
    badge: null,
    accent: "",
    cta: "Get Started",
    ctaHref: "https://github.com/Cerid-AI/cerid-ai",
    ctaStyle: "border border-border bg-background hover:bg-accent hover:text-accent-foreground",
    features: [
      "10 AI agents, 21 MCP tools",
      "Unified RAG (Manual + Smart modes)",
      "Hybrid BM25 + vector search",
      "Per-chunk retrieval profiles",
      "Streaming verification (4 claim types)",
      "6-type memory layer with salience scoring",
      "Ollama local LLM ($0 pipeline costs)",
      "Cross-encoder reranking (ONNX)",
      "Bulk folder import with preview",
      "Multi-machine sync via Dropbox",
      "Simple / Advanced UI mode",
      "Community support",
    ],
  },
  {
    name: "Cerid Pro",
    variant: "pro" as const,
    tierImage: "/pro-wordmark.jpg",
    price: "Paid",
    period: "per seat",
    description: "Smart. Secure. Fully Controlled.",
    badge: "Coming Soon",
    accent: "border-brand/40",
    cta: "Star on GitHub",
    ctaHref: "https://github.com/Cerid-AI/cerid-ai",
    ctaStyle: "bg-brand text-brand-foreground hover:bg-brand/90",
    features: [
      "Everything in Core",
      "Custom Smart RAG (per-source weights)",
      "OCR, audio transcription, vision plugins",
      "Metamorphic verification",
      "Advanced analytics dashboard",
      "Semantic deduplication",
      "Visual workflow builder",
    ],
  },
  {
    name: "Cerid Vault",
    variant: "vault" as const,
    tierImage: "/shield-3d.jpg",
    price: "Contact",
    period: "enterprise",
    description: "Secure by Design. Mission Assured.",
    badge: "Enterprise",
    accent: "border-gold",
    cta: "Contact Sales",
    ctaHref: "mailto:vault@cerid.ai",
    ctaStyle: "bg-gold/10 text-gold border border-gold hover:bg-gold/20",
    features: [
      "Everything in Pro",
      "Multi-user JWT auth + tenant isolation",
      "SSO / SAML integration (planned)",
      "Enterprise audit logging",
      "SLA & priority support",
      "Custom deployment assistance",
    ],
  },
]

export default function PricingPage() {
  return (
    <>
      {/* Hero */}
      <section className="bg-circuit py-24 border-b divider-gold">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">Pricing</h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
            Start free with the full-featured Core edition.
            Scale to Pro for commercial plugins or Vault for enterprise compliance.
          </p>
        </div>
      </section>

      {/* Plans */}
      <section className="py-20">
        <div className="mx-auto max-w-5xl px-6">
          <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
            {PLANS.map((plan) => (
              <Card
                key={plan.name}
                className={`relative flex flex-col overflow-visible border-border ${plan.accent}`}
              >
                {plan.badge && (
                  <div className="absolute -top-3 right-4 z-10">
                    <Badge className={plan.variant === "vault" ? "bg-gold/20 text-gold border border-gold shadow-md" : "bg-brand/20 text-brand border border-brand/30 shadow-md"}>
                      {plan.badge}
                    </Badge>
                  </div>
                )}
                <CardHeader className="text-center">
                  <div className="mx-auto mb-4">
                    {plan.tierImage ? (
                      <img src={plan.tierImage} alt={plan.name} className="mx-auto w-4/5 rounded-xl object-cover" />
                    ) : (
                      <BrandShield variant={plan.variant} size={44} />
                    )}
                  </div>
                  <CardTitle className="text-xl">{plan.name}</CardTitle>
                  <div className="mt-3">
                    <span className="text-3xl font-bold">{plan.price}</span>
                    <span className="ml-2 text-sm text-muted-foreground">{plan.period}</span>
                  </div>
                  <CardDescription className="mt-2">{plan.description}</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-1 flex-col">
                  <Link
                    href={plan.ctaHref}
                    target={plan.ctaHref.startsWith("http") ? "_blank" : undefined}
                    rel={plan.ctaHref.startsWith("http") ? "noopener noreferrer" : undefined}
                    className={`inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg text-sm font-medium shadow-sm transition-all ${plan.ctaStyle}`}
                  >
                    {plan.cta}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>

                  <ul className="mt-6 flex-1 space-y-3">
                    {plan.features.map((feature) => (
                      <li key={feature} className="flex items-start gap-2 text-sm">
                        <Check className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                        <span className="text-muted-foreground">{feature}</span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Deployment */}
      <section className="border-t divider-gold bg-circuit py-20">
        <div className="mx-auto max-w-4xl px-6">
          <div className="text-center mb-10">
            <div className="gold-line mx-auto w-16 mb-4" />
            <h2 className="text-2xl font-bold tracking-tight">Easy to deploy</h2>
            <p className="mt-3 text-muted-foreground">One script installs everything. Runs on any modern machine.</p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[
              { label: "Operating System", value: "macOS, Linux, Windows (WSL2)" },
              { label: "RAM", value: "16 GB minimum" },
              { label: "Disk", value: "10 GB free space" },
              { label: "Docker", value: "Docker Desktop or Engine" },
              { label: "Startup", value: "One command: ./scripts/start-cerid.sh" },
              { label: "Services", value: "5 containers auto-configured" },
            ].map((spec) => (
              <div key={spec.label} className="rounded-lg border border-border bg-card px-4 py-3">
                <p className="text-xs font-medium text-muted-foreground">{spec.label}</p>
                <p className="mt-1 text-sm font-semibold">{spec.value}</p>
              </div>
            ))}
          </div>
          <p className="mt-6 text-center text-xs text-muted-foreground">
            No cloud account needed. No API keys required to start (Ollama runs fully local).
            Add an OpenRouter key later for access to Claude, GPT, and other frontier models.
          </p>
        </div>
      </section>

      {/* BYOK */}
      <section className="border-t divider-gold bg-muted/20 py-20">
        <div className="mx-auto max-w-4xl px-6 text-center">
          <h2 className="text-2xl font-bold tracking-tight">Bring Your Own Key</h2>
          <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
            All tiers support your own OpenRouter API key. Use Claude, GPT, Gemini,
            Llama — or run Ollama locally for zero API costs. No vendor lock-in.
            Your key is Fernet-encrypted at rest.
          </p>
        </div>
      </section>
    </>
  )
}
