# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SDK error hierarchy and HTTP status-code mapping."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


class CeridSDKError(Exception):
    """Base exception for all Cerid SDK errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(CeridSDKError):
    """Raised on 401 Unauthorized or 403 Forbidden responses."""


class DomainRestrictedError(CeridSDKError):
    """Raised on 403 when the consumer is not allowed the requested KB domain."""


class NotFoundError(CeridSDKError):
    """Raised on 404 Not Found responses."""


class ValidationError(CeridSDKError):
    """Raised on 422 Unprocessable Entity responses."""


class RateLimitError(CeridSDKError):
    """Raised on 429 Too Many Requests responses.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the server.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code)
        self.retry_after = retry_after


class ServiceUnavailableError(CeridSDKError):
    """Raised on 503 Service Unavailable responses."""


class ProtocolVersionError(CeridSDKError):
    """Raised when the server's wire protocol is a different major version.

    Attributes:
        server_version: The protocol version the server reported.
    """

    def __init__(self, message: str, server_version: str) -> None:
        super().__init__(message)
        self.server_version = server_version


def _raise_for_status(response: httpx.Response) -> None:
    """Raise a typed :class:`CeridSDKError` for non-2xx responses."""
    if response.is_success:
        return

    status = response.status_code
    try:
        body = response.json()
    except Exception:
        body = {}

    detail = body.get("detail", response.text[:200])
    message = f"[{status}] {detail}"

    if status == 403:
        reason = None
        if isinstance(detail, dict):
            reason = detail.get("retrieval_reason")
        if reason == "consumer_domain_restricted":
            raise DomainRestrictedError(message, status)
        raise AuthenticationError(message, status)
    if status == 401:
        raise AuthenticationError(message, status)
    if status == 404:
        raise NotFoundError(message, status)
    if status == 422:
        raise ValidationError(message, status)
    if status == 429:
        retry_after_raw = response.headers.get("retry-after")
        retry_after = float(retry_after_raw) if retry_after_raw else None
        raise RateLimitError(message, status, retry_after=retry_after)
    if status == 503:
        raise ServiceUnavailableError(message, status)

    raise CeridSDKError(message, status)
