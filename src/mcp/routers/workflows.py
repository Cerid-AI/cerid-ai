# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Visual Workflow Builder — DAG composition and execution engine for agent pipelines."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict, deque
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deps import get_redis
from utils.time import utcnow_iso

_logger = logging.getLogger("ai-companion.workflows")

router = APIRouter(prefix="/workflows", tags=["workflows"])

# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------
_WF_PREFIX = "cerid:workflows"
_RUN_PREFIX = "cerid:workflow_runs"
_RUN_TTL = 60 * 60 * 24 * 7  # 7 days


def _wf_key(wf_id: str) -> str:
    return f"{_WF_PREFIX}:{wf_id}"


def _wf_index_key() -> str:
    return f"{_WF_PREFIX}:index"


def _run_key(wf_id: str, run_id: str) -> str:
    return f"{_RUN_PREFIX}:{wf_id}:{run_id}"


def _run_index_key(wf_id: str) -> str:
    return f"{_RUN_PREFIX}:{wf_id}:index"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class NodeType(str, Enum):
    AGENT = "agent"
    PARSER = "parser"
    TOOL = "tool"
    CONDITION = "condition"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NodePosition(BaseModel):
    x: float = 0
    y: float = 0


class WorkflowNode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: NodeType
    name: str = Field(..., min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    position: NodePosition = Field(default_factory=NodePosition)


class WorkflowEdge(BaseModel):
    source_id: str
    target_id: str
    label: str | None = None
    condition: str | None = None


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    nodes: list[WorkflowNode] | None = None
    edges: list[WorkflowEdge] | None = None
    enabled: bool | None = None


class Workflow(BaseModel):
    id: str
    name: str
    description: str = ""
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    created_at: str
    updated_at: str
    enabled: bool = True


class WorkflowRun(BaseModel):
    id: str
    workflow_id: str
    status: RunStatus = RunStatus.PENDING
    started_at: str
    finished_at: str | None = None
    results: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WorkflowListResponse(BaseModel):
    workflows: list[Workflow]
    total: int


# ---------------------------------------------------------------------------
# Available agents (used for node palette and execution)
# ---------------------------------------------------------------------------

AVAILABLE_AGENTS = [
    "query", "curator", "triage", "rectify", "audit",
    "maintenance", "hallucination", "memory", "self_rag",
]


# ---------------------------------------------------------------------------
# Workflow templates
# ---------------------------------------------------------------------------

def _make_chain(names: list[str], start_x: float = 100, start_y: float = 200) -> tuple[list[dict], list[dict]]:
    """Build a linear chain of agent nodes with edges."""
    nodes = []
    edges = []
    for i, name in enumerate(names):
        node_id = f"{name}_{i}"
        nodes.append({
            "id": node_id,
            "type": "agent",
            "name": name,
            "config": {},
            "position": {"x": start_x + i * 220, "y": start_y},
        })
        if i > 0:
            edges.append({
                "source_id": f"{names[i - 1]}_{i - 1}",
                "target_id": node_id,
                "label": None,
                "condition": None,
            })
    return nodes, edges


WORKFLOW_TEMPLATES = {
    "default_query_pipeline": {
        "name": "Default Query Pipeline",
        "description": "Standard 9-agent chain: query → curator → triage → rectify → audit → maintenance → hallucination → memory → self_rag",
        "agents": AVAILABLE_AGENTS,
    },
    "quick_rag": {
        "name": "Quick RAG",
        "description": "Minimal retrieval: query → self_rag",
        "agents": ["query", "self_rag"],
    },
    "deep_verification": {
        "name": "Deep Verification",
        "description": "Verification-focused: query → hallucination → self_rag → audit",
        "agents": ["query", "hallucination", "self_rag", "audit"],
    },
    "ingest_and_curate": {
        "name": "Ingest & Curate",
        "description": "Ingestion pipeline: triage → curator → maintenance",
        "agents": ["triage", "curator", "maintenance"],
    },
}


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------


def validate_dag(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> None:
    """Validate that the workflow forms a valid DAG (no cycles, valid refs)."""
    node_ids = {n.id for n in nodes}

    for edge in edges:
        if edge.source_id not in node_ids:
            raise HTTPException(400, f"Edge references unknown source node: {edge.source_id}")
        if edge.target_id not in node_ids:
            raise HTTPException(400, f"Edge references unknown target node: {edge.target_id}")
        if edge.source_id == edge.target_id:
            raise HTTPException(400, f"Self-loop detected on node: {edge.source_id}")

    # Cycle detection via topological sort (Kahn's algorithm)
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adj[edge.source_id].append(edge.target_id)
        in_degree[edge.target_id] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        nid = queue.popleft()
        visited += 1
        for neighbor in adj[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(node_ids):
        raise HTTPException(400, "Workflow contains a cycle — DAGs must be acyclic")


def topological_sort(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[str]:
    """Return node IDs in topological execution order."""
    node_ids = {n.id for n in nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adj[edge.source_id].append(edge.target_id)
        in_degree[edge.target_id] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for neighbor in adj[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return order


# ---------------------------------------------------------------------------
# Workflow execution
# ---------------------------------------------------------------------------


async def _execute_agent_node(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
    """Execute an agent node by name. Returns agent output as a dict."""
    query_text = input_data.get("query", input_data.get("text", ""))

    if name == "query":
        from agents.query_agent import lightweight_kb_query
        results = await lightweight_kb_query(query_text, top_k=input_data.get("top_k", 5))
        return {"results": results, "query": query_text}

    elif name == "curator":
        from agents.curator import curate
        result = await curate(query_text) if asyncio.iscoroutinefunction(curate) else curate(query_text)
        return result if isinstance(result, dict) else {"status": str(result)}

    elif name == "triage":
        from agents.triage import triage_file
        file_path = input_data.get("file_path", "")
        return await triage_file(file_path)

    elif name == "rectify":
        from agents.rectify import rectify
        from deps import get_chroma, get_neo4j, get_redis
        return await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
        )

    elif name == "audit":
        from agents.audit import audit
        from deps import get_redis as _get_redis
        return await audit(redis_client=_get_redis())

    elif name == "maintenance":
        from agents.maintenance import check_system_health
        from deps import get_chroma as _get_chroma
        from deps import get_neo4j as _get_neo4j
        from deps import get_redis as _get_redis2
        result = check_system_health(
            neo4j_driver=_get_neo4j(),
            chroma_client=_get_chroma(),
            redis_client=_get_redis2(),
        )
        return result if isinstance(result, dict) else {"status": str(result)}

    elif name == "hallucination":
        from agents.hallucination import check_hallucinations
        from deps import get_chroma as _gc
        from deps import get_neo4j as _gn
        from deps import get_redis as _gr
        return await check_hallucinations(
            response_text=query_text,
            conversation_id=input_data.get("conversation_id", "workflow"),
            chroma_client=_gc(),
            neo4j_driver=_gn(),
            redis_client=_gr(),
        )

    elif name == "memory":
        from agents.memory import extract_memories
        return await extract_memories(
            response_text=query_text,
            conversation_id=input_data.get("conversation_id", "workflow"),
        )

    elif name == "self_rag":
        from agents.self_rag import self_rag_enhance
        from deps import get_chroma as _gc2
        from deps import get_neo4j as _gn2
        from deps import get_redis as _gr2
        return await self_rag_enhance(
            query_result=input_data,
            response_text=query_text,
            chroma_client=_gc2(),
            neo4j_driver=_gn2(),
            redis_client=_gr2(),
        )

    return {"error": f"Unknown agent: {name}", "input": input_data}


async def _evaluate_condition(expression: str, input_data: dict[str, Any]) -> bool:
    """Evaluate a simple condition expression against input data.

    Supported: ``results_count > 0``, ``confidence >= 0.8``, ``status == 'ok'``
    """
    try:
        # Simple key-op-value parsing
        import re
        m = re.match(r"(\w+)\s*(==|!=|>=|<=|>|<)\s*(.+)", expression.strip())
        if not m:
            return True
        key, op, raw_val = m.group(1), m.group(2), m.group(3).strip().strip("'\"")

        actual = input_data.get(key)
        if actual is None:
            return False

        # Coerce to number if possible
        try:
            val = float(raw_val)
            actual = float(actual)
        except (ValueError, TypeError):
            val = raw_val  # type: ignore[assignment]

        ops = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
               ">": lambda a, b: a > b, "<": lambda a, b: a < b,
               ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
        return ops[op](actual, val)
    except Exception:
        return True


async def execute_workflow(workflow: Workflow, input_data: dict[str, Any]) -> WorkflowRun:
    """Execute a workflow DAG and store the run in Redis."""
    r = get_redis()
    run_id = str(uuid.uuid4())
    started_at = utcnow_iso()

    run = WorkflowRun(
        id=run_id, workflow_id=workflow.id,
        status=RunStatus.RUNNING, started_at=started_at,
    )

    # Store initial run state
    r.setex(_run_key(workflow.id, run_id), _RUN_TTL, run.model_dump_json())
    r.zadd(_run_index_key(workflow.id), {run_id: float(asyncio.get_event_loop().time())})

    node_map = {n.id: n for n in workflow.nodes}
    order = topological_sort(workflow.nodes, workflow.edges)

    # Build adjacency + edge map for condition evaluation
    adj: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in workflow.edges:
        adj[edge.source_id].append(edge)

    node_outputs: dict[str, Any] = {}
    results: dict[str, Any] = {}

    try:
        for nid in order:
            node = node_map[nid]

            # Gather inputs from upstream nodes
            upstream_data = dict(input_data)
            for prev_id, output in node_outputs.items():
                if isinstance(output, dict):
                    upstream_data.update(output)

            if node.type == NodeType.AGENT:
                output = await _execute_agent_node(node.name, upstream_data)
                node_outputs[nid] = output
                results[nid] = {"node": node.name, "type": "agent", "status": "completed", "output": output}

            elif node.type == NodeType.CONDITION:
                expr = node.config.get("expression", "true")
                passed = await _evaluate_condition(expr, upstream_data)
                node_outputs[nid] = {"passed": passed, **upstream_data}
                results[nid] = {"node": node.name, "type": "condition", "passed": passed}
                # If condition fails, skip downstream nodes by removing edges
                if not passed:
                    for edge in adj.get(nid, []):
                        if edge.condition and edge.condition.lower() == "true":
                            continue
                        # Mark downstream as skipped
                        results[edge.target_id] = {"node": node_map.get(edge.target_id, WorkflowNode(type=NodeType.TOOL, name="unknown")).name, "type": "skipped"}

            elif node.type in (NodeType.TOOL, NodeType.PARSER):
                # Generic passthrough for tool/parser nodes
                node_outputs[nid] = upstream_data
                results[nid] = {"node": node.name, "type": node.type.value, "status": "completed"}

        run.status = RunStatus.COMPLETED
        run.results = results

    except (KeyError, ValueError, TypeError, RuntimeError, asyncio.CancelledError) as exc:
        _logger.exception("Workflow execution failed: %s", exc)
        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.results = results

    run.finished_at = utcnow_iso()

    # Persist final run state
    r.setex(_run_key(workflow.id, run.id), _RUN_TTL, run.model_dump_json())

    return run


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=WorkflowListResponse)
async def list_workflows():
    """List all saved workflows."""
    r = get_redis()
    wf_ids = r.smembers(_wf_index_key())
    workflows: list[Workflow] = []
    for wf_id in sorted(wf_ids):
        raw = r.get(_wf_key(wf_id))
        if raw:
            workflows.append(Workflow.model_validate_json(raw))
    return WorkflowListResponse(workflows=workflows, total=len(workflows))


@router.get("/templates")
async def list_templates():
    """Return predefined workflow templates."""
    templates = []
    for key, tmpl in WORKFLOW_TEMPLATES.items():
        nodes, edges = _make_chain(list(tmpl["agents"]))
        templates.append({
            "id": key,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "nodes": nodes,
            "edges": edges,
        })
    return templates


@router.post("", response_model=Workflow, status_code=201)
async def create_workflow(body: WorkflowCreate):
    """Create a new workflow."""
    if body.nodes:
        validate_dag(body.nodes, body.edges)

    r = get_redis()
    wf_id = str(uuid.uuid4())[:12]
    now = utcnow_iso()

    wf = Workflow(
        id=wf_id,
        name=body.name,
        description=body.description,
        nodes=body.nodes,
        edges=body.edges,
        created_at=now,
        updated_at=now,
        enabled=body.enabled,
    )

    r.set(_wf_key(wf_id), wf.model_dump_json())
    r.sadd(_wf_index_key(), wf_id)
    _logger.info("Created workflow %s: %s (%d nodes)", wf_id, body.name, len(body.nodes))
    return wf


@router.get("/{workflow_id}", response_model=Workflow)
async def get_workflow(workflow_id: str):
    """Get a workflow with its nodes and edges."""
    r = get_redis()
    raw = r.get(_wf_key(workflow_id))
    if not raw:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")
    return Workflow.model_validate_json(raw)


@router.put("/{workflow_id}", response_model=Workflow)
async def update_workflow(workflow_id: str, body: WorkflowUpdate):
    """Update a workflow."""
    r = get_redis()
    raw = r.get(_wf_key(workflow_id))
    if not raw:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    wf = Workflow.model_validate_json(raw)
    updates = body.model_dump(exclude_none=True)

    # Validate updated DAG if nodes or edges changed
    new_nodes = updates.get("nodes", wf.nodes)
    new_edges = updates.get("edges", wf.edges)
    if "nodes" in updates or "edges" in updates:
        # Convert dicts back to models for validation
        node_objs = [WorkflowNode.model_validate(n) if isinstance(n, dict) else n for n in new_nodes]
        edge_objs = [WorkflowEdge.model_validate(e) if isinstance(e, dict) else e for e in new_edges]
        validate_dag(node_objs, edge_objs)

    for key, val in updates.items():
        setattr(wf, key, val)
    wf.updated_at = utcnow_iso()

    r.set(_wf_key(workflow_id), wf.model_dump_json())
    return wf


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str):
    """Delete a workflow and its run history."""
    r = get_redis()
    if not r.exists(_wf_key(workflow_id)):
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    r.delete(_wf_key(workflow_id))
    r.srem(_wf_index_key(), workflow_id)

    # Clean up run history
    run_ids = r.zrange(_run_index_key(workflow_id), 0, -1)
    for run_id in run_ids:
        r.delete(_run_key(workflow_id, run_id))
    r.delete(_run_index_key(workflow_id))

    return {"status": "deleted", "workflow_id": workflow_id}


@router.post("/{workflow_id}/run", response_model=WorkflowRun)
async def run_workflow(workflow_id: str, body: dict[str, Any] | None = None):
    """Execute a workflow (run the DAG)."""
    r = get_redis()
    raw = r.get(_wf_key(workflow_id))
    if not raw:
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    wf = Workflow.model_validate_json(raw)
    if not wf.enabled:
        raise HTTPException(400, "Workflow is disabled")
    if not wf.nodes:
        raise HTTPException(400, "Workflow has no nodes")

    input_data = body or {}
    run = await execute_workflow(wf, input_data)
    return run


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRun])
async def list_runs(workflow_id: str, limit: int = 20):
    """List runs for a workflow, most recent first."""
    r = get_redis()
    if not r.exists(_wf_key(workflow_id)):
        raise HTTPException(404, f"Workflow not found: {workflow_id}")

    run_ids = r.zrevrange(_run_index_key(workflow_id), 0, limit - 1)
    runs: list[WorkflowRun] = []
    for run_id in run_ids:
        raw = r.get(_run_key(workflow_id, run_id))
        if raw:
            runs.append(WorkflowRun.model_validate_json(raw))
    return runs
