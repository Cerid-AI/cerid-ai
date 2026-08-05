# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Wikidata SPARQL adapter (Phase API.1).

Uses the Wikidata Query Service (WDQS) SPARQL endpoint:
  https://query.wikidata.org/sparql

No authentication required.  Subject to the WDQS rate limits for
anonymous requests.  Long-running SPARQL queries should be avoided
in interactive paths.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_SPARQL_URL = "https://query.wikidata.org/sparql"
_ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"


class WikidataAdapter(ExternalAPIAdapter):
    """Adapter for the Wikidata SPARQL Query Service."""

    slug = "wikidata"
    display_name = "Wikidata"
    requires_key = False
    key_env_var = None

    async def sparql(self, query: str) -> list[dict[str, Any]]:
        """Execute a SPARQL SELECT query against Wikidata.

        Parameters
        ----------
        query:
            A complete SPARQL SELECT query string.

        Returns
        -------
        list[dict]
            Each element is a ``{variable: {"type": ..., "value": ...}}``
            binding dict from the SPARQL JSON response.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        try:
            resp = await client.post(
                _SPARQL_URL,
                data={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        return data.get("results", {}).get("bindings", [])

    async def lookup(self, entity_id: str) -> dict[str, Any]:  # type: ignore[override]
        """Convenience lookup: Q-number → label, description, claims.

        Parameters
        ----------
        entity_id:
            Wikidata Q-number (e.g. ``"Q42"``).

        Returns
        -------
        dict with keys:
            ``entity_id``, ``label``, ``description``, ``claims_count``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses or transport failures.
        """
        client = await get_http_client()
        url = _ENTITY_URL.format(entity_id=entity_id)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        entities = data.get("entities", {})
        entity = entities.get(entity_id, {})
        labels = entity.get("labels", {})
        label_en = (labels.get("en") or {}).get("value", "")
        descriptions = entity.get("descriptions", {})
        desc_en = (descriptions.get("en") or {}).get("value", "")
        claims = entity.get("claims", {})
        return {
            "entity_id": entity_id,
            "label": label_en,
            "description": desc_en,
            "claims_count": len(claims),
        }

    async def health_check(self) -> bool:
        """Return True when the SPARQL endpoint is reachable."""
        client = await get_http_client()
        try:
            resp = await client.get(
                _SPARQL_URL,
                params={"query": "SELECT ?x WHERE { BIND(1 AS ?x) } LIMIT 1", "format": "json"},
            )
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
