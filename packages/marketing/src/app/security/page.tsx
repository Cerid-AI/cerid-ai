import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Security — Cerid AI",
  description: "How Cerid AI keeps your data private: local-first architecture, encrypted secrets, infrastructure hardening, and zero telemetry.",
}

import {
  Shield,
  Lock,
  Eye,
  Server,
  Key,
  Database,
  HardDrive,
  Wifi,
  ShieldCheck,
} from "lucide-react"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const SECURITY_FEATURES = [
  {
    icon: Lock,
    title: "Knowledge Stays Local",
    description:
      "Your documents, embeddings, and metadata live on your machine. Only relevant context from queries is sent to your chosen LLM provider for processing.",
  },
  {
    icon: Key,
    title: "Encrypted at Rest",
    description:
      "API keys are Fernet-encrypted. Secrets managed with age encryption. Environment variables never committed to git.",
  },
  {
    icon: Shield,
    title: "Authentication & Authorization",
    description:
      "Optional multi-user JWT auth with bcrypt password hashing (cost 12). Short-lived access tokens (15 min) with refresh token revocation.",
  },
  {
    icon: Eye,
    title: "Rate Limiting",
    description:
      "Sliding-window rate limiting with per-user keys when authenticated. Path-specific limits protect ingestion and agent endpoints.",
  },
  {
    icon: Server,
    title: "Infrastructure Hardening",
    description:
      "Redis authentication enabled. Ports bound to 127.0.0.1. Container resource limits. Security headers via nginx and Caddy.",
  },
  {
    icon: Database,
    title: "Database Security",
    description:
      "Neo4j credential validation on every health check. ChromaDB reset disabled in production. Query parameterization throughout.",
  },
  {
    icon: HardDrive,
    title: "No Vendor Lock-in",
    description:
      "Self-hosted with full data portability. Export and import your entire knowledge base. Switch LLM providers freely.",
  },
  {
    icon: Wifi,
    title: "LAN Access Controls",
    description:
      "Optional Caddy HTTPS gateway. Multi-interface IP detection with stale-IP auto-fix. CORS origin restrictions.",
  },
  {
    icon: ShieldCheck,
    title: "CI/CD Security",
    description:
      "Secret detection in CI pipeline. CodeQL SAST analysis. Dependabot for dependency updates. mypy type checking.",
  },
]

export default function SecurityPage() {
  return (
    <>
      {/* Hero */}
      <section className="bg-circuit py-24 border-b divider-gold">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 items-center gap-12 md:grid-cols-2">
            <div>
              <div className="gold-line w-16 mb-6" />
              <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
                Security & Privacy
              </h1>
              <p className="mt-4 max-w-lg text-lg text-muted-foreground">
                Privacy is not a feature — it&apos;s the architecture. Cerid AI is
                designed from the ground up to keep your data under your control.
              </p>
            </div>
            <div className="flex justify-center gap-4">
              <img src="/badge-zerotrust.jpg" alt="Audit & Zero Trust" className="w-36 rounded-xl border border-border/30 shadow-lg" />
              <img src="/badge-ephemeral.jpg" alt="Ephemeral Data Injection" className="w-36 rounded-xl border border-border/30 shadow-lg" />
            </div>
          </div>
        </div>
      </section>

      {/* Security Grid */}
      <section className="py-16">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {SECURITY_FEATURES.map((feature) => (
              <Card
                key={feature.title}
                className="border-border bg-card"
              >
                <CardHeader>
                  <feature.icon className="mb-2 h-6 w-6 text-primary" />
                  <CardTitle className="text-lg">{feature.title}</CardTitle>
                  <CardDescription>{feature.description}</CardDescription>
                </CardHeader>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Brand showcase */}
      <section className="border-t divider-gold bg-muted/20 py-16">
        <div className="mx-auto max-w-4xl px-6 flex flex-wrap justify-center gap-6">
          <img src="/secure-intel.jpg" alt="Secure Intelligence" className="w-64 rounded-xl border border-border/30 shadow-lg" loading="lazy" />
          <img src="/badge-classrag.jpg" alt="Classification-Aware RAG" className="w-40 rounded-xl border border-border/30 shadow-lg" loading="lazy" />
        </div>
      </section>

      {/* Data flow */}
      <section className="border-t border-border bg-muted/50 py-16">
        <div className="mx-auto max-w-4xl px-6">
          <h2 className="text-center text-2xl font-bold tracking-tight">
            What leaves your machine?
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-center text-muted-foreground">
            Your knowledge base and credentials stay local. Chat context is sent to your chosen LLM provider. Optional Dropbox sync is encrypted when configured.
          </p>

          <div className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-3">
            <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-6">
              <h3 className="font-semibold text-green-600 dark:text-green-400">
                Stays on your machine
              </h3>
              <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                <li>Your original documents and files</li>
                <li>Knowledge base embeddings</li>
                <li>Knowledge graph relationships</li>
                <li>Search indices and caches</li>
                <li>User accounts and API keys</li>
                <li>Audit logs and usage data</li>
              </ul>
            </div>

            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-6">
              <h3 className="font-semibold text-amber-600 dark:text-amber-400">
                Sent to LLM provider (your choice)
              </h3>
              <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                <li>Chat messages and query context</li>
                <li>Relevant KB snippets for answering</li>
                <li>Claims for verification checks</li>
              </ul>
            </div>

            <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-6">
              <h3 className="font-semibold text-blue-600 dark:text-blue-400">
                Optional cloud sync (your Dropbox)
              </h3>
              <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                <li>Conversation history</li>
                <li>Settings and preferences</li>
                <li>Encrypted when key is configured</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* Open Source */}
      <section className="py-16">
        <div className="mx-auto max-w-4xl px-6 text-center">
          <h2 className="text-2xl font-bold tracking-tight">
            Open source. Auditable. Yours.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-muted-foreground">
            Every line of code is open source under the Apache 2.0 license.
            Audit the security model yourself. Run it on your own
            infrastructure. No trust required.
          </p>
        </div>
      </section>
    </>
  )
}
