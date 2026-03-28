"""Routers — FastAPI route handlers for all API endpoints.

29 routers registered in main.py, serving both root and /api/v1/ prefixes.
Stable external contract lives in sdk.py (/sdk/v1/ prefix).

Key routers:
  query.py        — RAG query endpoint (single + streaming)
  ingestion.py    — File and content ingestion pipeline
  chat.py         — LLM chat proxy with KB context injection
  health.py       — Liveness, readiness, and status probes
  agents.py       — Agent orchestration (curator, triage, audit)
  sdk.py          — Versioned external SDK contract (9 endpoints)
  observability.py — Real-time metrics dashboard API
  a2a.py          — Agent-to-Agent protocol (Agent Card + tasks)
"""
