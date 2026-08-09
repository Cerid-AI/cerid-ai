# KB Curation (Grok Build)

Specialized workflow for auditing and maintaining the Cerid AI Knowledge Base.

**Typical flows:**
- Audit recent ingestion quality
- Find and resolve duplicate content in ChromaDB / Neo4j
- Validate graph integrity after schema or ingestion changes
- Review new content sources before bulk import
- Debug missing or low-quality retrieval results

**Recommended invocation:**
Use this command together with the `kb-curator` agent (defined in `.grok/agents/kb-curator.md`).

This command should:
1. Gather current stats from the KB (collection sizes, recent ingestions, error rates)
2. Identify suspicious duplicates or low-quality chunks
3. Suggest or execute targeted cleanup
4. Review changes to ingestion pipeline code (`src/mcp/core/agents/curator.py`, `src/mcp/app/services/ingestion.py`, etc.)

Strongly prefer using the dedicated `kb-curator` subagent when running this command on non-trivial tasks.
