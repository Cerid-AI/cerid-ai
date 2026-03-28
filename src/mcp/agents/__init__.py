"""Agents — domain-specific AI agents for query, verification, memory, and curation.

Modules:
  query_agent.py — RAG retrieval pipeline orchestrator (8 strategies)
  hallucination/ — Claim extraction + verification (6 sub-modules)
  memory.py     — Memory extraction, decay, conflict resolution
  curator.py    — KB quality scoring + recommendations
  triage.py     — Intent classification and routing
  rectify.py    — Automated error correction
  audit.py      — Ingestion audit trail and quality checks
  maintenance.py — Scheduled KB maintenance tasks
  self_rag.py   — Self-RAG validation loop
  assembler.py  — Intelligent context assembly with facet coverage
  decomposer.py — Query decomposition for multi-aspect retrieval
"""
