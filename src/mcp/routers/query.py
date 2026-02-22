"""Query endpoint and query_knowledge service function."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict

from fastapi import APIRouter
from pydantic import BaseModel

import config
from deps import get_chroma

router = APIRouter()
logger = logging.getLogger("ai-companion")


def query_knowledge(query: str, domain: str = "general", top_k: int = 3) -> Dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "distances", "metadatas"],
    )
    timestamp = datetime.utcnow().isoformat()
    if not results.get("documents") or not results["documents"][0]:
        return {"context": "", "sources": [], "confidence": 0.0, "timestamp": timestamp}

    docs = results["documents"][0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    sources = []
    for i, doc in enumerate(docs):
        dist = distances[i] if i < len(distances) else 1.0
        relevance = max(0.0, min(1.0, 1.0 - dist))
        meta = metadatas[i] if i < len(metadatas) else {}
        sources.append({
            "content": doc[:200],
            "relevance": round(relevance, 3),
            "artifact_id": meta.get("artifact_id", ""),
            "filename": meta.get("filename", ""),
            "domain": meta.get("domain", domain),
            "chunk_index": meta.get("chunk_index", 0),
        })

    TOKEN_BUDGET_CHARS = 14000
    context_parts = []
    char_count = 0
    for doc in docs:
        if char_count + len(doc) > TOKEN_BUDGET_CHARS:
            remaining = TOKEN_BUDGET_CHARS - char_count
            if remaining > 200:
                context_parts.append(doc[:remaining] + "\n[...truncated for token budget...]")
            break
        context_parts.append(doc)
        char_count += len(doc)

    context = "\n\n".join(context_parts)
    avg_relevance = sum(s["relevance"] for s in sources) / len(sources) if sources else 0.0

    return {
        "context": context,
        "sources": sources,
        "confidence": round(avg_relevance, 3),
        "timestamp": timestamp,
    }


class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = 3


@router.post("/query")
async def query_endpoint(req: QueryRequest):
    return query_knowledge(req.query, req.domain, req.top_k)
