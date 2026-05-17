---
name: grok-preservation-guard (card)
description: Strict architectural subagent that enforces Cerid’s core invariants (core/app separation, import-linter, layering). Must be used during any significant refactoring or architecture changes.
---

**Quick Guidance:**
- Before or during any structural changes that touch core/ vs app/, agents, or data models → invoke this.
- It acts as a specialized reviewer persona.

Fork the full version from `~/Develop/.grok/skills/family/grok-preservation-guard/` when doing architecture work.