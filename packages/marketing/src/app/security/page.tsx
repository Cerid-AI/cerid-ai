import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Security — Cerid AI",
  description: "How Cerid AI keeps your data private: local-first architecture, encrypted secrets, infrastructure hardening, and opt-in-only telemetry.",
}

import { SecurityCard } from "@/components/security-card"

const SECURITY_FEATURES = [
  {
    iconName: "Lock",
    title: "Knowledge Stays Local",
    description: "Your documents, embeddings, and metadata live on your machine.",
    detail: "All data is stored in local Docker containers — ChromaDB for vector embeddings, Neo4j for the knowledge graph, and Redis for caching. Only the relevant context from your queries is sent to the LLM provider you choose. You can run Ollama for fully offline operation with zero data leaving your network.",
  },
  {
    iconName: "Key",
    title: "Encrypted at Rest",
    description: "API keys are Fernet-encrypted. Secrets managed with age encryption.",
    detail: "All API keys and credentials are encrypted using the Fernet symmetric encryption scheme before storage. The master encryption key is set via the CERID_ENCRYPTION_KEY environment variable. Secrets are managed with age encryption. Environment variables are never committed to git — a comprehensive .env.example documents all available options.",
  },
  {
    iconName: "Shield",
    title: "Authentication & Authorization",
    description: "Optional multi-user JWT auth with bcrypt password hashing.",
    detail: "When CERID_MULTI_USER is enabled, the system uses JWT-based authentication with bcrypt password hashing (cost factor 12). Access tokens are short-lived (15 minutes) with separate refresh tokens (7 days) that support revocation. Per-user API key management enables programmatic access with usage metering.",
  },
  {
    iconName: "Eye",
    title: "Rate Limiting",
    description: "Sliding-window rate limiting with per-client isolation.",
    detail: "A sliding-window rate limiter enforces per-client quotas via the X-Client-ID header. Default limits are 20 req/min for the GUI, 80 req/min for trading agents, and 10 req/min for unknown clients. Path-specific limits protect ingestion and agent endpoints from abuse. All limits are configurable via CONSUMER_REGISTRY.",
  },
  {
    iconName: "Server",
    title: "Infrastructure Hardening",
    description: "Ports bound to localhost. Container resource limits. Security headers.",
    detail: "Redis requires authentication. All database ports (Neo4j 7474/7687, ChromaDB 8001, Redis 6379) are bound to 127.0.0.1 — not accessible from the network. Docker containers have memory and CPU resource limits. Nginx and optional Caddy reverse proxy add security headers (X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security).",
  },
  {
    iconName: "Database",
    title: "Database Security",
    description: "Credential validation. Reset disabled. Query parameterization.",
    detail: "Neo4j credentials are validated on every health check to catch misconfigurations early. The ChromaDB reset endpoint is disabled in production to prevent accidental data loss. All database queries use parameterization to prevent injection attacks. Content hashes use SHA-256 with a UNIQUE constraint for atomic deduplication.",
  },
  {
    iconName: "HardDrive",
    title: "No Vendor Lock-in",
    description: "Self-hosted with full data portability.",
    detail: "Export your entire knowledge base as JSONL files at any time via the sync API. Import on another machine or restore from backup. Switch LLM providers freely — Cerid routes through OpenRouter supporting 20+ model providers. Run Ollama locally for zero external dependencies. All data formats are open and documented.",
  },
  {
    iconName: "Wifi",
    title: "LAN Access Controls",
    description: "HTTPS gateway. IP detection. CORS restrictions.",
    detail: "An optional Caddy reverse proxy provides automatic HTTPS with Let's Encrypt certificates. The startup script detects all network interfaces and configures LAN access URLs for iPad and other devices. CORS origins are restricted to configured domains. Stale IP addresses are automatically detected and corrected on startup.",
  },
  {
    iconName: "ShieldCheck",
    title: "CI/CD Security",
    description: "Secret detection. Dependency audit. Type checking.",
    detail: "The 9-job CI pipeline includes: detect-secrets scanning all tracked files, bandit static analysis for Python security issues, pip-audit for dependency vulnerabilities (including transitive), Trivy container image scanning for CRITICAL/HIGH CVEs, ruff linting with inline tier-check enforcement, and mypy type checking. All jobs must pass before the Docker image is built.",
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
            <div className="flex justify-center">
              <img src="/secure-intel.jpg" alt="Cerid AI — Secure Intelligence, Fully Yours" className="w-full max-w-lg rounded-xl border border-border/30 shadow-xl" />
            </div>
          </div>
        </div>
      </section>

      {/* Security Grid — click to expand */}
      <section className="py-16 bg-circuit">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {SECURITY_FEATURES.map((feature) => (
              <SecurityCard
                key={feature.title}
                iconName={feature.iconName}
                title={feature.title}
                description={feature.description}
                detail={feature.detail}
              />
            ))}
          </div>
        </div>
      </section>

      {/* Data flow */}
      <section className="border-t divider-gold bg-muted/30 py-16">
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
