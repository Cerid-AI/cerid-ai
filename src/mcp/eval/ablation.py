# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Ablation study scaffold for RAG features.

Runs query sets against different feature toggle configurations to measure
the impact of each RAG enhancement (adaptive retrieval, MMR diversity,
late interaction, etc.) on latency and result quality.

Usage (offline, not in query pipeline):

    from eval.ablation import run_ablation, PRESET_CONFIGS
    results = await run_ablation(queries, configs=PRESET_CONFIGS)
    print(results.to_string())
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from errors import CeridError

logger = logging.getLogger("ai-companion")

# RAG toggles relevant for ablation studies
_RAG_TOGGLES = [
    "enable_adaptive_retrieval",
    "enable_query_decomposition",
    "enable_mmr_diversity",
    "enable_intelligent_assembly",
    "enable_late_interaction",
    "enable_semantic_cache",
    "enable_self_rag",
]


@dataclass
class AblationConfig:
    """A feature toggle configuration for an ablation run."""

    name: str
    toggles: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Fill missing toggles with False (baseline behavior)
        for toggle in _RAG_TOGGLES:
            self.toggles.setdefault(toggle, False)


# ---------------------------------------------------------------------------
# Preset configurations
# ---------------------------------------------------------------------------

PRESET_CONFIGS: list[AblationConfig] = [
    AblationConfig(
        name="baseline",
        toggles={t: False for t in _RAG_TOGGLES},
    ),
    AblationConfig(
        name="full",
        toggles={t: True for t in _RAG_TOGGLES},
    ),
    # Individual feature ablation — each enables one feature on top of baseline
    *(
        AblationConfig(
            name=f"only_{toggle.removeprefix('enable_')}",
            toggles={t: (t == toggle) for t in _RAG_TOGGLES},
        )
        for toggle in _RAG_TOGGLES
    ),
]


@dataclass
class AblationResult:
    """Result of a single query under a single config."""

    config_name: str
    query: str
    latency_s: float
    result_count: int
    answer_snippet: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    ragas_scores: dict[str, float] = field(default_factory=dict)


async def _run_single(
    query: str,
    config: AblationConfig,
    chroma_client: Any,
    neo4j_driver: Any,
    redis_client: Any,
    *,
    run_ragas: bool = False,
) -> AblationResult:
    """Run a single query under a specific toggle configuration."""
    # Snapshot current state
    from config.features import FEATURE_TOGGLES
    from utils.features import set_toggle
    saved = {k: v for k, v in FEATURE_TOGGLES.items() if k in _RAG_TOGGLES}

    try:
        # Apply ablation config
        for toggle_name, value in config.toggles.items():
            set_toggle(toggle_name, value)

        from agents.query_agent import agent_query

        t0 = time.monotonic()
        result = await agent_query(
            query=query,
            chroma_client=chroma_client,
            redis_client=redis_client,
            neo4j_driver=neo4j_driver,
            debug_timing=True,
        )
        elapsed = time.monotonic() - t0

        answer = result.get("answer", "")
        timings = result.get("_timings", {})
        contexts = [
            a.get("content", "") for a in result.get("artifacts", [])
        ]

        ragas_scores: dict[str, float] = {}
        if run_ragas and answer and contexts:
            try:
                from eval.ragas_metrics import evaluate_all

                metrics = await evaluate_all(query, answer, contexts)
                ragas_scores = {k: v.score for k, v in metrics.items()}
            except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                logger.warning("RAGAS eval failed for %s/%s: %s", config.name, query[:30], e)

        return AblationResult(
            config_name=config.name,
            query=query,
            latency_s=round(elapsed, 4),
            result_count=len(result.get("artifacts", [])),
            answer_snippet=answer[:100],
            timings=timings,
            ragas_scores=ragas_scores,
        )
    finally:
        # Restore original toggles
        for toggle_name, value in saved.items():
            set_toggle(toggle_name, value)


async def run_ablation(
    queries: list[str],
    configs: list[AblationConfig] | None = None,
    *,
    chroma_client: Any = None,
    neo4j_driver: Any = None,
    redis_client: Any = None,
    run_ragas: bool = False,
) -> list[AblationResult]:
    """Run queries across toggle configurations and collect results.

    Args:
        queries: List of query strings to test.
        configs: Toggle configurations. Defaults to PRESET_CONFIGS.
        chroma_client: ChromaDB client. Injected from caller.
        neo4j_driver: Neo4j driver. Injected from caller.
        redis_client: Redis client. Injected from caller.
        run_ragas: If True, run RAGAS metrics on each result (slower).

    Returns:
        List of AblationResult dataclass instances.
    """
    if configs is None:
        configs = PRESET_CONFIGS

    results: list[AblationResult] = []
    total = len(queries) * len(configs)
    done = 0

    for config in configs:
        for query in queries:
            try:
                result = await _run_single(
                    query=query,
                    config=config,
                    chroma_client=chroma_client,
                    neo4j_driver=neo4j_driver,
                    redis_client=redis_client,
                    run_ragas=run_ragas,
                )
                results.append(result)
            except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                logger.error("Ablation failed for %s/%s: %s", config.name, query[:30], e)
                results.append(AblationResult(
                    config_name=config.name,
                    query=query,
                    latency_s=0.0,
                    result_count=0,
                    answer_snippet=f"ERROR: {e}",
                ))
            done += 1
            if done % 5 == 0:
                logger.info("Ablation progress: %d/%d", done, total)

    return results


def results_to_table(results: list[AblationResult]) -> list[dict[str, Any]]:
    """Convert ablation results to a list of flat dicts for tabular display."""
    rows = []
    for r in results:
        row: dict[str, Any] = {
            "config": r.config_name,
            "query": r.query[:50],
            "latency_s": r.latency_s,
            "result_count": r.result_count,
        }
        for k, v in r.timings.items():
            row[f"t_{k}"] = v
        for k, v in r.ragas_scores.items():
            row[f"ragas_{k}"] = v
        rows.append(row)
    return rows
