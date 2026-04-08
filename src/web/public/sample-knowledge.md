# Cerid AI — What Is It?

Cerid AI is a privacy-first personal AI knowledge companion. It connects your documents, notes, and files to powerful language models — while keeping everything on your machine.

## How It Works

When you drop a document into Cerid, it goes through a multi-stage pipeline:

1. **Parsing** — PDFs, Word docs, Markdown, and plain text are extracted into clean text
2. **Chunking** — Long documents are split into semantically meaningful pieces
3. **Embedding** — Each chunk is converted to a vector representation using an embedding model
4. **Storage** — Vectors go to ChromaDB for semantic search, relationships go to Neo4j for graph queries
5. **Deduplication** — SHA-256 hashing prevents duplicate content from entering the knowledge base

## Querying Your Knowledge

When you ask Cerid a question, it uses Retrieval-Augmented Generation (RAG):

- Your question is embedded and matched against your knowledge base
- The most relevant document chunks are retrieved
- These chunks are sent as context alongside your question to the LLM
- The model generates an answer grounded in your actual documents

This means Cerid's answers are based on **your data**, not just the model's training data.

## Key Features

- **Multi-Domain Knowledge** — Organize documents into domains like Coding, Finance, Personal, and Projects. Cerid routes queries to the right domain automatically.
- **Verification Pipeline** — Every AI response can be verified against source documents. Claims are extracted and checked for factual grounding.
- **Local-First** — All data stays on your machine. LLM API calls send only the query context, never your raw documents.
- **Smart Routing** — Cerid classifies your intent (coding help, research, simple question) and routes to the best model for the task.
- **Memory** — Cerid remembers context from previous conversations and can surface relevant memories in future queries.

## Architecture

Cerid runs as a set of microservices:

- **MCP Server** (port 8888) — The core API that powers ingestion, retrieval, and agent orchestration
- **Bifrost** (port 8080) — LLM gateway that handles model routing and intent classification
- **ChromaDB** (port 8001) — Vector database for semantic search
- **Neo4j** (port 7474) — Graph database for document relationships
- **Redis** (port 6379) — Cache layer for query results and audit logging

## Getting Started

After setup, try these:

1. Drop a PDF into your archive folder and watch it get ingested automatically
2. Ask "What documents do I have?" to see your knowledge base
3. Ask a specific question about a document you ingested
4. Check the verification panel to see how claims are grounded in sources
