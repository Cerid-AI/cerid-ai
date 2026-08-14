# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""LLM-summarised community descriptions for the GraphRAG global mode.

Workstream E Phase 4b.2. For each ``(:Community)`` produced by
:mod:`app.db.neo4j.community_detection`, fetch the top-K entities by
degree centrality, fetch one representative chunk per entity, and
ask the LLM to summarise the community's shared theme. The summary
is cached on ``Community.summary`` so the global retrieval mode
(Phase 4b.3) can return summaries directly without re-LLM-ing.

Layer note: lives in ``app/db/neo4j/`` — needs the chroma client
to fetch representative chunks. Pure orchestration; no FastAPI.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import config
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.community_summaries")


SUMMARY_PROMPT = """\
You are labelling one cluster of related entities from a personal knowledge \
base so a person can pick it out of a list of many clusters at a glance.

Entities: {entities}

Representative passages:
{passages}

Reply with exactly two lines:
Name: a specific title for this cluster — 2-6 words, Title Case, naming the \
actual subject (like a book chapter heading). Never generic filler such as \
"This community", "Various topics", "Related entities", or phrases starting \
"The theme".
Summary: 1-3 concise sentences (<=80 words total) capturing WHAT the entities \
have in common and the broader topic — avoid listing entity names, and start \
with the substance itself, never with openers like "This community revolves \
around" or "The theme centers on"."""


# Openers the LLM habitually produces despite instructions. Used to clean
# generated names/summaries and as the fallback-name derivation. Covers both
# present-tense ("relates to") and participle ("related to") forms.
_BOILERPLATE_OPENER = (
    r"^(?:the|this|a)?\s*"
    r"(?:theme|community|cluster|topic|content|summary)?\s*"
    r"(?:(?:is\s+)?(?:revolv|centr|center|focus|relat)(?:es|s|ed|ing)?\s+"
    r"(?:around|on|to)|is\s+about|covers|concerns)\s+"
)


# Async LLM caller signature: messages -> string content.
# Mirrors call_internal_llm without the response_format/temperature plumbing
# (this prompt produces freeform prose, not JSON).
LLMCaller = Callable[[list[dict[str, str]]], Awaitable[str]]


async def default_llm_caller(messages: list[dict[str, str]]) -> str:
    """Production caller wrapping call_internal_llm with the
    ``community_summary`` stage breadcrumb."""
    from core.utils.internal_llm import call_internal_llm

    return await call_internal_llm(
        messages,
        temperature=0.2,
        max_tokens=200,
        stage="community_summary",
    )


async def summarize_communities(
    driver: Any,
    chroma_client: Any,
    *,
    level: int = 0,
    top_k_entities: int = 10,
    max_communities: int | None = None,
    skip_with_existing_summary: bool = True,
    llm_caller: LLMCaller = default_llm_caller,
) -> dict[str, Any]:
    """Generate summaries for each Community at the given Leiden level.

    Caches the result on ``Community.summary`` + ``Community.summary_generated_at``.
    Set ``skip_with_existing_summary=False`` to force-refresh existing
    summaries (useful after a major corpus update).

    Returns ``{"summarised": int, "skipped_existing": int, "skipped_no_chunks": int}``.
    """
    targets = _list_summary_targets(
        driver, level=level, top_k_entities=top_k_entities,
        skip_with_existing_summary=skip_with_existing_summary,
    )
    if max_communities is not None:
        targets = targets[:max_communities]
    logger.info(
        "Community summarisation: level=%d, candidates=%d", level, len(targets),
    )

    stats = {"summarised": 0, "skipped_existing": 0, "skipped_no_chunks": 0,
             "errors": 0}

    for target in targets:
        passages = _fetch_representative_passages(chroma_client, target["entities"])
        if not passages:
            stats["skipped_no_chunks"] += 1
            continue
        try:
            name, summary = await _summarise(
                target["entities"], passages, llm_caller=llm_caller,
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            logger.exception(
                "summarise community %s failed: %s", target["community_id"], exc,
            )
            stats["errors"] += 1
            continue

        # Read the taken names fresh per community rather than snapshotting
        # once: a second generation pass (or a concurrent one) would otherwise
        # mint a name this run's stale snapshot never saw. Excluding the
        # community's own current name keeps re-summarisation from suffixing a
        # name against itself.
        taken = _existing_names(
            driver, level=level, exclude_id=target["community_id"],
        )
        name = _disambiguate_name(name, taken, target["entities"])
        _persist_summary(driver, target["community_id"], summary, name=name)
        stats["summarised"] += 1

    return stats


def _list_summary_targets(
    driver: Any,
    *,
    level: int,
    top_k_entities: int,
    skip_with_existing_summary: bool,
) -> list[dict[str, Any]]:
    """For each Community at ``level``, pull top-K entities by degree.

    Degree = number of CO_MENTIONED edges to other community members.
    Higher-degree entities are the "anchors" of the cluster — they
    discriminate community theme better than peripheral mentions.
    """
    cypher = """
    MATCH (c:Community {level: $level})
    """
    if skip_with_existing_summary:
        # `c.name IS NULL` keeps pre-name-era communities eligible so the
        # periodic batches backfill names without a forced refresh.
        cypher += " WHERE c.summary IS NULL OR c.name IS NULL"
    cypher += """
    MATCH (c)<-[:IN_COMMUNITY]-(e:Entity)
    OPTIONAL MATCH (e)-[r:CO_MENTIONED]-(peer:Entity)-[:IN_COMMUNITY]->(c)
    WITH c, e, count(r) AS degree
    ORDER BY c.id, degree DESC
    WITH c, collect({
        canonical_id: e.canonical_id, name: e.name,
        entity_type: e.entity_type, degree: degree
    })[..$top_k] AS entities
    RETURN c.id AS community_id, c.level AS level, entities
    ORDER BY c.id
    """
    with driver.session() as session:
        rows = session.run(cypher, level=level, top_k=top_k_entities)
        return [dict(r) for r in rows]


def _fetch_representative_passages(
    chroma_client: Any, entities: list[dict[str, Any]],
) -> list[str]:
    """One representative chunk per top entity (by entity name).

    Searches all domain collections; picks the highest-relevance hit
    that mentions the entity name (or a slug fragment thereof).
    Trims each passage to ~400 chars so the prompt stays tight.
    """
    passages: list[str] = []
    seen_chunks: set[str] = set()
    for ent in entities[: 6]:  # cap LLM context — 6 passages plus prompt = ~2k tokens
        name = ent["name"]
        for domain in config.DOMAINS:
            try:
                coll = chroma_client.get_collection(name=config.collection_name(domain))
            except Exception:  # noqa: BLE001
                continue
            try:
                res = coll.query(
                    query_texts=[name],
                    n_results=1,
                    include=["documents"],
                )
            except Exception:  # noqa: BLE001
                continue
            if not res["ids"] or not res["ids"][0]:
                continue
            chunk_id = res["ids"][0][0]
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            doc = (res["documents"][0][0] if res["documents"] else "") or ""
            if doc.strip():
                passages.append(doc.strip()[:400])
                break  # one passage per entity
    return passages


async def _summarise(
    entities: list[dict[str, Any]],
    passages: list[str],
    *,
    llm_caller: LLMCaller,
) -> tuple[str, str]:
    """Returns ``(name, summary)``. ``name`` may be "" when the model
    ignores the format and no usable fallback can be derived."""
    entity_descriptions = ", ".join(
        f"{e['name']} ({e['entity_type']})" for e in entities
    )
    passage_block = "\n\n---\n\n".join(passages)
    prompt = SUMMARY_PROMPT.format(
        entities=entity_descriptions, passages=passage_block,
    )
    messages = [
        {"role": "system", "content": "You are a concise topic summariser."},
        {"role": "user", "content": prompt},
    ]
    return _parse_name_summary((await llm_caller(messages)).strip())


def _parse_name_summary(raw: str) -> tuple[str, str]:
    """Split the two-line ``Name: … / Summary: …`` reply, tolerating models
    that drop either label or answer in one blob."""
    import re

    name = ""
    summary_parts: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"(?i)^name\s*:\s*(.*)$", stripped)
        if m and not name:
            name = m.group(1).strip()
            continue
        summary_parts.append(re.sub(r"(?i)^summary\s*:\s*", "", stripped))
    summary = " ".join(summary_parts).strip() or raw.strip()
    summary = re.sub(r"(?i)^name\s*:\s*", "", summary)
    name = _clean_name(name) or _clean_name(_derive_name_from_summary(summary))
    return name, summary


# Name cap: titles, not sentences — the prompt asks for 2-6 words, these
# bounds absorb model overshoot without truncating mid-word.
_NAME_MAX_WORDS = 8
_NAME_MAX_CHARS = 60


def _clean_name(name: str) -> str:
    import re

    name = name.strip().strip("\"'").strip()
    name = re.sub(_BOILERPLATE_OPENER, "", name, flags=re.IGNORECASE).strip()
    if not name or re.match(r"^\d+(\.\d+)?$", name):
        return ""
    # Word-boundary cap: names are titles, not sentences.
    words = name.split()
    if len(words) > _NAME_MAX_WORDS:
        name = " ".join(words[:_NAME_MAX_WORDS])
    if len(name) > _NAME_MAX_CHARS:
        name = name[:_NAME_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:")
    return name[0].upper() + name[1:] if name else ""


def _derive_name_from_summary(summary: str) -> str:
    """Fallback: first clause of the summary with boilerplate stripped."""
    import re

    stripped = re.sub(
        _BOILERPLATE_OPENER, "", summary.strip(), flags=re.IGNORECASE,
    )
    first = re.split(r"[.:\n,;]", stripped, maxsplit=1)[0].strip()
    return first


def _disambiguate_name(
    name: str, used_names: set[str], entities: list[dict[str, Any]],
) -> str:
    """Suffix a colliding name with its top entity so two clusters never
    share a label ("Apache Spark" vs "Apache Spark (SparkContext)")."""
    if not name or name.casefold() not in used_names:
        return name
    for ent in entities:
        ent_name = str(ent.get("name") or "").strip()
        if ent_name and ent_name.casefold() not in name.casefold():
            candidate = f"{name} ({ent_name})"
            if candidate.casefold() not in used_names:
                return candidate
    # Last resort: numeric suffix.
    for i in range(2, 100):
        candidate = f"{name} ({i})"
        if candidate.casefold() not in used_names:
            return candidate
    return name


def _existing_names(
    driver: Any, *, level: int, exclude_id: str | None = None,
) -> set[str]:
    with driver.session() as session:
        rows = session.run(
            "MATCH (c:Community {level: $level}) "
            "WHERE c.name IS NOT NULL AND c.id <> coalesce($exclude_id, '') "
            "RETURN c.name AS name",
            level=level,
            exclude_id=exclude_id,
        )
        return {
            str(r.get("name")).casefold() for r in rows if r.get("name")
        }


def _persist_summary(
    driver: Any, community_id: str, summary: str, *, name: str = "",
) -> None:
    now = utcnow_iso()
    with driver.session() as session:
        session.run(
            """
            MATCH (c:Community {id: $cid})
            SET c.summary = $summary,
                c.name = CASE WHEN $name = '' THEN c.name ELSE $name END,
                c.summary_generated_at = $now
            """,
            cid=community_id,
            summary=summary,
            name=name,
            now=now,
        )


def list_community_summaries(
    driver: Any,
    *,
    level: int = 0,
    only_with_summary: bool = True,
) -> list[dict[str, Any]]:
    """Read-back helper used by the global retrieval mode + tests."""
    cypher = "MATCH (c:Community {level: $level})"
    if only_with_summary:
        cypher += " WHERE c.summary IS NOT NULL"
    cypher += (
        " OPTIONAL MATCH (c)<-[:IN_COMMUNITY]-(e:Entity)"
        " WITH c, count(e) AS member_count"
        " RETURN c.id AS id, c.level AS level, c.name AS name,"
        "        c.summary AS summary,"
        "        c.summary_generated_at AS generated_at, member_count"
        " ORDER BY member_count DESC"
    )
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher, level=level)]
