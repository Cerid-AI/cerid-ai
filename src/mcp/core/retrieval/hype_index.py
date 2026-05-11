# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HyPE (Hypothetical Prompt Embeddings) — index-time question generation.

Phase R.3.  At index time, for each chunk we generate 3-5 hypothetical
questions a user might ask whose answer is contained in that chunk.  Those
questions are embedded and stored alongside the chunk so that at retrieval
time query embeddings are matched against *both* content embeddings and HyPE
embeddings, improving recall for query-phrasing that differs from the chunk
wording.

Design decisions
----------------
* Pure logic layer — no Chroma, Neo4j, or FastAPI imports.
* LLM caller and embed function are injected; tests supply stubs.
* ``generate_hype_prompts`` uses ``stage="hype_index/generate"`` so the call
  appears in structlog + Sentry scope correctly (contract test enforces this).
* Empty or blank content returns an empty list immediately (no LLM call).
* Malformed LLM responses raise ``ValueError`` — we do NOT silently return
  fewer questions than requested.  The caller decides whether to swallow or
  propagate.

Storage strategy (documented here; enforced by ``hype_indexer.py``)
--------------------------------------------------------------------
HyPE embeddings are written into a **parallel ChromaDB collection** named
``{base_collection}_hype`` (e.g. ``cerid_general_hype``).  Each document in
that collection is the generated question text; the corresponding metadata
carries ``source_chunk_id`` and ``source_artifact_id`` so dedup logic can map
HyPE hits back to their parent chunks.

*Why parallel collection over inline metadata?*
ChromaDB metadata values must be scalar (str/int/float/bool).  Storing a list
of 384-dimensional vectors as a JSON string in a metadata field is unwieldy and
bypasses the HNSW index entirely — there is no benefit over a second Chroma
collection.  A parallel collection retains ANN search, keeps the schema clean,
and lets us independently purge HyPE data without touching primary chunks.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

logger = logging.getLogger("ai-companion.hype_index")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

#: Async LLM caller: list of message dicts → raw text.
LLMCaller = Callable[[list[dict[str, str]]], Awaitable[str]]

#: Async embed function: text → embedding vector.
EmbedFn = Callable[[str], Awaitable[list[float]]]


class HyPEPrompt(BaseModel):
    """A single hypothetical question generated for a chunk.

    ``embedding`` is ``None`` until :func:`embed_hype_prompts` is called.
    ``generated_at`` is an ISO-8601 UTC timestamp set at generation time.
    ``model`` is the model identifier used for generation (informational).
    """

    question: str
    embedding: list[float] | None = None
    generated_at: str
    model: str


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_HYPE_SYSTEM = (
    "You are an expert at generating information-retrieval test queries.  "
    "Given a passage of text, produce exactly N hypothetical questions that "
    "a person might ask whose answer is directly present in the passage.  "
    "Output ONLY the questions, one per line, numbered 1. through N.  "
    "Do not reproduce the passage.  Do not add commentary."
)

_HYPE_USER_TMPL = """\
Passage:
\"\"\"
{content}
\"\"\"

Generate exactly {n} questions whose answers are found in the passage above.
Output ONLY the numbered list, one question per line.
"""


def _build_hype_messages(content: str, n: int) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _HYPE_SYSTEM},
        {"role": "user", "content": _HYPE_USER_TMPL.format(content=content, n=n)},
    ]


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------

# Matches "1. ...", "2) ...", "1 ..." at line start.
_NUMBERED_LINE = re.compile(r"^\s*\d+[.)]\s*(.+)$")


def _parse_questions(raw: str, n: int) -> list[str]:
    """Parse an LLM response into a list of ``n`` questions.

    Strips numbering, empty lines, and trailing whitespace.  Raises
    ``ValueError`` if fewer than ``n`` non-empty lines are found — the
    caller decides whether to swallow or propagate.

    Does NOT raise on *more* than n lines; extra lines are silently dropped
    so that a verbose model doesn't break the call.
    """
    questions: list[str] = []
    for line in raw.splitlines():
        m = _NUMBERED_LINE.match(line)
        if m:
            q = m.group(1).strip()
            if q:
                questions.append(q)
        elif line.strip():
            # Unnumbered non-empty line — accept it (some models skip numbers)
            questions.append(line.strip())

    if len(questions) < n:
        raise ValueError(
            f"HyPE LLM returned {len(questions)} parseable question(s); "
            f"expected at least {n}.  Raw response (first 300 chars): "
            f"{raw[:300]!r}"
        )
    return questions[:n]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_hype_prompts(
    content: str,
    *,
    n: int = 5,
    llm_caller: LLMCaller,
    model: str = "ollama/local",
) -> list[str]:
    """Generate ``n`` hypothetical questions for a chunk of content.

    Parameters
    ----------
    content:
        The chunk text.  Empty / whitespace-only returns ``[]`` without
        making an LLM call.
    n:
        Number of questions to generate.  Must be ≥ 1.
    llm_caller:
        Async callable ``(messages) → raw_text``.  Production callers wrap
        ``call_internal_llm(stage="hype_index/generate", ...)``.  Tests
        inject a stub.
    model:
        Informational model identifier stored on each :class:`HyPEPrompt`.

    Returns
    -------
    list[str]
        List of ``n`` question strings.  Returns ``[]`` for empty content.

    Raises
    ------
    ValueError
        When the LLM response cannot be parsed into ``n`` questions.
    Exception
        Propagates LLM call exceptions — caller decides to swallow or retry.
    """
    if n < 1:
        raise ValueError(f"n must be ≥ 1, got {n}")

    cleaned = (content or "").strip()
    if not cleaned:
        return []

    messages = _build_hype_messages(cleaned, n)
    raw = await llm_caller(messages)
    return _parse_questions(raw, n)


async def embed_hype_prompts(
    prompts: list[str],
    *,
    embed_fn: EmbedFn,
    model: str = "ollama/local",
) -> list[HyPEPrompt]:
    """Embed a list of questions and return fully-populated :class:`HyPEPrompt` objects.

    Parameters
    ----------
    prompts:
        Raw question strings from :func:`generate_hype_prompts`.
    embed_fn:
        Async callable ``(text) → embedding_vector``.
    model:
        Model identifier stamped onto each :class:`HyPEPrompt`.

    Returns
    -------
    list[HyPEPrompt]
        One entry per prompt, with ``embedding`` populated.  Order is
        preserved.

    Raises
    ------
    Exception
        Propagates embed_fn exceptions — caller decides to swallow or retry.
    """
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    result: list[HyPEPrompt] = []
    for question in prompts:
        embedding = await embed_fn(question)
        result.append(
            HyPEPrompt(
                question=question,
                embedding=embedding,
                generated_at=now_iso,
                model=model,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Default LLM caller (production wiring)
# ---------------------------------------------------------------------------

async def default_hype_llm_caller(messages: list[dict[str, str]]) -> str:
    """Production caller — routes through ``call_internal_llm`` with the
    ``hype_index/generate`` stage breadcrumb."""
    from core.utils.internal_llm import call_internal_llm

    return await call_internal_llm(
        messages,
        temperature=0.3,  # mild creativity for diverse questions
        max_tokens=512,
        stage="hype_index/generate",
    )


# ---------------------------------------------------------------------------
# Parallel-collection helpers
# ---------------------------------------------------------------------------

def hype_collection_name(base_collection: str) -> str:
    """Return the parallel HyPE collection name for a given base collection.

    >>> hype_collection_name("cerid_general")
    'cerid_general_hype'
    """
    return f"{base_collection}_hype"


def build_hype_doc_id(source_chunk_id: str, question_index: int) -> str:
    """Stable ChromaDB doc ID for a HyPE question.

    Format: ``{source_chunk_id}_hype_{question_index}``
    """
    return f"{source_chunk_id}_hype_{question_index}"


def build_hype_metadata(
    source_chunk_id: str,
    source_artifact_id: str,
    prompt: HyPEPrompt,
    question_index: int,
) -> dict[str, Any]:
    """Build ChromaDB-compatible metadata for a HyPE document."""
    return {
        "source_chunk_id": source_chunk_id,
        "source_artifact_id": source_artifact_id,
        "hype_question_index": question_index,
        "hype_model": prompt.model,
        "hype_generated_at": prompt.generated_at,
    }
