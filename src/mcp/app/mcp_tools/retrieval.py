# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 3 advanced retrieval tools — 8 tools.

Each leverages existing primitives so the cerid-kb surface grows
without duplicate logic:

* ``pkb_answer_with_citations`` — ``agent_query`` →
  ``check_hallucinations`` → assemble grounded answer.
* ``pkb_question_decompose`` — single ``call_internal_llm`` to break
  a complex question into sub-questions.
* ``pkb_hypothetical_doc`` — HyDE pattern: generate a hypothetical
  answer, retrieve against it.
* ``pkb_summarize_artifact`` — fetch artifact chunks, LLM summarise.
* ``pkb_summarize_domain`` — fetch recent artifacts in a domain,
  LLM synthesise.
* ``pkb_extract_claims`` — exposes the internal claim extractor
  from ``core.agents.hallucination.extraction``.
* ``pkb_extract_entities`` — exposes
  ``core.agents.entity_extraction.extract_entities_from_text``.
* ``pkb_compare_artifacts`` — diff/contrast two-or-more artifacts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import config
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j, get_redis
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    register_tool,
)

logger = logging.getLogger("ai-companion.mcp_tools.retrieval")


# ---------------------------------------------------------------------- helpers


def _llm_call_messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _fetch_artifact_text(artifact_id: str, max_chars: int = 12000) -> str:
    """Pull an artifact's full text from ChromaDB chunks, capped.

    Returns the concatenated chunk text — used by summarisation /
    compare / claim / entity tools that need the literal source text.
    Raises ``ResourceNotFoundError`` when the id doesn't exist.
    """
    from app.mcp_tools.fundamentals import pkb_artifact_get

    record = await pkb_artifact_get(artifact_id=artifact_id, include_chunks=True)
    chunks = record.get("chunks", [])
    text = "\n\n".join(c.get("text", "") for c in chunks)
    if len(text) > max_chars:
        # Truncate at a paragraph boundary if possible
        cut = text.rfind("\n\n", 0, max_chars)
        text = text[: cut if cut > 0 else max_chars]
    return text


# ============================================================ pkb_extract_claims


@register_tool(
    name="pkb_extract_claims",
    description=(
        "Extract structured factual claims from text or an existing "
        "artifact. **Use when** building a structured argument from a "
        "doc, fact-checking a passage, or pre-processing for the "
        "hallucination-check pipeline. Falls back to a regex-based "
        "extractor when the LLM is unreachable. **Returns** "
        "`{claims: [str], method: 'llm'|'heuristic', count}`. Pass "
        "exactly one of `text` or `artifact_id`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Raw text to extract claims from"},
            "artifact_id": {"type": "string", "description": "Artifact UUID (alternative to `text`)"},
            "user_query": {
                "type": "string",
                "description": "Optional context for claim filtering",
                "default": "",
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "claims": {"type": "array", "items": {"type": "string"}},
            "method": {"type": "string", "description": "'llm' or 'heuristic'"},
            "count": {"type": "integer"},
            "source_artifact_id": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_extract_claims(
    text: str = "",
    artifact_id: str = "",
    user_query: str = "",
) -> dict[str, Any]:
    if not text and not artifact_id:
        raise InvalidParamsError("Pass exactly one of `text` or `artifact_id`")
    if text and artifact_id:
        raise InvalidParamsError("Pass exactly one of `text` or `artifact_id`, not both")

    if artifact_id:
        text = await _fetch_artifact_text(artifact_id)

    from core.agents.hallucination.extraction import extract_claims

    claims, method = await extract_claims(text, user_query or None)
    return {
        "claims": claims,
        "method": method,
        "count": len(claims),
        "source_artifact_id": artifact_id,
    }


# ============================================================ pkb_extract_entities


@register_tool(
    name="pkb_extract_entities",
    description=(
        "Extract named entities (PERSON / ORG / ASSET / EVENT / DATE / "
        "LOC / OTHER) from text or an existing artifact. Each entity "
        "is canonicalised (e.g. 'Elon Musk' → `person:elon-musk`) so "
        "it can be linked into the graph. **Use when** preparing "
        "content for graph ingestion or building a named-entity "
        "index for one source. **Returns** `{entities: [{name, type, "
        "canonical_id, confidence}], count}`. Pass exactly one of "
        "`text` or `artifact_id`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Raw text to extract entities from"},
            "artifact_id": {"type": "string", "description": "Artifact UUID (alternative to `text`)"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "canonical_id": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
            "count": {"type": "integer"},
            "source_artifact_id": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_extract_entities(
    text: str = "",
    artifact_id: str = "",
) -> dict[str, Any]:
    if not text and not artifact_id:
        raise InvalidParamsError("Pass exactly one of `text` or `artifact_id`")
    if text and artifact_id:
        raise InvalidParamsError("Pass exactly one of `text` or `artifact_id`, not both")

    if artifact_id:
        text = await _fetch_artifact_text(artifact_id)

    from core.agents.entity_extraction import extract_entities_from_text
    from core.utils.internal_llm import call_internal_llm

    async def _caller(messages, *, json_mode=False, stage=None):
        return await call_internal_llm(
            messages,
            response_format={"type": "json_object"} if json_mode else None,
            stage=stage or "mcp_extract_entities",
        )

    entities = await extract_entities_from_text(text, llm_caller=_caller)
    return {
        "entities": [
            {
                "name": e.name,
                "type": e.entity_type,
                "canonical_id": e.canonical_id,
                "confidence": e.confidence,
            }
            for e in entities
        ],
        "count": len(entities),
        "source_artifact_id": artifact_id,
    }


# ============================================================ pkb_summarize_artifact


@register_tool(
    name="pkb_summarize_artifact",
    description=(
        "Summarise a single artifact at the requested length. **Use "
        "when** the user wants a quick read of a long doc without "
        "paging through chunks. Lengths: `tldr` (~30 words), `short` "
        "(~100), `medium` (~250), `long` (~500). **Returns** "
        "`{summary, length, word_count, artifact_id}`. Errors with "
        "-32004 when the artifact id doesn't exist."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "length": {
                "type": "string",
                "enum": ["tldr", "short", "medium", "long"],
                "default": "medium",
            },
        },
        "required": ["artifact_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "length": {"type": "string"},
            "word_count": {"type": "integer"},
            "artifact_id": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_summarize_artifact(
    artifact_id: str,
    length: str = "medium",
) -> dict[str, Any]:
    _LENGTH_TARGETS = {
        "tldr": "approximately 30 words, one sentence",
        "short": "approximately 100 words, 1-2 paragraphs",
        "medium": "approximately 250 words, 2-3 paragraphs",
        "long": "approximately 500 words, structured with brief sub-headings",
    }
    if length not in _LENGTH_TARGETS:
        raise InvalidParamsError(
            f"length must be one of {sorted(_LENGTH_TARGETS)}; got {length!r}"
        )

    text = await _fetch_artifact_text(artifact_id, max_chars=15000)
    if not text.strip():
        raise ResourceNotFoundError(
            f"Artifact {artifact_id!r} has no chunk content to summarise"
        )

    from core.utils.internal_llm import call_internal_llm

    summary = await call_internal_llm(
        _llm_call_messages(
            system="You are a precise summariser. Produce a focused summary at the target length. No preamble.",
            user=(
                f"Summarise the following content. Target length: "
                f"{_LENGTH_TARGETS[length]}.\n\n---\n{text}\n---"
            ),
        ),
        temperature=0.2,
        max_tokens=900,
        stage="mcp_summarize_artifact",
    )

    return {
        "summary": summary.strip(),
        "length": length,
        "word_count": len(summary.split()),
        "artifact_id": artifact_id,
    }


# ============================================================ pkb_summarize_domain


@register_tool(
    name="pkb_summarize_domain",
    description=(
        "Synthesise a high-level summary across a domain over a period. "
        "Fetches recent artifacts, then asks the LLM to produce a "
        "narrative + themes. **Use when** producing a 'what's new in "
        "<domain>?' briefing. **Returns** `{summary, themes: [str], "
        "standout_artifacts: [{artifact_id, filename, reason}], "
        "domain, period, artifacts_considered}`. Slower than "
        "`pkb_summarize_artifact` (synthesises N inputs)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": f"Domain to summarise ({', '.join(config.DOMAINS)})",
            },
            "period": {
                "type": "string",
                "description": "Lookback (e.g. '24h', '7d', '30d')",
                "default": "7d",
            },
            "max_artifacts": {
                "type": "integer",
                "description": "Maximum artifacts to feed the synthesiser",
                "default": 50,
            },
        },
        "required": ["domain"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "themes": {"type": "array", "items": {"type": "string"}},
            "standout_artifacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "filename": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "domain": {"type": "string"},
            "period": {"type": "string"},
            "artifacts_considered": {"type": "integer"},
        },
    },
    cost_class="high",
)
async def pkb_summarize_domain(
    domain: str,
    period: str = "7d",
    max_artifacts: int = 50,
) -> dict[str, Any]:
    if domain not in config.DOMAINS:
        raise InvalidParamsError(
            f"Invalid domain {domain!r}. Valid: {sorted(config.DOMAINS)}"
        )

    # Parse period into ISO since-time
    from datetime import datetime, timedelta, timezone
    units = {"h": "hours", "d": "days", "w": "weeks"}
    suffix = period[-1].lower() if period else ""
    if suffix in units:
        try:
            n = int(period[:-1])
        except ValueError:
            raise InvalidParamsError(f"Invalid period {period!r}; expected like '24h', '7d', '4w'")
        delta = timedelta(**{units[suffix]: n})
    else:
        raise InvalidParamsError(f"Period must end in h/d/w; got {period!r}")
    since = (datetime.now(timezone.utc) - delta).isoformat()

    driver = get_neo4j()
    artifacts = await asyncio.to_thread(
        graph.list_artifacts,
        driver,
        domain=domain,
        since=since,
        limit=max_artifacts,
    )

    if not artifacts:
        return {
            "summary": f"No artifacts in domain {domain!r} for period {period!r}.",
            "themes": [],
            "standout_artifacts": [],
            "domain": domain,
            "period": period,
            "artifacts_considered": 0,
        }

    # Build the synthesis prompt from filename + summary fields.
    listing = "\n".join(
        f"- {a.get('filename') or a['id'][:8]}: {a.get('summary', '')[:200]}"
        for a in artifacts
    )

    from core.utils.internal_llm import call_internal_llm

    raw = await call_internal_llm(
        _llm_call_messages(
            system=(
                "You are a precise synthesiser. Given a list of recent "
                "artifacts, produce a structured JSON summary. Respond "
                "ONLY with valid JSON in the schema: "
                '{"summary": str, "themes": [str], "standout": [{"id": str, "reason": str}]}. '
                "Maximum 5 themes. Maximum 5 standout artifacts. Use "
                "the verbatim filenames as the id field."
            ),
            user=(
                f"Domain: {domain}. Period: {period}. "
                f"Artifacts ({len(artifacts)}):\n\n{listing}"
            ),
        ),
        temperature=0.2,
        max_tokens=1500,
        response_format={"type": "json_object"},
        stage="mcp_summarize_domain",
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back to plain text summary on bad JSON.
        return {
            "summary": raw.strip()[:2000],
            "themes": [],
            "standout_artifacts": [],
            "domain": domain,
            "period": period,
            "artifacts_considered": len(artifacts),
        }

    # Build standout_artifacts with artifact_id back-resolution by
    # matching the LLM's `id` field against filenames in the list.
    by_filename = {a.get("filename"): a for a in artifacts}
    standout = []
    for s in (parsed.get("standout") or [])[:5]:
        match = by_filename.get(s.get("id", ""))
        if match:
            standout.append({
                "artifact_id": match["id"],
                "filename": match.get("filename", ""),
                "reason": s.get("reason", "")[:300],
            })

    return {
        "summary": parsed.get("summary", "").strip(),
        "themes": list(parsed.get("themes", []))[:5],
        "standout_artifacts": standout,
        "domain": domain,
        "period": period,
        "artifacts_considered": len(artifacts),
    }


# ============================================================ pkb_compare_artifacts


@register_tool(
    name="pkb_compare_artifacts",
    description=(
        "Diff or contrast two-or-more artifacts across configurable "
        "aspects. Each aspect is computed independently so the LLM can "
        "see exactly what's shared vs unique. **Use when** the user "
        "asks 'how does X differ from Y' across docs, or building a "
        "structured argument from competing sources. **Returns** "
        "`{aspects: {summary: {by_id: {...}}, claims: {only_in_<id>: "
        "[...], shared: [...]}, entities: {...}}, ids}`. Aspects "
        "currently supported: `summary`, `claims`, `entities`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Artifact UUIDs to compare (2-5)",
            },
            "aspects": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["summary", "claims", "entities"],
                },
                "description": "Aspects to compute",
                "default": ["summary", "claims"],
            },
        },
        "required": ["ids"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "aspects": {
                "type": "object",
                "description": "Per-aspect comparison (keys: summary, claims, entities)",
            },
            "ids": {"type": "array", "items": {"type": "string"}},
        },
    },
    cost_class="high",
)
async def pkb_compare_artifacts(
    ids: list[str],
    aspects: list[str] | None = None,
) -> dict[str, Any]:
    aspects = aspects or ["summary", "claims"]
    if len(ids) < 2:
        raise InvalidParamsError("Pass at least 2 artifact ids")
    if len(ids) > 5:
        raise InvalidParamsError("Maximum 5 artifacts per comparison")

    result: dict[str, Any] = {"aspects": {}, "ids": ids}

    if "summary" in aspects:
        by_id = {}
        for aid in ids:
            summarised = await pkb_summarize_artifact(artifact_id=aid, length="short")
            by_id[aid] = summarised["summary"]
        result["aspects"]["summary"] = {"by_id": by_id}

    if "claims" in aspects:
        all_claims_by_id: dict[str, list[str]] = {}
        for aid in ids:
            ex = await pkb_extract_claims(artifact_id=aid)
            all_claims_by_id[aid] = ex.get("claims", [])
        # Normalise for set comparison
        norm = {aid: {c.lower().strip() for c in claims} for aid, claims in all_claims_by_id.items()}
        shared = set.intersection(*norm.values()) if norm else set()
        only_in: dict[str, list[str]] = {}
        for aid, claims in norm.items():
            unique = claims - shared
            # Map back to original casing
            origs = all_claims_by_id[aid]
            only_in[f"only_in_{aid}"] = [c for c in origs if c.lower().strip() in unique]
        result["aspects"]["claims"] = {
            **only_in,
            "shared": sorted(shared),
        }

    if "entities" in aspects:
        all_entities: dict[str, list[dict]] = {}
        for aid in ids:
            ex = await pkb_extract_entities(artifact_id=aid)
            all_entities[aid] = ex.get("entities", [])
        # Intersect by canonical_id
        canonical_sets = {
            aid: {e["canonical_id"] for e in elist}
            for aid, elist in all_entities.items()
        }
        shared_canonical = set.intersection(*canonical_sets.values()) if canonical_sets else set()
        only_in_ent: dict[str, list[dict]] = {}
        for aid, elist in all_entities.items():
            only_in_ent[f"only_in_{aid}"] = [
                e for e in elist if e["canonical_id"] not in shared_canonical
            ]
        # Pick one representative for shared
        shared_reps: list[dict] = []
        seen: set[str] = set()
        for elist in all_entities.values():
            for e in elist:
                if e["canonical_id"] in shared_canonical and e["canonical_id"] not in seen:
                    shared_reps.append(e)
                    seen.add(e["canonical_id"])
        result["aspects"]["entities"] = {
            **only_in_ent,
            "shared": shared_reps,
        }

    return result


# ============================================================ pkb_question_decompose


@register_tool(
    name="pkb_question_decompose",
    description=(
        "Break a complex multi-hop question into atomic sub-questions "
        "the retrieval layer can answer independently. **Use when** "
        "about to call `pkb_agent_query` on a question that combines "
        "multiple facts (e.g. 'What's the difference between Stripe's "
        "and Adyen's chargeback handling for European cards?'). "
        "**Returns** `{sub_questions: [str], rationale}`. Default "
        "`max_steps=4` keeps the LLM call cheap."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "max_steps": {
                "type": "integer",
                "description": "Maximum sub-questions to produce",
                "default": 4,
            },
        },
        "required": ["question"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "sub_questions": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
            "original_question": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_question_decompose(
    question: str,
    max_steps: int = 4,
) -> dict[str, Any]:
    if not question.strip():
        raise InvalidParamsError("question must be non-empty")
    max_steps = max(1, min(int(max_steps), 8))

    from core.utils.internal_llm import call_internal_llm

    raw = await call_internal_llm(
        _llm_call_messages(
            system=(
                "You decompose complex questions into independent sub-"
                f"questions (up to {max_steps}). Respond ONLY with valid "
                "JSON: "
                '{"sub_questions": [str], "rationale": str}. '
                "Each sub-question must be answerable by a single "
                "retrieval call; do not chain references. If the "
                "question is already atomic, return it as the sole "
                "sub-question with rationale 'already atomic'."
            ),
            user=question,
        ),
        temperature=0.2,
        max_tokens=600,
        response_format={"type": "json_object"},
        stage="mcp_question_decompose",
    )

    try:
        parsed = json.loads(raw)
        subs = list(parsed.get("sub_questions") or [])[:max_steps]
        rationale = str(parsed.get("rationale") or "")
    except (json.JSONDecodeError, TypeError):
        subs = [question]
        rationale = "decomposition failed; returning original"

    return {
        "sub_questions": subs,
        "rationale": rationale,
        "original_question": question,
    }


# ============================================================ pkb_hypothetical_doc


@register_tool(
    name="pkb_hypothetical_doc",
    description=(
        "HyDE primitive: generate a plausible answer to the question, "
        "then retrieve against THAT hypothetical answer instead of the "
        "raw question. Often produces better recall on technical "
        "questions where the corpus phrasing differs from the user's. "
        "**Use when** `pkb_agent_query` is returning low-confidence or "
        "off-target results for a technical question. **Returns** "
        "`{hypothetical_docs: [str], retrieval: {results, context, "
        "confidence, ...}}`. Runs retrieval automatically so callers "
        "don't have to chain."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "num_variants": {
                "type": "integer",
                "description": "How many hypothetical docs to generate (1-3)",
                "default": 1,
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Domains to search ({', '.join(config.DOMAINS)})",
            },
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["question"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "hypothetical_docs": {"type": "array", "items": {"type": "string"}},
            "retrieval": {"type": "object"},
            "question": {"type": "string"},
        },
    },
    cost_class="high",
)
async def pkb_hypothetical_doc(
    question: str,
    num_variants: int = 1,
    domains: list[str] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    num_variants = max(1, min(int(num_variants), 3))

    from core.agents.query_agent import agent_query
    from core.utils.internal_llm import call_internal_llm

    docs = []
    for _ in range(num_variants):
        doc = await call_internal_llm(
            _llm_call_messages(
                system=(
                    "Write a plausible paragraph that COULD appear in a "
                    "reference document answering the user's question. "
                    "Use the technical vocabulary the corpus would use. "
                    "Do not hedge ('it depends', 'it could be'). Output "
                    "the paragraph only — no preamble, no caveats."
                ),
                user=question,
            ),
            temperature=0.4,  # Some variation between variants
            max_tokens=400,
            stage="mcp_hypothetical_doc",
        )
        docs.append(doc.strip())

    # Retrieve against the synthesised text. Concatenate variants so the
    # embedding is a "centroid" of the multiple guesses.
    retrieval_query = "\n\n".join(docs)
    retrieval = await agent_query(
        query=retrieval_query,
        domains=domains,
        top_k=top_k,
        use_reranking=True,
        chroma_client=get_chroma(),
        redis_client=get_redis(),
        neo4j_driver=get_neo4j(),
    )

    return {
        "hypothetical_docs": docs,
        "retrieval": retrieval,
        "question": question,
    }


# ============================================================ pkb_answer_with_citations


@register_tool(
    name="pkb_answer_with_citations",
    description=(
        "**Killer RAG primitive.** Retrieve → answer → cite — one tool "
        "call. Runs `pkb_agent_query`, generates an answer from the "
        "context, extracts the claims, and binds each claim to its "
        "source chunk(s) by similarity. **Use when** the user wants a "
        "grounded answer with verifiable sources in one shot. "
        "**Returns** `{answer, citations: [{claim, source: "
        "{artifact_id, chunk_id, text_snippet}, confidence}], "
        "unsupported_claims, retrieval_meta}`. Cost class: high — "
        "involves retrieval + answer LLM + claim extraction."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "max_sources": {
                "type": "integer",
                "description": "Max sources to retrieve",
                "default": 5,
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Domains to search ({', '.join(config.DOMAINS)})",
            },
            "verify": {
                "type": "boolean",
                "description": "Run hallucination check on the assembled answer",
                "default": False,
            },
        },
        "required": ["question"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "source": {
                            "type": "object",
                            "properties": {
                                "artifact_id": {"type": "string"},
                                "chunk_id": {"type": "string"},
                                "text_snippet": {"type": "string"},
                            },
                        },
                        "confidence": {"type": "number"},
                    },
                },
            },
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "retrieval_meta": {"type": "object"},
            "question": {"type": "string"},
        },
    },
    cost_class="high",
)
async def pkb_answer_with_citations(
    question: str,
    max_sources: int = 5,
    domains: list[str] | None = None,
    verify: bool = False,
) -> dict[str, Any]:
    if not question.strip():
        raise InvalidParamsError("question must be non-empty")

    from core.agents.hallucination.extraction import extract_claims
    from core.agents.query_agent import agent_query
    from core.retrieval.surface_router import route as _surface_route
    from core.utils.cache import record_chunks_per_answer
    from core.utils.internal_llm import call_internal_llm

    # Phase K3.5 — surface routing. When the router classifies the
    # query as a compiled-summary intent AND we can resolve a wiki
    # page, we pull the page summary into the context budget as a
    # high-priority block ahead of chunk citations. The wiki page is
    # a verified, NLI-gated artifact written by WikiRefreshJob;
    # treating it as a first-class citation surface lets the answer
    # path leverage compounding state instead of re-deriving.
    surface_decision = _surface_route(question)
    wiki_block: str = ""
    wiki_page_meta: dict[str, Any] | None = None
    if (
        "wiki" in surface_decision.surfaces
        and surface_decision.matched_entity_hint
    ):
        try:
            from app.services.wiki_pages import get_entity_page  # noqa: PLC0415

            page = await get_entity_page(
                get_neo4j(), surface_decision.matched_entity_hint,
            )
            if page is not None and getattr(page, "summary", None):
                wiki_page_meta = {
                    "slug": page.slug,
                    "name": page.name,
                    "confidence_band": page.confidence_band,
                    "last_updated_at": page.last_updated_at,
                }
                wiki_block = (
                    f"[Compiled wiki summary for {page.name} "
                    f"(confidence={page.confidence_band})]\n{page.summary}\n\n"
                )
        except Exception:  # noqa: BLE001 — wiki page is optional context
            wiki_block = ""

    # 1. Retrieve
    retrieval = await agent_query(
        query=question,
        domains=domains,
        top_k=max_sources,
        use_reranking=True,
        chroma_client=get_chroma(),
        redis_client=get_redis(),
        neo4j_driver=get_neo4j(),
    )

    results = retrieval.get("results", [])
    if not results and not wiki_block:
        return {
            "answer": "I don't have any sources in the KB matching that question.",
            "citations": [],
            "unsupported_claims": [],
            "retrieval_meta": {
                "domains_searched": retrieval.get("domains_searched", []),
                "total_results": 0,
            },
            "question": question,
        }

    # Record chunks-per-answer for the K-program soak collector. Emitted
    # only on the grounded-answer path (past the no-sources early return),
    # so a "no sources" reply doesn't deflate the baseline. A wiki-only
    # answer legitimately records 0 chunks — that is the compiled-summary win.
    record_chunks_per_answer(
        get_redis(),
        intent=surface_decision.intent,
        chunk_count=len(results),
    )

    # 2. Generate answer grounded in retrieved context.
    # Reserve up to 2000 chars for the wiki block; remaining budget
    # goes to chunk context. The wiki block is structurally separate
    # from chunks so the LLM can cite them differently.
    chunk_budget = max(2000, 8000 - len(wiki_block))
    chunk_context = retrieval.get("context", "")[:chunk_budget]
    context = (wiki_block + chunk_context) if wiki_block else chunk_context
    answer = await call_internal_llm(
        _llm_call_messages(
            system=(
                "Answer the question using ONLY the provided context. "
                "If the context is insufficient, say so explicitly. Do "
                "not invent facts. Cite source identifiers when the "
                "context provides them. Be direct — no preamble."
            ),
            user=f"Question: {question}\n\nContext:\n{context}",
        ),
        temperature=0.1,
        max_tokens=900,
        stage="mcp_answer_with_citations",
    )
    answer = answer.strip()

    # 3. Extract claims from the answer + bind each to its source by
    #    substring overlap with chunk text (simple but effective for v1;
    #    a future iteration can use embedding similarity).
    claims, _method = await extract_claims(answer, user_query=question)
    citations = []
    unsupported = []
    chunks_by_text = [
        (r.get("text", ""), r) for r in results
    ]
    for claim in claims:
        claim_words = set(claim.lower().split())
        # Score each chunk by word overlap; pick the best above threshold.
        best_score, best_chunk = 0.0, None
        for text, r in chunks_by_text:
            chunk_words = set(text.lower().split())
            overlap = len(claim_words & chunk_words)
            score = overlap / max(1, len(claim_words))
            if score > best_score:
                best_score, best_chunk = score, r
        if best_score >= 0.25 and best_chunk:
            citations.append({
                "claim": claim,
                "source": {
                    "artifact_id": best_chunk.get("artifact_id", ""),
                    "chunk_id": best_chunk.get("chunk_id", ""),
                    "text_snippet": (best_chunk.get("text") or "")[:300],
                },
                "confidence": round(float(best_score), 3),
            })
        else:
            unsupported.append(claim)

    out = {
        "answer": answer,
        "citations": citations,
        "unsupported_claims": unsupported,
        "retrieval_meta": {
            "domains_searched": retrieval.get("domains_searched", []),
            "total_results": retrieval.get("total_results", 0),
            "retrieval_confidence": retrieval.get("confidence"),
            "surface_route": {
                "primary": surface_decision.primary,
                "surfaces": surface_decision.surfaces,
                "intent": surface_decision.intent,
                "confidence": surface_decision.confidence,
            },
            "wiki_page": wiki_page_meta,
        },
        "question": question,
    }

    # 4. Optional verify pass — runs hallucination check on the
    #    answer + returns its summary alongside.
    if verify:
        try:
            from core.agents.hallucination import check_hallucinations
            check = await check_hallucinations(
                response_text=answer,
                conversation_id=f"mcp-aws-{hash(question) % 100000}",
                chroma_client=get_chroma(),
                neo4j_driver=get_neo4j(),
                redis_client=get_redis(),
            )
            out["verification"] = check.get("summary", {})
        except Exception as exc:
            logger.warning("pkb_answer_with_citations verify failed: %s", exc)
            out["verification_error"] = str(exc)

    return out
