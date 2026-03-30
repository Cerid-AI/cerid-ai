import type { Metadata } from "next"
import Link from "next/link"
import { ArrowRight, BookOpen, Code2, FileText, Shield, Layers, Cpu, Database, Zap } from "lucide-react"
import { Card, CardHeader, CardTitle } from "@/components/ui/card"
import { BrandShield } from "@/components/brand-shield"

export const metadata: Metadata = {
  title: "Documentation — Cerid AI",
  description: "Cerid AI documentation — API reference, architecture overview, deployment guide, and developer resources.",
}

const GITHUB = "https://github.com/Cerid-AI/cerid-ai/blob/main"

const DOC_SECTIONS = [
  {
    title: "Getting Started",
    icon: BookOpen,
    docs: [
      { name: "README", desc: "Quick start, architecture overview, and setup instructions", href: `${GITHUB}/README.md` },
      { name: "Setup Guide", desc: "Detailed installation with Docker Compose", href: `${GITHUB}/scripts/README.md` },
      { name: "Environment Variables", desc: "All configurable env vars and their defaults", href: `${GITHUB}/docs/ENV_REFERENCE.md` },
    ],
  },
  {
    title: "API Reference",
    icon: Code2,
    docs: [
      { name: "REST API Reference", desc: "All 178 endpoints across 32 routers with request/response shapes", href: `${GITHUB}/docs/API_REFERENCE.md` },
      { name: "MCP Tools", desc: "21 core MCP tools for knowledge base, memory, and verification", href: `${GITHUB}/docs/API_REFERENCE.md#mcp-tools` },
      { name: "SDK Endpoints", desc: "Stable versioned API for external consumers", href: `${GITHUB}/docs/API_REFERENCE.md#sdk-api` },
    ],
  },
  {
    title: "Architecture",
    icon: Layers,
    docs: [
      { name: "Completed Phases", desc: "Full development history from Phase 1 through Phase 51", href: `${GITHUB}/docs/COMPLETED_PHASES.md` },
      { name: "Tier Matrix", desc: "Feature availability across Core, Pro, and Vault tiers", href: `${GITHUB}/docs/TIER_MATRIX.md` },
      { name: "Changelog", desc: "Version history with added features, changes, and fixes", href: `${GITHUB}/CHANGELOG.md` },
    ],
  },
  {
    title: "Security",
    icon: Shield,
    docs: [
      { name: "Security Architecture", desc: "Local-first design, encryption, infrastructure hardening", href: "/security" },
      { name: "License", desc: "Apache-2.0 (Core), BSL-1.1 (Pro plugins), Commercial (Enterprise)", href: `${GITHUB}/LICENSE` },
    ],
  },
  {
    title: "Development",
    icon: Cpu,
    docs: [
      { name: "Contributing", desc: "Development setup, testing, and contribution guidelines", href: `${GITHUB}/CLAUDE.md` },
      { name: "Future Roadmap", desc: "Planned features — External APIs, Watched Folders, RAG Resilience", href: `${GITHUB}/tasks/todo.md` },
    ],
  },
]

export default function DocsPage() {
  return (
    <>
      {/* Hero */}
      <section className="bg-circuit py-20 border-b divider-gold">
        <div className="mx-auto max-w-6xl px-6">
          <div className="flex items-center gap-4">
            <BrandShield variant="vault" size={40} />
            <div>
              <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Documentation</h1>
              <p className="mt-1 text-muted-foreground">Everything you need to deploy, configure, and extend Cerid AI.</p>
            </div>
          </div>
        </div>
      </section>

      {/* Doc sections */}
      <section className="py-16">
        <div className="mx-auto max-w-4xl px-6 space-y-12">
          {DOC_SECTIONS.map((section) => (
            <div key={section.title}>
              <div className="flex items-center gap-2.5 mb-4">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/10 text-brand">
                  <section.icon className="h-4 w-4" />
                </div>
                <h2 className="text-xl font-bold">{section.title}</h2>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {section.docs.map((doc) => (
                  <Link
                    key={doc.name}
                    href={doc.href}
                    target={doc.href.startsWith("http") ? "_blank" : undefined}
                    rel={doc.href.startsWith("http") ? "noopener noreferrer" : undefined}
                    className="group"
                  >
                    <Card className="h-full border-border bg-card transition-all hover:border-brand/30 hover:shadow-md hover:shadow-brand/5">
                      <CardHeader className="p-4 space-y-1.5">
                        <div className="flex items-center justify-between">
                          <CardTitle className="text-sm">{doc.name}</CardTitle>
                          <ArrowRight className="h-3 w-3 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                        <p className="text-xs leading-relaxed text-muted-foreground">{doc.desc}</p>
                      </CardHeader>
                    </Card>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* GitHub CTA */}
      <section className="border-t divider-gold bg-muted/20 py-16 text-center">
        <div className="mx-auto max-w-3xl px-6">
          <h2 className="text-xl font-bold">Full source on GitHub</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            All documentation lives alongside the code. Star the repo to stay updated.
          </p>
          <Link
            href="https://github.com/Cerid-AI/cerid-ai"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-6 inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-6 text-sm font-semibold text-brand-foreground shadow-lg shadow-brand/20 hover:bg-brand/90 transition-all"
          >
            View Repository <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </section>
    </>
  )
}
