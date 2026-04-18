"""Single source of truth for /agent/query and /query response shapes.

Invariants:
    * ``results`` is always ``flatten(source_breakdown.{kb,memory,external})``.
    * ``sources`` mirrors ``results`` (legacy alias for GUI compatibility).
    * ``source_status`` is always set for all three keys.
    * Calling ``mark_degraded`` never drops already-collected results.
    * ``confidence`` is recomputed from the current result pool.

Lives in ``core/`` (not ``app/``) so that ``core.agents.query_agent`` can
emit the degraded-path envelope without violating the
``core must not import app`` import-linter contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

SourceStatus = Literal["ok", "timeout", "disabled", "error"]


@dataclass
class SourceItem:
    content: str
    relevance: float
    artifact_id: str
    filename: str
    source_type: Literal["kb", "memory", "external"]
    domain: str = ""
    chunk_id: str = ""
    collection: str = ""
    source_url: str = ""
    source_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "relevance": self.relevance,
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "source_type": self.source_type,
            "domain": self.domain,
            "chunk_id": self.chunk_id,
            "collection": self.collection,
            "source_url": self.source_url,
            "source_name": self.source_name,
        }


@dataclass
class QueryEnvelope:
    kb: list[SourceItem] = field(default_factory=list)
    memory: list[SourceItem] = field(default_factory=list)
    external: list[SourceItem] = field(default_factory=list)
    kb_status: SourceStatus = "ok"
    memory_status: SourceStatus = "ok"
    external_status: SourceStatus = "ok"
    context: str = ""
    answer: str = ""
    strategy: str = "default"
    budget_exceeded: bool = False
    budget_seconds: float = 0.0
    degraded_reason: str = ""
    token_budget_used: int = 0  # characters; renamed "context_char_count" in Wave 2
    graph_results: int = 0

    def mark_degraded(self, *, budget_seconds: float, reason: str) -> None:
        self.budget_exceeded = True
        self.budget_seconds = budget_seconds
        self.degraded_reason = reason
        self.strategy = "degraded_budget_exhausted"
        # Any bucket that produced zero results AND wasn't explicitly marked
        # disabled/error is treated as timeout for the status field.
        for bucket, status_attr in (("kb", "kb_status"), ("memory", "memory_status"), ("external", "external_status")):
            if not getattr(self, bucket) and getattr(self, status_attr) == "ok":
                setattr(self, status_attr, "timeout")

    def merge_external(self, items: list[SourceItem]) -> None:
        """Append late-arriving external results and upgrade status to 'ok'."""
        if not items:
            return
        self.external.extend(items)
        self.external_status = "ok"

    def _confidence(self) -> float:
        all_items = self.kb + self.memory + self.external
        if not all_items:
            return 0.0
        return round(sum(i.relevance for i in all_items) / len(all_items), 4)

    def to_dict(self) -> dict[str, Any]:
        flat = [i.to_dict() for i in (self.kb + self.memory + self.external)]
        return {
            "results": flat,
            "sources": flat,
            "source_breakdown": {
                "kb": [i.to_dict() for i in self.kb],
                "memory": [i.to_dict() for i in self.memory],
                "external": [i.to_dict() for i in self.external],
            },
            "source_status": {
                "kb": self.kb_status,
                "memory": self.memory_status,
                "external": self.external_status,
            },
            "context": self.context,
            "answer": self.answer,
            "strategy": self.strategy,
            "total_results": len(flat),
            "confidence": self._confidence(),
            "budget_exceeded": self.budget_exceeded,
            "budget_seconds": self.budget_seconds,
            "degraded_reason": self.degraded_reason,
            "timestamp": datetime.now(UTC).isoformat(),
            "token_budget_used": self.token_budget_used,
            "graph_results": self.graph_results,
        }

    @classmethod
    def from_legacy_result(cls, result: dict[str, Any]) -> "QueryEnvelope":
        """Reconstruct envelope from an already-assembled result dict.

        Used when a downstream call (`agent_query`) produced a dict that we
        need to mutate via the envelope API. Idempotent when the result was
        already produced by ``to_dict``.
        """
        sb = result.get("source_breakdown") or {}
        ss = result.get("source_status") or {}

        def _items(key: str) -> list[SourceItem]:
            return [
                SourceItem(**{k: v for k, v in s.items() if k in SourceItem.__dataclass_fields__})
                for s in sb.get(key, [])
            ]

        env = cls(
            kb=_items("kb"),
            memory=_items("memory"),
            external=_items("external"),
            kb_status=ss.get("kb", "ok"),
            memory_status=ss.get("memory", "ok"),
            external_status=ss.get("external", "ok"),
            context=result.get("context", ""),
            answer=result.get("answer", ""),
            strategy=result.get("strategy", "default"),
            budget_exceeded=bool(result.get("budget_exceeded", False)),
            budget_seconds=float(result.get("budget_seconds", 0.0)),
            degraded_reason=result.get("degraded_reason", ""),
            token_budget_used=int(result.get("token_budget_used", 0)),
            graph_results=int(result.get("graph_results", 0)),
        )
        return env
