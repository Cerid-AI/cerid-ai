# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layout-aware ingestion pipeline (Workstream E Phase 2).

Splits the historical "parse then chunk" monolith in
``app/parsers/registry.py`` + ``utils/chunker.py`` into two cleanly-
typed stages:

  parsers/  — raw bytes → list[ParsedElement]
              Each element carries semantic metadata (page_num,
              sheet_name, cell_ref, heading_path, email_thread_id,
              element_type ∈ {Title, NarrativeText, Table, ListItem,
              EmailHeader, ...}) that downstream chunkers and
              metadata writers consume.

  chunkers/ — list[ParsedElement] → list[Chunk]
              Dispatches by element_type so XLSXRow gets row-replay,
              MarkdownSection gets header-hierarchy splitting, code
              files get tree-sitter-bounded chunks, etc.

Phase 2a ships the protocol shape — TypedDict + Protocol — so the
backward-compat shim in app/parsers/registry.py can detect old vs
new return shapes by ``parser_version`` without committing to a
specific parser library yet. Phase 2b lands the per-format parsers
once the public-corpus test fixtures are in place (locked Decision #1).
"""
