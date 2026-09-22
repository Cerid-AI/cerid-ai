// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Compiled copies of the TypeScript snippets published in README.md and
 * docs/SDK_GUIDE.md. `npm run typecheck` compiles this file, and
 * doc-snippets.test.ts asserts the markdown still matches it line for line —
 * so a snippet that calls a method that doesn't exist, or passes a field the
 * request type doesn't declare, fails CI instead of a new user's build.
 *
 * Each function takes the client so the snippet body stays verbatim; the
 * construction line is asserted separately by the test.
 */
import { CeridClient, isMemoryExtractAccepted } from "../src/index.js";

export async function readmeUsage(cerid: CeridClient): Promise<void> {
  const answer = await cerid.kb.query({ query: 'what did I decide about the storage layout?' })
  console.log(answer.context)
  for (const hit of answer.results) {
    console.log(hit.content, hit.relevance)
  }
}

export async function guideQuickstart(client: CeridClient): Promise<void> {
  // Query the knowledge base (domains is a list; the field is top_k, matching
  // the wire contract — there is no camelCase alias)
  const result = await client.kb.query({ query: "circuit breaker pattern", domains: ["coding"], top_k: 5 });
  console.log(result.results[0].content);

  // Verify claims — conversation_id is required
  const check = await client.verify.check({
    response_text: "Redis defaults to port 6380.",
    conversation_id: "demo",
  });
  console.log(check.summary.overall_confidence, check.nli_skipped);

  // Extract memories; a queued server answers 202 with a job to poll
  const extracted = await client.memory.extract({
    response_text: "I prefer dark mode.",
    conversation_id: "demo",
  });
  if (isMemoryExtractAccepted(extracted)) {
    const job = await client.memory.getJob(extracted.job_id);
    console.log(job.status);
  } else {
    console.log(extracted.memories_stored, "memories stored");
  }

  // Check health
  const health = await client.system.health();
  console.log(health.version, health.services);

  // Ingest content with provenance metadata (any domain in your consumer's grant works)
  const resp = await client.kb.ingest({
    content: "PostgreSQL uses MVCC for concurrency.",
    domain: "databases",
    metadata: { title: "MVCC note", provenance: "design_review" },
  });
  console.log(resp.artifact_id, resp.chunks);
}

export async function guideKeepWebOut(client: CeridClient): Promise<void> {
  await client.kb.query({
    query: "net worth and safe to spend", domains: ["finance"], strict_domains: true,
    context_sources: { kb: true, memory: true, external: false },
  });
}
