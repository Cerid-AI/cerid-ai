# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Services — business-logic orchestration layer between routers and storage.

Modules:
  ingestion.py     — Core ingest pipeline: parse, dedup, chunk, store (ChromaDB + Neo4j)
  folder_scanner.py — Recursive directory scanner for batch ingestion
  multimodal.py    — Image/audio ingestion via vision and transcription models
"""
