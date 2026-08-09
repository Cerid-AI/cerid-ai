# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GitHub REST API adapter (Phase API.1).

Uses the GitHub REST API v3:
  https://api.github.com

No authentication required for public resources.  Supply
``GITHUB_TOKEN`` in the environment for higher rate limits
(5 000 req/hr authenticated vs 60 req/hr anonymous).

Cerid never proxies through its own GitHub token.  If the user
provides a ``GITHUB_TOKEN``, it is passed as a Bearer token.
"""
from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any

import httpx

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    get_http_client,
)

_BASE_URL = "https://api.github.com"


def _auth_headers() -> dict[str, str]:
    """Return authorization headers if GITHUB_TOKEN is set."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


class GitHubAdapter(ExternalAPIAdapter):
    """Adapter for the GitHub REST API.

    Works without a token at 60 requests/hour for the unauthenticated
    rate limit.  Set ``GITHUB_TOKEN`` to raise this to 5 000 req/hr.
    """

    slug = "github"
    display_name = "GitHub"
    requires_key = False  # works without; GITHUB_TOKEN raises rate limit
    key_env_var = "GITHUB_TOKEN"

    async def lookup(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        """Route to :meth:`get_repo` or :meth:`get_user` based on keyword args."""
        if "owner" in kwargs and "repo" in kwargs:
            return await self.get_repo(kwargs["owner"], kwargs["repo"])
        if "username" in kwargs:
            return await self.get_user(kwargs["username"])
        if len(args) == 2:
            return await self.get_repo(args[0], args[1])
        if len(args) == 1:
            return await self.get_user(args[0])
        raise ValueError("Pass owner+repo or username")

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        """Fetch metadata for a GitHub repository.

        Parameters
        ----------
        owner:
            GitHub organisation or username.
        repo:
            Repository name.

        Returns
        -------
        dict with keys: ``full_name``, ``description``, ``url``,
            ``stars``, ``forks``, ``language``, ``topics``,
            ``open_issues``, ``pushed_at``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses (including 404 for private repos).
        """
        client = await get_http_client()
        url = f"{_BASE_URL}/repos/{owner}/{repo}"
        headers = {"Accept": "application/vnd.github+json", **_auth_headers()}
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        return {
            "full_name": data.get("full_name", ""),
            "description": data.get("description") or "",
            "url": data.get("html_url", ""),
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "language": data.get("language"),
            "topics": data.get("topics") or [],
            "open_issues": data.get("open_issues_count", 0),
            "pushed_at": data.get("pushed_at"),
        }

    async def get_user(self, username: str) -> dict[str, Any]:
        """Fetch metadata for a GitHub user or organisation.

        Parameters
        ----------
        username:
            GitHub login handle.

        Returns
        -------
        dict with keys: ``login``, ``name``, ``bio``, ``url``,
            ``public_repos``, ``followers``, ``created_at``.

        Raises
        ------
        ExternalAPIError
            On non-2xx responses.
        """
        client = await get_http_client()
        url = f"{_BASE_URL}/users/{username}"
        headers = {"Accept": "application/vnd.github+json", **_auth_headers()}
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        data = resp.json()
        return {
            "login": data.get("login", ""),
            "name": data.get("name") or "",
            "bio": data.get("bio") or "",
            "url": data.get("html_url", ""),
            "public_repos": data.get("public_repos", 0),
            "followers": data.get("followers", 0),
            "created_at": data.get("created_at"),
        }

    async def health_check(self) -> bool:
        """Return True when the GitHub API is reachable."""
        client = await get_http_client()
        headers = {"Accept": "application/vnd.github+json", **_auth_headers()}
        try:
            resp = await client.get(f"{_BASE_URL}/zen", headers=headers)
            return resp.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        except Exception:  # noqa: BLE001
            return False
