import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Pricing — Cerid AI",
  description: "Cerid AI is free and open source. Self-host the full-featured Community edition or wait for managed Pro hosting.",
}
import { Check, ArrowRight } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

const PLANS = [
  {
    name: "Cerid Core",
    price: "Free",
    period: "forever",
    description: "Self-hosted, open source. Smart. Extensible. Private.",
    badge: null,
    accent: "",
    cta: "Get Started",
    ctaHref: "https://github.com/Cerid-AI/cerid-ai",
    icon: "/core-icon.jpg",
    features: [
      "10 AI agents, 26 MCP tools",
      "Unified RAG modes (Manual + Smart)",
      "Hybrid BM25 + vector search",
      "Per-chunk retrieval profiles",
      "Streaming verification (4 claim types)",
      "6-type memory layer with salience scoring",
      "Ollama local LLM support ($0 pipeline)",
      "Cross-encoder reranking (ONNX)",
      "Bulk folder import with preview",
      "Multi-machine sync with Dropbox",
      "Simple / Advanced mode",
      "Community support",
    ],
  },
  {
    name: "Cerid Pro",
    price: "Paid",
    period: "per seat",
    description: "Smart. Secure. Fully Controlled.",
    badge: "Coming Soon",
    accent: "",
    cta: "Star on GitHub",
    ctaHref: "https://github.com/Cerid-AI/cerid-ai",
    icon: "/pro-wordmark.jpg",
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
    price: "Contact",
    period: "enterprise",
    description: "Secure by Design. Mission Assured.",
    badge: "Enterprise",
    accent: "border-gold",
    cta: "Contact Sales",
    ctaHref: "mailto:vault@cerid.ai",
    icon: "/vault-icon.jpg",
    features: [
      "Everything in Pro",
      "Multi-user JWT auth + tenant isolation",
      "SSO / SAML integration",
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
      <section className="bg-gradient-to-b from-background to-muted/30 py-20">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Pricing
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
            Start free with the full-featured self-hosted version. Scale to
            managed hosting when you need multi-user support.
          </p>
        </div>
      </section>

      {/* Plans */}
      <section className="py-16">
        <div className="mx-auto max-w-4xl px-6">
          <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
            {PLANS.map((plan) => (
              <Card
                key={plan.name}
                className={`relative border-border ${plan.accent} ${
                  plan.badge ? "border-primary/30" : ""
                }`}
              >
                {plan.badge && (
                  <div className="absolute -top-3 right-4">
                    <Badge className={plan.accent ? "bg-gold/20 text-gold border-gold" : "bg-primary text-primary-foreground"}>
                      {plan.badge}
                    </Badge>
                  </div>
                )}
                <CardHeader>
                  {plan.icon && (
                    <img src={plan.icon} alt={plan.name} className="mb-3 h-16 w-16 rounded-lg object-cover" />
                  )}
                  <CardTitle className="text-2xl">{plan.name}</CardTitle>
                  <div className="mt-2">
                    <span className="text-4xl font-bold">{plan.price}</span>
                    <span className="ml-2 text-sm text-muted-foreground">
                      {plan.period}
                    </span>
                  </div>
                  <CardDescription className="mt-2">
                    {plan.description}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <Link
                    href={plan.ctaHref}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`inline-flex h-10 w-full items-center justify-center gap-2 rounded-md text-sm font-medium shadow transition-colors ${
                      plan.badge
                        ? "bg-primary text-primary-foreground hover:bg-primary/90"
                        : "border border-input bg-background hover:bg-accent hover:text-accent-foreground"
                    }`}
                  >
                    {plan.cta}
                    <ArrowRight className="h-4 w-4" />
                  </Link>

                  <ul className="mt-6 space-y-3">
                    {plan.features.map((feature) => (
                      <li
                        key={feature}
                        className="flex items-start gap-2 text-sm"
                      >
                        <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                        <span>{feature}</span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* BYOK */}
      <section className="border-t border-border bg-muted/50 py-16">
        <div className="mx-auto max-w-4xl px-6 text-center">
          <h2 className="text-2xl font-bold tracking-tight">
            Bring Your Own Key
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
            Both plans support bringing your own OpenRouter API key. Use your
            preferred LLM provider — Claude, GPT, Gemini, Llama, and more —
            without vendor lock-in. Your key is Fernet-encrypted at rest.
          </p>
        </div>
      </section>
    </>
  )
}
