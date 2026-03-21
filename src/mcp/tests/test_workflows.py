# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for routers/workflows.py — DAG composition and execution engine."""

import asyncio
import json
from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

from routers.workflows import (
    AVAILABLE_AGENTS,
    WORKFLOW_TEMPLATES,
    NodeType,
    RunStatus,
    Workflow,
    WorkflowCreate,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    _evaluate_condition,
    _make_chain,
    execute_workflow,
    topological_sort,
    validate_dag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(name: str, nid: str | None = None, ntype: NodeType = NodeType.AGENT, **config) -> WorkflowNode:
    return WorkflowNode(id=nid or name, type=ntype, name=name, config=config)


def _edge(src: str, tgt: str, label: str | None = None, condition: str | None = None) -> WorkflowEdge:
    return WorkflowEdge(source_id=src, target_id=tgt, label=label, condition=condition)


def _workflow(nodes: list[WorkflowNode], edges: list[WorkflowEdge], name: str = "test") -> Workflow:
    return Workflow(
        id="wf-test",
        name=name,
        nodes=nodes,
        edges=edges,
        created_at="2026-03-21T00:00:00Z",
        updated_at="2026-03-21T00:00:00Z",
    )


def _mock_redis():
    """Create a mock Redis with in-memory dict backend."""
    store: dict[str, str] = {}
    sets: dict[str, set] = defaultdict(set)
    zsets: dict[str, dict] = defaultdict(dict)
    r = MagicMock()
    r.get = lambda k: store.get(k)
    r.set = lambda k, v: store.__setitem__(k, v)
    r.setex = lambda k, ttl, v: store.__setitem__(k, v)
    r.delete = lambda k: store.pop(k, None)
    r.exists = lambda k: k in store
    r.smembers = lambda k: sets.get(k, set())
    r.sadd = lambda k, v: sets[k].add(v)
    r.srem = lambda k, v: sets[k].discard(v)
    r.zadd = lambda k, mapping: zsets[k].update(mapping)
    r.zrange = lambda k, s, e: list(zsets.get(k, {}).keys())
    r.zrevrange = lambda k, s, e: list(reversed(list(zsets.get(k, {}).keys())))
    return r


# ---------------------------------------------------------------------------
# Tests: DAG Validation
# ---------------------------------------------------------------------------


class TestDAGValidation:
    def test_valid_linear_chain(self):
        nodes = [_node("a", "a"), _node("b", "b"), _node("c", "c")]
        edges = [_edge("a", "b"), _edge("b", "c")]
        validate_dag(nodes, edges)  # Should not raise

    def test_empty_dag_is_valid(self):
        validate_dag([], [])

    def test_single_node_no_edges(self):
        validate_dag([_node("a", "a")], [])

    def test_cycle_detection(self):
        nodes = [_node("a", "a"), _node("b", "b"), _node("c", "c")]
        edges = [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")]
        with pytest.raises(Exception, match="cycle"):
            validate_dag(nodes, edges)

    def test_self_loop_detected(self):
        nodes = [_node("a", "a")]
        edges = [_edge("a", "a")]
        with pytest.raises(Exception, match="Self-loop"):
            validate_dag(nodes, edges)

    def test_unknown_source_node(self):
        nodes = [_node("a", "a")]
        edges = [_edge("missing", "a")]
        with pytest.raises(Exception, match="unknown source"):
            validate_dag(nodes, edges)

    def test_unknown_target_node(self):
        nodes = [_node("a", "a")]
        edges = [_edge("a", "missing")]
        with pytest.raises(Exception, match="unknown target"):
            validate_dag(nodes, edges)

    def test_diamond_dag_valid(self):
        """Diamond: A -> B, A -> C, B -> D, C -> D."""
        nodes = [_node("a", "a"), _node("b", "b"), _node("c", "c"), _node("d", "d")]
        edges = [_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")]
        validate_dag(nodes, edges)  # Should not raise

    def test_two_node_cycle(self):
        nodes = [_node("a", "a"), _node("b", "b")]
        edges = [_edge("a", "b"), _edge("b", "a")]
        with pytest.raises(Exception, match="cycle"):
            validate_dag(nodes, edges)


# ---------------------------------------------------------------------------
# Tests: Topological Sort
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_linear_chain_order(self):
        nodes = [_node("a", "a"), _node("b", "b"), _node("c", "c")]
        edges = [_edge("a", "b"), _edge("b", "c")]
        order = topological_sort(nodes, edges)
        assert order == ["a", "b", "c"]

    def test_diamond_order(self):
        nodes = [_node("a", "a"), _node("b", "b"), _node("c", "c"), _node("d", "d")]
        edges = [_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")]
        order = topological_sort(nodes, edges)
        assert order[0] == "a"
        assert order[-1] == "d"
        assert set(order) == {"a", "b", "c", "d"}

    def test_single_node(self):
        nodes = [_node("a", "a")]
        order = topological_sort(nodes, [])
        assert order == ["a"]

    def test_no_edges_all_roots(self):
        nodes = [_node("a", "a"), _node("b", "b")]
        order = topological_sort(nodes, [])
        assert set(order) == {"a", "b"}


# ---------------------------------------------------------------------------
# Tests: Templates
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_all_templates_exist(self):
        assert "default_query_pipeline" in WORKFLOW_TEMPLATES
        assert "quick_rag" in WORKFLOW_TEMPLATES
        assert "deep_verification" in WORKFLOW_TEMPLATES
        assert "ingest_and_curate" in WORKFLOW_TEMPLATES

    def test_default_pipeline_has_all_agents(self):
        tmpl = WORKFLOW_TEMPLATES["default_query_pipeline"]
        assert tmpl["agents"] == AVAILABLE_AGENTS
        assert len(tmpl["agents"]) == 9

    def test_quick_rag_minimal(self):
        tmpl = WORKFLOW_TEMPLATES["quick_rag"]
        assert tmpl["agents"] == ["query", "self_rag"]

    def test_make_chain_creates_nodes_and_edges(self):
        nodes, edges = _make_chain(["query", "self_rag"])
        assert len(nodes) == 2
        assert len(edges) == 1
        assert edges[0]["source_id"] == "query_0"
        assert edges[0]["target_id"] == "self_rag_1"

    def test_make_chain_single_node(self):
        nodes, edges = _make_chain(["query"])
        assert len(nodes) == 1
        assert len(edges) == 0


# ---------------------------------------------------------------------------
# Tests: Condition Evaluation
# ---------------------------------------------------------------------------


class TestConditionEvaluation:
    def test_numeric_gt(self):
        assert asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("confidence > 0.5", {"confidence": 0.8})
        )

    def test_numeric_lt(self):
        assert not asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("confidence > 0.5", {"confidence": 0.3})
        )

    def test_string_eq(self):
        assert asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("status == 'ok'", {"status": "ok"})
        )

    def test_string_neq(self):
        assert asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("status != 'error'", {"status": "ok"})
        )

    def test_missing_key_returns_false(self):
        assert not asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("missing > 0", {})
        )

    def test_invalid_expression_returns_true(self):
        assert asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("nonsense!!!", {})
        )

    def test_gte(self):
        assert asyncio.get_event_loop().run_until_complete(
            _evaluate_condition("count >= 5", {"count": 5})
        )


# ---------------------------------------------------------------------------
# Tests: CRUD Operations (mocked Redis)
# ---------------------------------------------------------------------------


class TestWorkflowCRUD:
    def test_create_workflow_model(self):
        body = WorkflowCreate(
            name="Test Flow",
            description="A test",
            nodes=[_node("query", "q"), _node("self_rag", "sr")],
            edges=[_edge("q", "sr")],
        )
        assert body.name == "Test Flow"
        assert len(body.nodes) == 2
        assert len(body.edges) == 1

    def test_workflow_roundtrip_json(self):
        wf = _workflow(
            [_node("query", "q")],
            [],
        )
        dumped = wf.model_dump_json()
        restored = Workflow.model_validate_json(dumped)
        assert restored.id == wf.id
        assert len(restored.nodes) == 1
        assert restored.nodes[0].name == "query"

    def test_workflow_with_all_node_types(self):
        nodes = [
            _node("query", "q", NodeType.AGENT),
            _node("check", "c", NodeType.CONDITION, expression="confidence > 0.5"),
            _node("parse", "p", NodeType.PARSER),
            _node("search", "t", NodeType.TOOL),
        ]
        edges = [_edge("q", "c"), _edge("c", "p"), _edge("p", "t")]
        wf = _workflow(nodes, edges)
        assert len(wf.nodes) == 4
        types = {n.type for n in wf.nodes}
        assert types == {NodeType.AGENT, NodeType.CONDITION, NodeType.PARSER, NodeType.TOOL}

    def test_workflow_run_model(self):
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            status=RunStatus.COMPLETED,
            started_at="2026-03-21T00:00:00Z",
            finished_at="2026-03-21T00:01:00Z",
            results={"q": {"status": "ok"}},
        )
        assert run.status == RunStatus.COMPLETED
        assert run.error is None

    def test_workflow_run_with_error(self):
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            status=RunStatus.FAILED,
            started_at="2026-03-21T00:00:00Z",
            error="Agent failed",
        )
        assert run.status == RunStatus.FAILED
        assert run.error == "Agent failed"


# ---------------------------------------------------------------------------
# Tests: Workflow Execution (mocked agents)
# ---------------------------------------------------------------------------


class TestWorkflowExecution:
    @patch("routers.workflows.get_redis")
    @patch("routers.workflows._execute_agent_node")
    def test_simple_chain_execution(self, mock_agent, mock_redis_fn):
        mock_redis_fn.return_value = _mock_redis()
        mock_agent.return_value = {"results": [{"text": "found"}]}

        wf = _workflow(
            [_node("query", "q"), _node("self_rag", "sr")],
            [_edge("q", "sr")],
        )

        loop = asyncio.new_event_loop()
        run = loop.run_until_complete(execute_workflow(wf, {"query": "test"}))
        loop.close()

        assert run.status == RunStatus.COMPLETED
        assert run.error is None
        assert "q" in run.results
        assert "sr" in run.results

    @patch("routers.workflows.get_redis")
    @patch("routers.workflows._execute_agent_node")
    def test_execution_failure_captured(self, mock_agent, mock_redis_fn):
        mock_redis_fn.return_value = _mock_redis()
        mock_agent.side_effect = RuntimeError("Agent crashed")

        wf = _workflow([_node("query", "q")], [])

        loop = asyncio.new_event_loop()
        run = loop.run_until_complete(execute_workflow(wf, {"query": "test"}))
        loop.close()

        assert run.status == RunStatus.FAILED
        assert "Agent crashed" in run.error

    @patch("routers.workflows.get_redis")
    @patch("routers.workflows._execute_agent_node")
    def test_condition_node_evaluation(self, mock_agent, mock_redis_fn):
        mock_redis_fn.return_value = _mock_redis()
        mock_agent.return_value = {"confidence": 0.9}

        nodes = [
            _node("query", "q"),
            _node("check", "c", NodeType.CONDITION, expression="confidence > 0.5"),
            _node("self_rag", "sr"),
        ]
        edges = [_edge("q", "c"), _edge("c", "sr")]
        wf = _workflow(nodes, edges)

        loop = asyncio.new_event_loop()
        run = loop.run_until_complete(execute_workflow(wf, {"query": "test"}))
        loop.close()

        assert run.status == RunStatus.COMPLETED
        assert run.results["c"]["passed"] is True

    @patch("routers.workflows.get_redis")
    @patch("routers.workflows._execute_agent_node")
    def test_empty_workflow_runs(self, mock_agent, mock_redis_fn):
        """A workflow with nodes but no edges runs all nodes independently."""
        mock_redis_fn.return_value = _mock_redis()
        mock_agent.return_value = {"status": "ok"}

        wf = _workflow([_node("query", "q"), _node("audit", "a")], [])

        loop = asyncio.new_event_loop()
        run = loop.run_until_complete(execute_workflow(wf, {}))
        loop.close()

        assert run.status == RunStatus.COMPLETED
        assert len(run.results) == 2


# ---------------------------------------------------------------------------
# Tests: Available Agents
# ---------------------------------------------------------------------------


class TestAvailableAgents:
    def test_agent_count(self):
        assert len(AVAILABLE_AGENTS) == 9

    def test_all_agents_present(self):
        expected = {"query", "curator", "triage", "rectify", "audit",
                    "maintenance", "hallucination", "memory", "self_rag"}
        assert set(AVAILABLE_AGENTS) == expected
