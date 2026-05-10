# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure knowledge-pack helpers (manifest, registry, verifier, install state).

This package never imports ``app/`` (enforced by ``import-linter``). The
install/uninstall orchestration lives in ``app.services.knowledge_packs``;
HTTP/MCP surfacing in ``app.routers.knowledge_packs``.
"""
