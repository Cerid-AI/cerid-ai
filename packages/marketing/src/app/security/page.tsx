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
    title: "Data Stays Local",
    description:
      "All your knowledge, embeddings, and metadata live on your machine. Nothing is sent to external servers except LLM API calls.",
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
      <section className="bg-gradient-to-b from-background to-muted/30 py-20">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
            <Shield className="h-8 w-8 text-primary" />
          </div>
          <h1 className="mt-6 text-4xl font-bold tracking-tight sm:text-5xl">
            Security & Privacy
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
            Privacy is not a feature — it is the architecture. Cerid AI is
            designed from the ground up to keep your data under your control.
          </p>
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

      {/* Data flow */}
      <section className="border-t border-border bg-muted/50 py-16">
        <div className="mx-auto max-w-4xl px-6">
          <h2 className="text-center text-2xl font-bold tracking-tight">
            What leaves your machine?
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-center text-muted-foreground">
            Only LLM API calls go external. Everything else stays local.
          </p>

          <div className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-2">
            <div className="rounded-lg border border-green-500/30 bg-green-500/5 p-6">
              <h3 className="font-semibold text-green-600 dark:text-green-400">
                Stays on your machine
              </h3>
              <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                <li>Your documents and files</li>
                <li>Knowledge base embeddings</li>
                <li>Knowledge graph relationships</li>
                <li>Search indices and caches</li>
                <li>Conversation history</li>
                <li>User accounts and API keys</li>
                <li>Audit logs and usage data</li>
              </ul>
            </div>

            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-6">
              <h3 className="font-semibold text-amber-600 dark:text-amber-400">
                Sent externally (encrypted)
              </h3>
              <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                <li>LLM prompts via HTTPS to OpenRouter</li>
                <li>Your chosen LLM provider processes the query</li>
                <li>Responses streamed back over HTTPS</li>
              </ul>
              <p className="mt-4 text-xs text-muted-foreground">
                You choose the LLM provider. Use your own API key. No data is
                stored by the gateway.
              </p>
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
