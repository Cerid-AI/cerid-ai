# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A2A client — discover and invoke other A2A-compliant agents.

Provides a lightweight async client for the Google A2A protocol so cerid
can act as both an A2A *server* (via ``routers.a2a``) and an A2A *client*
that discovers and delegates work to peer agents.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("ai-companion.a2a_client")

_DEFAULT_TIMEOUT = 30.0


class A2AClient:
    """Client for discovering and invoking A2A-compliant agents."""

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        api_key: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover(self, url: str) -> dict[str, Any]:
        """Fetch the Agent Card from ``/.well-known/agent.json``.

        Parameters
        ----------
        url:
            Base URL of the remote agent (e.g. ``http://localhost:9000``).

        Returns
        -------
        dict
            The parsed Agent Card JSON.
        """
        client = await self._get_client()
        base = url.rstrip("/")
        resp = await client.get(f"{base}/.well-known/agent.json")
        resp.raise_for_status()
        card: dict[str, Any] = resp.json()
        logger.info("Discovered A2A agent %r at %s", card.get("name"), base)
        return card

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def create_task(
        self,
        agent_url: str,
        skill_id: str,
        input_data: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a task on a remote A2A agent.

        Parameters
        ----------
        agent_url:
            Base URL of the remote agent.
        skill_id:
            The skill to invoke (from the agent card's skills list).
        input_data:
            Skill-specific input payload.
        metadata:
            Optional metadata dict forwarded to the remote agent.

        Returns
        -------
        dict
            The created task object (id, status, output, etc.).
        """
        client = await self._get_client()
        base = agent_url.rstrip("/")
        payload: dict[str, Any] = {
            "skill_id": skill_id,
            "input": input_data,
        }
        if metadata:
            payload["metadata"] = metadata

        resp = await client.post(f"{base}/a2a/tasks", json=payload)
        resp.raise_for_status()
        task: dict[str, Any] = resp.json()
        logger.info(
            "Created A2A task %s on %s (skill=%s, status=%s)",
            task.get("id"),
            base,
            skill_id,
            task.get("status"),
        )
        return task

    async def get_task(
        self, agent_url: str, task_id: str
    ) -> dict[str, Any]:
        """Get task status from a remote agent.

        Parameters
        ----------
        agent_url:
            Base URL of the remote agent.
        task_id:
            The task ID returned by ``create_task``.

        Returns
        -------
        dict
            The task object with current status and output.
        """
        client = await self._get_client()
        base = agent_url.rstrip("/")
        resp = await client.get(f"{base}/a2a/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    async def cancel_task(
        self, agent_url: str, task_id: str
    ) -> dict[str, Any]:
        """Cancel a task on a remote agent.

        Parameters
        ----------
        agent_url:
            Base URL of the remote agent.
        task_id:
            The task ID to cancel.

        Returns
        -------
        dict
            The updated task object with canceled status.
        """
        client = await self._get_client()
        base = agent_url.rstrip("/")
        resp = await client.post(f"{base}/a2a/tasks/{task_id}/cancel")
        resp.raise_for_status()
        return resp.json()

    async def get_task_history(
        self, agent_url: str, task_id: str
    ) -> dict[str, Any]:
        """Get the status transition history for a task.

        Parameters
        ----------
        agent_url:
            Base URL of the remote agent.
        task_id:
            The task ID to query.

        Returns
        -------
        dict
            ``{"transitions": [{"status": ..., "at": ...}, ...]}``
        """
        client = await self._get_client()
        base = agent_url.rstrip("/")
        resp = await client.get(f"{base}/a2a/tasks/{task_id}/history")
        resp.raise_for_status()
        return resp.json()
