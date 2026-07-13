// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Static fallback for the workflow node-type catalog served by
 * GET /workflows/node-types. The editor fetches the server catalog on mount
 * and falls back to this copy when the request fails, so node tooltips and
 * the detail panel always have purpose text to show. Keep the copy aligned
 * with NODE_TYPE_CATALOG / AGENT_CATALOG in src/mcp/app/routers/workflows.py.
 */
import type { WorkflowNode } from "@/lib/types"
import type { WorkflowNodeCatalog, WorkflowNodeTypeInfo } from "@/lib/api/workflows"

export const FALLBACK_NODE_CATALOG: WorkflowNodeCatalog = {
  node_types: [
    {
      type: "agent",
      label: "Agent",
      description: "Runs one of Cerid's built-in agents as a pipeline step. The node's name selects which agent executes.",
      inputs: "The workflow input merged with every upstream node's output (query, results, confidence, ...).",
      outputs: "The agent's result fields, passed downstream to connected nodes.",
      config_schema_summary: null,
    },
    {
      type: "parser",
      label: "Parser",
      description: "Marks a parsing step in the pipeline. Currently passes upstream data through unchanged.",
      inputs: "Upstream node outputs merged with the workflow input.",
      outputs: "The same data, unchanged.",
      config_schema_summary: null,
    },
    {
      type: "tool",
      label: "Tool",
      description: "Marks an external tool step in the pipeline. Currently passes upstream data through unchanged.",
      inputs: "Upstream node outputs merged with the workflow input.",
      outputs: "The same data, unchanged.",
      config_schema_summary: null,
    },
    {
      type: "condition",
      label: "Condition",
      description: "Evaluates a comparison expression against the data flowing in. When the expression is false, downstream nodes are skipped.",
      inputs: "Upstream node outputs merged with the workflow input.",
      outputs: "A passed flag plus the unchanged upstream data.",
      config_schema_summary: "expression — a comparison of one field against a value, e.g. confidence > 0.5 (operators: == != > < >= <=).",
    },
  ],
  agents: [
    { name: "query", description: "Retrieves the most relevant knowledge-base entries for the input query.", inputs: "query (text), top_k (optional, default 5)", outputs: "results (matching entries), query" },
    { name: "curator", description: "Curates knowledge related to the query — deduplication, consistency, and quality checks.", inputs: "query (text)", outputs: "curation result" },
    { name: "triage", description: "Classifies an incoming file and routes it to the right ingestion path.", inputs: "file_path", outputs: "triage classification" },
    { name: "rectify", description: "Repairs inconsistencies between the vector store and the knowledge graph.", inputs: "none (operates on stored knowledge)", outputs: "rectification report" },
    { name: "audit", description: "Audits recent system activity and knowledge changes for anomalies.", inputs: "none (operates on stored state)", outputs: "audit report" },
    { name: "maintenance", description: "Runs system health checks across the graph, vector, and cache stores.", inputs: "none (operates on live services)", outputs: "health summary" },
    { name: "hallucination", description: "Checks response text against stored knowledge for unsupported claims.", inputs: "query (treated as the text to verify), conversation_id (optional)", outputs: "verification result" },
    { name: "memory", description: "Extracts durable memories from the text into episodic storage.", inputs: "query (treated as the text to mine), conversation_id (optional)", outputs: "results (extracted memories)" },
    { name: "self_rag", description: "Re-evaluates and improves a response with retrieval-augmented self-critique.", inputs: "query (text) plus upstream retrieval results", outputs: "enhanced response" },
  ],
}

export interface NodeDescription {
  /** Human label for the node's type, e.g. "Agent". */
  typeLabel: string
  /** One-line purpose — agent-specific when the agent is known. */
  purpose: string
  inputs: string
  outputs: string
  configSummary: string | null
}

/** Resolve display metadata for a node, preferring agent-specific copy. */
export function describeNode(
  node: Pick<WorkflowNode, "type" | "name">,
  catalog: WorkflowNodeCatalog = FALLBACK_NODE_CATALOG,
): NodeDescription {
  const typeInfo: WorkflowNodeTypeInfo | undefined =
    catalog.node_types.find((t) => t.type === node.type) ??
    FALLBACK_NODE_CATALOG.node_types.find((t) => t.type === node.type)
  const agentInfo =
    node.type === "agent"
      ? (catalog.agents.find((a) => a.name === node.name) ??
        FALLBACK_NODE_CATALOG.agents.find((a) => a.name === node.name))
      : undefined

  return {
    typeLabel: typeInfo?.label ?? node.type,
    purpose: agentInfo?.description ?? typeInfo?.description ?? "No description available for this node type.",
    inputs: agentInfo?.inputs ?? typeInfo?.inputs ?? "Upstream node outputs.",
    outputs: agentInfo?.outputs ?? typeInfo?.outputs ?? "Passed downstream unchanged.",
    configSummary: typeInfo?.config_schema_summary ?? null,
  }
}
