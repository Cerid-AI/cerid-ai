# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""SAML 2.0 SSO (Enterprise ``sso_saml``).

Prefix: /auth/saml

    GET  /auth/saml/metadata  → SP metadata XML, for pasting into the IdP
    GET  /auth/saml/login     → redirect to the IdP (SP-initiated, HTTP-Redirect)
    POST /auth/saml/acs       → consume the SAMLResponse and issue a session

Registered only when ``CERID_MULTI_USER=true``, alongside ``auth.py``. SSO with
no user model to attach an identity to would be theatre: single-user mode has
exactly one operator authenticated by API key, and there is nobody for an IdP
to distinguish.

All verification lives in ``core/utils/saml.py``. This router is the plumbing
around it — configuration, replay storage, user provisioning, session issue —
and deliberately makes no security decisions of its own beyond refusing to run
unconfigured.
"""
from __future__ import annotations

import base64
import logging
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

import config.settings as settings
from app.db.neo4j.users import create_user, get_user_by_email, update_last_login
from app.deps import get_neo4j, get_redis
from config.features import CERID_JWT_ACCESS_TTL, DEFAULT_TENANT_ID
from core.utils import audit_log
from core.utils.saml import SamlConfig, SamlError, decode_saml_response, sp_metadata, verify_response

logger = logging.getLogger("ai-companion.saml_router")

UTC = timezone.utc

router = APIRouter(prefix="/auth/saml", tags=["saml"])

FEATURE_FLAG = "sso_saml"

#: Replay keys live at least this long even if the assertion's own window is
#: shorter or unreadable — a replay window that collapses to nothing is not a
#: replay defence.
_MIN_REPLAY_TTL = 300
_REPLAY_PREFIX = "cerid:saml:assertion:"


class SamlLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    user_id: str


def _require_feature() -> None:
    """Refuse when the flag is off, and refuse when it cannot be evaluated.

    Fail CLOSED on the import — the same shape as `pro_automations` and
    `audit_log`. An authentication endpoint is the last place to serve a
    request because a gate could not answer.
    """
    try:
        from config.features import is_feature_enabled
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Feature gating is unavailable; refusing the request.",
        ) from exc

    if not is_feature_enabled(FEATURE_FLAG):
        raise HTTPException(
            status_code=403,
            detail=f"{FEATURE_FLAG} feature flag is off (Enterprise tier).",
        )


def _config() -> SamlConfig:
    """Build the SP config, refusing rather than half-configuring.

    Every field is required. A partially-configured SP is the dangerous state:
    an empty IdP certificate would make every signature check fail in a way an
    operator reads as "SSO is broken", and an empty entity id would make the
    audience check compare against "" and pass for anything.
    """
    missing = [
        name
        for name, value in (
            ("CERID_SAML_SP_ENTITY_ID", settings.SAML_SP_ENTITY_ID),
            ("CERID_SAML_SP_ACS_URL", settings.SAML_SP_ACS_URL),
            ("CERID_SAML_IDP_ENTITY_ID", settings.SAML_IDP_ENTITY_ID),
            ("CERID_SAML_IDP_SSO_URL", settings.SAML_IDP_SSO_URL),
            ("CERID_SAML_IDP_X509_CERT", settings.SAML_IDP_X509_CERT),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"SAML is not configured — missing: {', '.join(missing)}",
        )
    return SamlConfig(
        sp_entity_id=settings.SAML_SP_ENTITY_ID,
        sp_acs_url=settings.SAML_SP_ACS_URL,
        idp_entity_id=settings.SAML_IDP_ENTITY_ID,
        idp_sso_url=settings.SAML_IDP_SSO_URL,
        # An env var cannot hold real newlines, so a PEM pasted into .env
        # arrives with literal backslash-n. Without this every signature check
        # fails with an unhelpful parse error.
        idp_x509_cert=settings.SAML_IDP_X509_CERT.replace("\\n", "\n"),
        clock_skew_seconds=settings.SAML_CLOCK_SKEW_SECONDS,
    )


def _seen_assertion(redis_client, assertion_id: str, ttl: int) -> bool:
    """Record the assertion and report whether it had already been recorded.

    SET NX is what makes this a check rather than a race: two simultaneous
    POSTs of the same stolen assertion cannot both find it absent.
    """
    key = f"{_REPLAY_PREFIX}{assertion_id}"
    stored = redis_client.set(key, "1", nx=True, ex=max(ttl, _MIN_REPLAY_TTL))
    return not stored


@router.get("/metadata")
async def metadata() -> Response:
    """SP metadata, for the IdP side of the handshake.

    Unauthenticated on purpose — an IdP fetches this before any session
    exists — but still behind the feature flag, and it discloses only values
    the operator configured for publication.
    """
    _require_feature()
    return Response(content=sp_metadata(_config()), media_type="application/samlmetadata+xml")


@router.get("/login")
async def login(relay_state: str = "") -> RedirectResponse:
    """Start SP-initiated login: redirect to the IdP with an AuthnRequest.

    The request is unsigned, which the published metadata declares
    (``AuthnRequestsSigned="false"``) — the security of the flow rests on
    verifying the IdP's signature on the way back, not on signing the way out.
    """
    _require_feature()
    cfg = _config()

    request_id = f"_{uuid.uuid4().hex}"
    issued = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    authn_request = (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"'
        ' xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
        f' ID="{request_id}" Version="2.0" IssueInstant="{issued}"'
        f' Destination="{cfg.idp_sso_url}"'
        ' ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"'
        f' AssertionConsumerServiceURL="{cfg.sp_acs_url}">'
        f"<saml:Issuer>{cfg.sp_entity_id}</saml:Issuer>"
        "</samlp:AuthnRequest>"
    )
    # HTTP-Redirect binding: raw-DEFLATE (no zlib header), then base64.
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    deflated = compressor.compress(authn_request.encode()) + compressor.flush()
    params = {"SAMLRequest": base64.b64encode(deflated).decode()}
    if relay_state:
        params["RelayState"] = relay_state

    separator = "&" if "?" in cfg.idp_sso_url else "?"
    return RedirectResponse(f"{cfg.idp_sso_url}{separator}{urlencode(params)}", status_code=302)


@router.post("/acs", response_model=SamlLoginResponse)
async def assertion_consumer_service(
    SAMLResponse: str = Form(...),  # noqa: N803 — the field name is fixed by the SAML spec
    RelayState: str = Form(""),  # noqa: N803
) -> SamlLoginResponse:
    """Consume a SAMLResponse and issue a session.

    Every rejection is audited as a denied `auth.saml` with the reason, and
    every success as a granted one. A run of denials on this endpoint is what
    an attack looks like, and it is the reason the audit log exists.
    """
    _require_feature()
    cfg = _config()

    # Replay protection lives in Redis, so no Redis means no replay
    # protection. Fail CLOSED: an authentication endpoint that cannot tell a
    # first use from a twentieth must not authenticate. This used to be an
    # unhandled 500, which reads as a bug rather than a refusal.
    try:
        redis_client = get_redis()
    except Exception as exc:  # noqa: BLE001 — any connectivity failure is one refusal
        audit_log.audit(
            "auth.saml", outcome="denied", detail={"reason": "replay store unavailable"}
        )
        logger.error("SAML refused: replay store unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="SSO is temporarily unavailable; refusing to authenticate without replay protection.",
        ) from exc

    try:
        xml = decode_saml_response(SAMLResponse)
        identity = verify_response(
            xml,
            cfg,
            seen_assertion=lambda aid: _seen_assertion(
                redis_client, aid, _replay_ttl(cfg)
            ),
        )
    except SamlError as exc:
        # The reason is recorded, but not returned: telling an unauthenticated
        # caller exactly which check it failed helps them pass it next time.
        audit_log.audit("auth.saml", outcome="denied", detail={"reason": str(exc)[:200]})
        logger.warning("SAML assertion rejected: %s", exc)
        raise HTTPException(status_code=401, detail="SAML authentication failed.") from exc

    email = identity.email()
    if not email:
        audit_log.audit(
            "auth.saml", outcome="denied", detail={"reason": "assertion carries no email"}
        )
        raise HTTPException(
            status_code=401,
            detail="The assertion carries no email address; configure the IdP to release one.",
        )

    driver = get_neo4j()
    user = get_user_by_email(driver, email)
    if user is None:
        # Just-in-time provisioning. The password field is deliberately
        # unusable rather than empty: an SSO user must not gain a second,
        # weaker way in through the password login route.
        user = create_user(
            driver,
            email=email,
            hashed_password="!sso-no-password",
            tenant_id=DEFAULT_TENANT_ID,
            display_name=identity.first("displayName") or email,
        )
        audit_log.audit("user.provision", target=email, detail={"via": "saml"})

    update_last_login(driver, user["id"])
    token = _build_token(user)
    audit_log.audit(
        "auth.saml",
        target=email,
        detail={"user_id": user["id"], "session_index": identity.session_index},
    )
    return SamlLoginResponse(access_token=token, email=email, user_id=user["id"])


def _replay_ttl(cfg: SamlConfig) -> int:
    """How long a consumed assertion id must be remembered.

    Its own validity window plus the skew — remembering for less would let a
    replay land while the assertion is still valid, which is the entire window
    an attacker has.
    """
    return int(cfg.skew().total_seconds()) + _MIN_REPLAY_TTL


def _build_token(user: dict) -> str:
    from app.middleware.jwt_auth import create_access_token

    now = datetime.now(UTC)
    return create_access_token(
        {
            "sub": user["id"],
            "tenant_id": user.get("tenant_id") or DEFAULT_TENANT_ID,
            "role": user.get("role") or "member",
            "iat": now,
            "exp": now + timedelta(seconds=CERID_JWT_ACCESS_TTL),
        }
    )
