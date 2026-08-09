# Quenchforge / Local Inference Management (Grok Build)

Inspect and manage local model routing via Quenchforge for the Cerid AI stack.

**Useful subcommands / behaviors:**
- `status`: Show which models are currently routed locally vs cloud (Quenchforge vs grok.com / Anthropic)
- `gpu`: Check GPU availability, VRAM usage, and running inference processes on this machine
- `models`: List available local models and their quantization
- `route <task>`: Recommend best local vs cloud model for a given task type (coding, embedding, long-context, vision, etc.)
- `logs`: Tail Quenchforge / inference server logs

**Context for Cerid:**
Many parts of the system (especially RAG, embeddings via contextplus, and some agent loops) can benefit from local models running through Quenchforge on the Mac Pro's GPUs.

This command helps the agent (and you) make smart decisions about when to use expensive cloud models vs fast local ones.

Update this command over time as the Quenchforge setup evolves.
