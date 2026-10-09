# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The forget assistant: find what matches a plain-language scope, then group it.

Finding is deterministic and writes nothing: knowledge-base search over the
readable domains (passages folded into their documents), documents that share
entities with those, memory rows read straight from Chroma (recall would
reinforce the memories it found), and a term match over conversation titles
and messages. Items already forgotten are left out.

Grouping runs on the ``forget_assist`` stage, which never leaves the machine.
When the local model is unavailable the caller learns ``needs_consent`` and
may ask again with ``allow_cloud``; the cloud stage is refused under the
local-only profile and in Private Mode. The model only sorts candidate
numbers into groups: a number it invents is dropped and a candidate it leaves
out lands in "Other matches", so nothing reaches the user that the search did
not find, and nothing the search found is hidden.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import config
from core.forget import registry as forget_registry
from core.utils.swallowed import log_swallowed_error
from core.utils.text import STOPWORDS, WORD_RE

logger = logging.getLogger("ai-companion.forget.assist")

MAX_DOCUMENTS = 40
MAX_NEIGHBOURS = 10
MAX_MEMORIES = 20
MAX_CONVERSATIONS = 20
MEMORY_MIN_RELEVANCE = 0.35
_EXCERPT = 200
_CONVERSATIONS_DOMAIN = "conversations"
_VERIFIED_PREFIX = "verified_memory_"
_MEMORY_TYPES = [
    "empirical", "decision", "preference", "project_context", "temporal", "conversational", "fact", "action_item",
]


def _excerpt(text: Any) -> str:
    from core.retrieval.chunk_ids import chunk_body
    flat = chunk_body(str(text or ""))
    return flat if len(flat) <= _EXCERPT else flat[: _EXCERPT - 1] + "…"


# Words that phrase a forget request rather than name what it is about.
_REQUEST_WORDS = frozenset({
    "everything", "anything", "all", "stuff", "things", "thing", "related", "regarding", "relating",
    "forget", "delete", "remove", "erase", "old", "mentions", "mentioning",
})


def scope_terms(scope: str) -> list[str]:
    """The words of a scope that can match: lower-cased, without stopwords,
    request wording or one-letter tokens."""
    seen: list[str] = []
    for word in WORD_RE.findall(scope.lower()):
        if len(word) > 1 and word not in STOPWORDS and word not in _REQUEST_WORDS and word not in seen:
            seen.append(word)
    return seen


def readable_domains(domains: list[str] | None) -> list[str]:
    """The knowledge-base domains a search covers. Conversations are searched
    on their own: their memories and transcripts are not documents."""
    from utils.domain_privacy import owner_domains, sensitive_domains_opted_in, visible_domains

    base = list(domains) if domains is not None else owner_domains()
    visible = visible_domains(base, include_sensitive=sensitive_domains_opted_in()) or []
    return [d for d in visible if d != _CONVERSATIONS_DOMAIN]


async def _documents(scope: str, domains: list[str]) -> list[dict[str, Any]]:
    # The per-domain arms directly, not agent_query_full: that path logs the
    # query, writes the semantic cache and may search the web, and a forget
    # search must write nothing and stay on the machine.
    from app.deps import get_chroma
    from core.agents.query_agent import (  # retrieval-import-allowed: a forget search must write nothing
        multi_domain_query,
    )

    if not domains:
        return []
    results = await multi_domain_query(scope, domains=domains, top_k=10, chroma_client=get_chroma())
    docs: dict[str, dict[str, Any]] = {}
    for r in sorted(results, key=lambda x: x.get("relevance", 0.0), reverse=True):
        aid = str(r.get("artifact_id") or "")
        if not aid or aid.startswith("community:") or r.get("source_type") == "external":
            continue
        unit = str(r.get("parent_chunk_id") or r.get("chunk_id") or "")
        doc = docs.get(aid)
        if doc is None:
            if len(docs) >= MAX_DOCUMENTS:
                continue
            doc = docs[aid] = {
                "kind": "artifact", "id": aid, "store": "knowledge_base",
                "label": str(r.get("filename") or aid), "domain": str(r.get("domain") or ""),
                "excerpt": _excerpt(r.get("content")), "reason": "matches the wording",
                "score": round(float(r.get("relevance") or 0.0), 4), "passages": [],
            }
        if unit and all(p["id"] != unit for p in doc["passages"]):
            doc["passages"].append({"kind": "chunk", "id": unit, "excerpt": _excerpt(r.get("content"))})
    return list(docs.values())


def _neighbours(seeds: list[dict[str, Any]], domains: list[str]) -> list[dict[str, Any]]:
    from app.deps import get_neo4j
    from core.retrieval.graphrag_retriever import entity_neighborhood_artifact_ids

    seed_ids = [d["id"] for d in seeds[:10]]
    if not seed_ids:
        return []
    driver = get_neo4j()
    pairs = entity_neighborhood_artifact_ids(driver, seed_ids, top_k=MAX_NEIGHBOURS * 2)
    if not pairs:
        return []
    shared = dict(pairs)
    with driver.session() as session:
        rows = [dict(r) for r in session.run(
            "UNWIND $ids AS i MATCH (a:Artifact {id: i}) WHERE NOT coalesce(a.archived, false) "
            "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, a.summary AS summary",
            ids=[aid for aid, _ in pairs],
        )]
    out = []
    for row in rows:
        if row.get("domain") not in domains or forget_registry.is_forgotten("artifact", row["id"]):
            continue
        out.append({
            "kind": "artifact", "id": row["id"], "store": "knowledge_base",
            "label": str(row.get("filename") or row["id"]), "domain": str(row.get("domain") or ""),
            "excerpt": _excerpt(row.get("summary")),
            "reason": f"mentions {shared.get(row['id'], 1)} of the same people, places or things",
            "score": 0.0, "passages": [],
        })
    out.sort(key=lambda c: shared.get(c["id"], 0), reverse=True)
    return out[:MAX_NEIGHBOURS]


def _memories(scope: str) -> list[dict[str, Any]]:
    from app.deps import get_chroma
    from core.context.identity import with_tenant_scope
    from core.utils.embeddings import l2_distance_to_relevance

    collection = get_chroma().get_or_create_collection(name=config.collection_name(_CONVERSATIONS_DOMAIN))
    got = collection.query(
        query_texts=[scope], n_results=MAX_MEMORIES * 2,
        where=with_tenant_scope({"memory_type": {"$in": _MEMORY_TYPES}}),
        include=["documents", "metadatas", "distances"],
    )
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    ids = (got.get("ids") or [[]])[0]
    for i, row_id in enumerate(ids):
        meta = (got.get("metadatas") or [[]])[0][i] or {}
        relevance = l2_distance_to_relevance((got.get("distances") or [[1.0]])[0][i])
        if relevance < MEMORY_MIN_RELEVANCE:
            continue
        if str(row_id).startswith(_VERIFIED_PREFIX):
            kind, sid = "memory", str(row_id)[len(_VERIFIED_PREFIX):]
        else:
            kind, sid = "artifact", str(meta.get("artifact_id") or "")
        if not sid or (kind, sid) in seen or forget_registry.is_forgotten(kind, sid):
            continue
        seen.add((kind, sid))
        text = (got.get("documents") or [[]])[0][i]
        out.append({
            "kind": kind, "id": sid, "store": "memories", "label": _excerpt(meta.get("summary") or text),
            "domain": _CONVERSATIONS_DOMAIN, "excerpt": _excerpt(text), "reason": "a memory close to the wording",
            "score": round(relevance, 4), "passages": [],
        })
        if len(out) >= MAX_MEMORIES:
            break
    return out


def _conversations(terms: list[str]) -> list[dict[str, Any]]:
    """Conversations whose title or messages contain at least half the scope's terms."""
    if not terms or not config.SYNC_DIR:
        return []
    from app.sync.user_state import read_conversations

    need = max(1, (len(terms) + 1) // 2)
    # Whole words: "tax" must not match "syntax", nor "ai" match "said".
    patterns = {t: re.compile(rf"\b{re.escape(t)}\b") for t in terms}
    scored: list[tuple[int, dict[str, Any]]] = []
    for convo in read_conversations(config.SYNC_DIR):
        cid = str(convo.get("id") or "")
        if not cid or forget_registry.is_forgotten("conversation", cid):
            continue
        title = str(convo.get("title") or "")
        texts = [title] + [str((m or {}).get("content") or "") for m in convo.get("messages") or []]
        lowered = [t.lower() for t in texts]
        hits = [t for t in terms if any(patterns[t].search(x) for x in lowered)]
        if len(hits) < need:
            continue
        snippet = next((t for t in texts[1:] if any(patterns[h].search(t.lower()) for h in hits)), title)
        scored.append((len(hits), {
            "kind": "conversation", "id": cid, "store": "conversations", "label": title or "Untitled chat",
            "domain": "", "excerpt": _excerpt(snippet), "reason": f"mentions {', '.join(hits)}",
            "score": float(len(hits)), "passages": [],
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:MAX_CONVERSATIONS]]


async def find_candidates(scope: str, *, domains: list[str] | None = None) -> list[dict[str, Any]]:
    """Everything Cerid holds that matches *scope*, from every store. A store
    that fails is logged and skipped; the others still answer."""
    scope = scope.strip()
    readable = readable_domains(domains)
    candidates: list[dict[str, Any]] = []
    try:
        docs = await _documents(scope, readable)
    except Exception as exc:  # noqa: BLE001 — one store must not hide the others
        log_swallowed_error("app.services.forget.assist.documents", exc)
        docs = []
    candidates.extend(docs)
    try:
        neighbours = await asyncio.to_thread(_neighbours, docs, readable)
    except Exception as exc:  # noqa: BLE001 — one store must not hide the others
        log_swallowed_error("app.services.forget.assist.neighbours", exc)
        neighbours = []
    have = {(c["kind"], c["id"]) for c in candidates}
    candidates.extend(n for n in neighbours if (n["kind"], n["id"]) not in have)
    if domains is None:
        try:
            candidates.extend(await asyncio.to_thread(_memories, scope))
        except Exception as exc:  # noqa: BLE001 — one store must not hide the others
            log_swallowed_error("app.services.forget.assist.memories", exc)
        try:
            candidates.extend(await asyncio.to_thread(_conversations, scope_terms(scope)))
        except Exception as exc:  # noqa: BLE001 — one store must not hide the others
            log_swallowed_error("app.services.forget.assist.conversations", exc)
    return candidates


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #

_SYSTEM = (
    "You help a person decide what to delete from their personal knowledge base. They described what they want "
    "forgotten. You get a numbered list of items a search found. Sort the items into a few groups a person can "
    "decide on together, and say in one plain sentence why each group matches the description. Put items that "
    "probably do not match in one group titled \"Probably unrelated\". Use only the numbers given; put each "
    "number in at most one group. Answer with JSON only: "
    '{"groups": [{"title": "...", "explanation": "...", "items": [1, 2]}]}'
)

_KIND_TITLES = {"artifact": "Documents", "memory": "Memories", "conversation": "Conversations"}


def _prompt(scope: str, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    lines = [
        f"{i}. [{c['store']}] {c['label']}: {c['excerpt']}"
        for i, c in enumerate(candidates, start=1)
    ]
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"What to forget: {scope}\n\nItems:\n" + "\n".join(lines)},
    ]


def _ungrouped(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One group per kind of store, in a fixed order, with no explanation."""
    groups: list[dict[str, Any]] = []
    for store, title in (("knowledge_base", "Documents"), ("memories", "Memories"), ("conversations", "Conversations")):
        items = [c for c in candidates if c["store"] == store]
        if items:
            groups.append({"title": title, "explanation": "", "items": items})
    return groups


def parse_groups(raw: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's groups, keeping only the numbers it was given, each once;
    every candidate it left out goes to "Other matches"."""
    from core.utils.llm_parsing import parse_llm_json

    data = parse_llm_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ValueError("the model did not return groups")
    placed: set[int] = set()
    groups: list[dict[str, Any]] = []
    for g in data["groups"]:
        if not isinstance(g, dict):
            continue
        numbers = []
        for n in g.get("items") or []:
            if isinstance(n, int) and 1 <= n <= len(candidates) and n not in placed:
                placed.add(n)
                numbers.append(n)
        if numbers:
            groups.append({
                "title": str(g.get("title") or "Group")[:80],
                "explanation": str(g.get("explanation") or "")[:300],
                "items": [candidates[n - 1] for n in numbers],
            })
    rest = [c for i, c in enumerate(candidates, start=1) if i not in placed]
    if rest:
        groups.append({"title": "Other matches", "explanation": "", "items": rest})
    return groups


def cloud_refusal() -> str:
    """Why a consented cloud call is not allowed right now; "" when it is."""
    if getattr(config, "CERID_ENVIRONMENT_PROFILE", "") == "local-only":
        return "This install is set to local-only, so nothing is sent to a cloud model."
    try:
        from app.services.private_mode import get_private_mode_level
        if get_private_mode_level() >= 1:
            return "Private Mode is on, so nothing is sent to a cloud model."
    except Exception as exc:  # noqa: BLE001 — unknown privacy state refuses the cloud
        log_swallowed_error("app.services.forget.assist.private_mode", exc)
        return "Private Mode could not be read, so nothing is sent to a cloud model."
    return ""


def cloud_model() -> str:
    from core.utils.internal_llm import _resolve_stage_model
    return _resolve_stage_model("forget_assist_cloud") or str(getattr(config, "INTERNAL_LLM_JSON_FALLBACK_MODEL", ""))


async def group_candidates(
    scope: str, candidates: list[dict[str, Any]], *, allow_cloud: bool = False,
) -> dict[str, Any]:
    """Group candidates on the local model, or on the cloud model when the
    caller consented. Returns ``{status, model, reason, cloud_model, groups}``."""
    from core.utils.internal_llm import call_internal_llm

    if not candidates:
        return {"status": "grouped", "model": None, "reason": "", "cloud_model": "", "groups": []}
    messages = _prompt(scope, candidates)
    stage = "forget_assist"
    if allow_cloud:
        refusal = cloud_refusal()
        if refusal:
            return {"status": "ungrouped", "model": None, "reason": refusal, "cloud_model": "",
                    "groups": _ungrouped(candidates)}
        stage = "forget_assist_cloud"
    try:
        raw = await call_internal_llm(
            messages, temperature=0.0, max_tokens=1500, response_format={"type": "json_object"},
            stage=stage, interactive=True,
        )
        groups = parse_groups(raw, candidates)
    except Exception as exc:  # noqa: BLE001 — a model that is down or answers badly leaves the list ungrouped
        log_swallowed_error("app.services.forget.assist.group", exc, context={"stage": stage})
        if stage == "forget_assist":
            refusal = cloud_refusal()
            return {
                "status": "ungrouped" if refusal else "needs_consent",
                "model": None,
                "reason": refusal or "The local model is not available.",
                "cloud_model": "" if refusal else cloud_model(),
                "groups": _ungrouped(candidates),
            }
        return {"status": "ungrouped", "model": None, "reason": "The cloud model did not answer.",
                "cloud_model": "", "groups": _ungrouped(candidates)}
    return {"status": "grouped", "model": "cloud" if allow_cloud else "local", "reason": "", "cloud_model": "",
            "groups": groups}


async def assist(scope: str, *, allow_cloud: bool = False, domains: list[str] | None = None) -> dict[str, Any]:
    candidates = await find_candidates(scope, domains=domains)
    result = await group_candidates(scope, candidates, allow_cloud=allow_cloud)
    result["scope"] = scope
    result["total"] = len(candidates)
    return result

