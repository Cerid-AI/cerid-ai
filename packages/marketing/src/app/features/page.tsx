import {
  Search,
  Bot,
  Database,
  Shield,
  GitBranch,
  Zap,
  Brain,
  FileText,
  RefreshCw,
  Eye,
  Layers,
  MessageSquare,
  Cpu,
  HardDrive,
  Network,
} from "lucide-react"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

const AGENTS = [
  { name: "Query", description: "Orchestrates RAG retrieval with context-aware KB injection" },
  { name: "Triage", description: "Routes queries to the most capable model" },
  { name: "Curator", description: "Manages knowledge quality and metadata enrichment" },
  { name: "Rectify", description: "Detects and corrects outdated or conflicting knowledge" },
  { name: "Audit", description: "Monitors system health and data integrity" },
  { name: "Maintenance", description: "Handles cleanup, deduplication, and optimization" },
  { name: "Hallucination", description: "Validates claims against your knowledge base" },
  { name: "Memory", description: "Manages long-term conversational memory" },
  { name: "Self-RAG", description: "Self-reflective retrieval for higher accuracy" },
]

const FEATURES = [
  {
    icon: Search,
    title: "Hybrid Search",
    description:
      "BM25s with stemming + ChromaDB vector search. Semantic chunking preserves context across document boundaries.",
    tag: "Retrieval",
  },
  {
    icon: Eye,
    title: "Streaming Verification",
    description:
      "Real-time claim verification as the LLM responds. Four claim types: evasion, citation, recency, and ignorance detection.",
    tag: "Verification",
  },
  {
    icon: RefreshCw,
    title: "Self-RAG Validation",
    description:
      "Self-reflective retrieval-augmented generation. The system validates its own retrieval quality before responding.",
    tag: "Quality",
  },
  {
    icon: Zap,
    title: "Smart Model Routing",
    description:
      "Three-way routing with capability-based model scoring. Direct-to-OpenRouter chat proxy. Proactive model switching on ignorance detection.",
    tag: "Routing",
  },
  {
    icon: FileText,
    title: "Universal Ingestion",
    description:
      "PDF, Office, email, ebooks, structured data. Watch directories for automatic ingestion. Drag-and-drop in the UI.",
    tag: "Ingestion",
  },
  {
    icon: GitBranch,
    title: "Knowledge Graph",
    description:
      "Neo4j-backed relationships between artifacts. Domain taxonomy, quality badges, tag vocabulary with typeahead.",
    tag: "Graph",
  },
  {
    icon: Layers,
    title: "Context-Aware Chat",
    description:
      "Corrections, token-budget KB injection, semantic dedup. Smart KB suggestions surface relevant context automatically.",
    tag: "Chat",
  },
  {
    icon: MessageSquare,
    title: "18 MCP Tools",
    description:
      "Full Model Context Protocol integration. Query, ingest, search, manage taxonomy, and more — all via MCP.",
    tag: "Integration",
  },
  {
    icon: Network,
    title: "Circuit Breakers",
    description:
      "All Bifrost and Neo4j calls protected by circuit breakers. Graceful degradation when services are overloaded.",
    tag: "Reliability",
  },
]

export default function FeaturesPage() {
  return (
    <>
      {/* Hero */}
      <section className="bg-gradient-to-b from-background to-muted/30 py-20">
        <div className="mx-auto max-w-6xl px-6 text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Features
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
            A complete AI knowledge system with retrieval, verification,
            intelligent agents, and a knowledge graph — all running locally.
          </p>
        </div>
      </section>

      {/* Feature Grid */}
      <section className="py-16">
        <div className="mx-auto max-w-6xl px-6">
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <Card key={feature.title} className="border-border bg-card">
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <feature.icon className="h-6 w-6 text-primary" />
                    <Badge variant="secondary" className="text-xs">
                      {feature.tag}
                    </Badge>
                  </div>
                  <CardTitle className="mt-3 text-lg">{feature.title}</CardTitle>
                  <CardDescription>{feature.description}</CardDescription>
                </CardHeader>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Agents */}
      <section className="border-t border-border bg-muted/50 py-16">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <div className="inline-flex items-center gap-2 text-primary">
              <Bot className="h-5 w-5" />
              <span className="text-sm font-semibold uppercase tracking-wider">
                Agent System
              </span>
            </div>
            <h2 className="mt-3 text-3xl font-bold tracking-tight">
              9 intelligent agents
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-muted-foreground">
              Specialized agents collaborate to ensure high-quality retrieval,
              verification, and knowledge management.
            </p>
          </div>

          <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {AGENTS.map((agent) => (
              <div
                key={agent.name}
                className="rounded-lg border border-border bg-card p-4"
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Cpu className="h-4 w-4" />
                  </div>
                  <h3 className="font-semibold">{agent.name}</h3>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">
                  {agent.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Architecture */}
      <section className="py-16">
        <div className="mx-auto max-w-6xl px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight">
              Architecture
            </h2>
            <p className="mx-auto mt-4 max-w-2xl text-muted-foreground">
              Six core services working together on a shared Docker network.
            </p>
          </div>

          <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[
              { icon: Brain, name: "MCP Server", port: "8888", tech: "FastAPI / Python 3.11" },
              { icon: Zap, name: "Bifrost Gateway", port: "8080", tech: "Semantic intent routing" },
              { icon: HardDrive, name: "ChromaDB", port: "8001", tech: "Vector database" },
              { icon: GitBranch, name: "Neo4j", port: "7474", tech: "Graph database" },
              { icon: Database, name: "Redis", port: "6379", tech: "Cache + audit log" },
              { icon: Shield, name: "React GUI", port: "3000", tech: "React 19 + Vite + nginx" },
            ].map((svc) => (
              <div
                key={svc.name}
                className="flex items-start gap-4 rounded-lg border border-border bg-card p-4"
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <svc.icon className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="font-semibold">{svc.name}</h3>
                  <p className="text-sm text-muted-foreground">
                    Port {svc.port} &middot; {svc.tech}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  )
}
