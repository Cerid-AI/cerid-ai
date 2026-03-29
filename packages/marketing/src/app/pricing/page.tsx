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
    name: "Community",
    price: "Free",
    period: "forever",
    description: "Self-hosted, open source. Full power, no limits.",
    badge: null,
    cta: "Clone on GitHub",
    ctaHref: "https://github.com/sunrunnerfire/cerid-ai",
    features: [
      "All 9 agents",
      "Hybrid BM25s + vector search",
      "Streaming verification",
      "Knowledge graph",
      "27 MCP tools",
      "Self-RAG validation",
      "Smart model routing",
      "Universal file ingestion",
      "Multi-machine sync",
      "Cross-encoder reranking (ONNX)",
      "Contextual chunking",
      "6-stage adaptive RAG pipeline",
      "Semantic query cache",
      "Async batch ingestion",
      "Simple / Advanced mode",
      "Community support",
    ],
  },
  {
    name: "Pro",
    price: "Usage-based",
    period: "per query / per ingestion",
    description: "Managed hosting with usage-based pricing. Bring your own LLM key.",
    badge: "Coming Soon",
    cta: "Star on GitHub",
    ctaHref: "https://github.com/sunrunnerfire/cerid-ai",
    features: [
      "Everything in Community",
      "Managed cloud hosting",
      "Multi-user support",
      "Per-user API key management",
      "Usage metering dashboard",
      "Tenant data isolation",
      "Encrypted API key storage",
      "Priority support",
      "Automatic updates",
      "99.9% uptime SLA",
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
          <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
            {PLANS.map((plan) => (
              <Card
                key={plan.name}
                className={`relative border-border ${
                  plan.badge ? "border-primary/30" : ""
                }`}
              >
                {plan.badge && (
                  <div className="absolute -top-3 right-4">
                    <Badge className="bg-primary text-primary-foreground">
                      {plan.badge}
                    </Badge>
                  </div>
                )}
                <CardHeader>
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
