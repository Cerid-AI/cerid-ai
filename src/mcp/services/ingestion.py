# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Re-export bridge — see app/services/ingestion.py for implementation.
from app.services.ingestion import *  # noqa: F401,F403
from app.services.ingestion import cache, get_chroma, get_neo4j, get_redis, graph  # noqa: F401
