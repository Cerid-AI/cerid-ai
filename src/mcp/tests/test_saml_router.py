# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the /auth/saml REST surface (Enterprise ``sso_saml``).

The verification core has its own suite; what is pinned here is the plumbing
that decides whether verification even runs — the tier gate, the refusal to
operate half-configured, the replay store, and that a rejected assertion is
recorded and does not tell the caller which check it failed.
"""
from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.utils import audit_log

# Reuse the signing helpers and the throwaway IdP keypair.
from tests.test_saml import IDP_CERT, _response_xml, _sign

SP_ENTITY = "https://cerid.local/saml/metadata"
ACS_URL = "https://cerid.local/auth/saml/acs"
IDP_ENTITY = "https://idp.example.com/metadata"
IDP_SSO = "https://idp.example.com/sso"


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(audit_log, "_write_failures", 0, raising=False)
    yield


@pytest.fixture(autouse=True)
def _enterprise_tier():
    from config.features import FEATURE_TIER, set_tier

    original = FEATURE_TIER
    set_tier("enterprise")
    try:
        yield
    finally:
        set_tier(original)


@pytest.fixture()
def configured(monkeypatch):
    import config.settings as settings

    monkeypatch.setattr(settings, "SAML_SP_ENTITY_ID", SP_ENTITY)
    monkeypatch.setattr(settings, "SAML_SP_ACS_URL", ACS_URL)
    monkeypatch.setattr(settings, "SAML_IDP_ENTITY_ID", IDP_ENTITY)
    monkeypatch.setattr(settings, "SAML_IDP_SSO_URL", IDP_SSO)
    monkeypatch.setattr(settings, "SAML_IDP_X509_CERT", IDP_CERT)
    monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 60)


@pytest.fixture()
def client():
    from app.routers.saml import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _post(client, xml: bytes):
    return client.post(
        "/auth/saml/acs",
        data={"SAMLResponse": base64.b64encode(xml).decode()},
    )


class TestGate:
    PATHS = ["/auth/saml/metadata", "/auth/saml/login"]

    def test_community_is_refused(self, client, configured):
        from config.features import set_tier

        set_tier("community")
        for path in self.PATHS:
            assert client.get(path).status_code == 403, path
        assert _post(client, _sign(_response_xml())).status_code == 403

    def test_pro_is_refused(self, client, configured):
        # Enterprise-only, not merely paid.
        from config.features import set_tier

        set_tier("pro")
        assert client.get("/auth/saml/metadata").status_code == 403

    def test_enterprise_is_allowed(self, client, configured):
        assert client.get("/auth/saml/metadata").status_code == 200

    def test_refuses_when_the_gate_cannot_be_evaluated(self, client, configured, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def broken(name, *args, **kwargs):
            if name == "config.features":
                raise ImportError("boom")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", broken)
        assert client.get("/auth/saml/metadata").status_code == 503


class TestConfiguration:
    def test_refuses_to_run_half_configured(self, client, monkeypatch):
        """Missing config is a 503 naming what is missing, not a broken flow.

        Half-configured is the dangerous state: a blank IdP certificate makes
        every signature fail in a way an operator reads as "SSO is broken",
        and a blank SP entity id makes the audience check compare against ""
        and pass for anything.
        """
        import config.settings as settings

        for name in (
            "SAML_SP_ENTITY_ID", "SAML_SP_ACS_URL", "SAML_IDP_ENTITY_ID",
            "SAML_IDP_SSO_URL", "SAML_IDP_X509_CERT",
        ):
            monkeypatch.setattr(settings, name, "")
        resp = client.get("/auth/saml/metadata")
        assert resp.status_code == 503
        assert "CERID_SAML_IDP_X509_CERT" in resp.json()["detail"]

    def test_names_only_the_missing_fields(self, client, configured, monkeypatch):
        import config.settings as settings

        monkeypatch.setattr(settings, "SAML_IDP_X509_CERT", "")
        detail = client.get("/auth/saml/metadata").json()["detail"]
        assert "CERID_SAML_IDP_X509_CERT" in detail
        assert "CERID_SAML_SP_ENTITY_ID" not in detail

    def test_unescapes_a_pem_pasted_into_env(self, client, monkeypatch):
        # An env var cannot hold real newlines. Without the unescape every
        # signature check fails with an unhelpful parse error.
        import config.settings as settings
        from app.routers.saml import _config

        monkeypatch.setattr(settings, "SAML_SP_ENTITY_ID", SP_ENTITY)
        monkeypatch.setattr(settings, "SAML_SP_ACS_URL", ACS_URL)
        monkeypatch.setattr(settings, "SAML_IDP_ENTITY_ID", IDP_ENTITY)
        monkeypatch.setattr(settings, "SAML_IDP_SSO_URL", IDP_SSO)
        monkeypatch.setattr(settings, "SAML_IDP_X509_CERT", IDP_CERT.replace("\n", "\\n"))
        monkeypatch.setattr(settings, "SAML_CLOCK_SKEW_SECONDS", 60)
        assert "\\n" not in _config().idp_x509_cert
        assert _config().idp_x509_cert == IDP_CERT


class TestMetadata:
    def test_serves_sp_metadata_xml(self, client, configured):
        resp = client.get("/auth/saml/metadata")
        assert resp.headers["content-type"].startswith("application/samlmetadata+xml")
        assert ACS_URL in resp.text


class TestLogin:
    def test_redirects_to_the_idp_with_a_deflated_request(self, client, configured):
        from urllib.parse import parse_qs, urlparse

        resp = client.get("/auth/saml/login", follow_redirects=False)
        assert resp.status_code == 302
        target = urlparse(resp.headers["location"])
        assert f"{target.scheme}://{target.netloc}{target.path}" == IDP_SSO

        # HTTP-Redirect binding is raw DEFLATE (no zlib header) then base64.
        # Getting that wrong produces a redirect an IdP silently rejects.
        import zlib

        raw = base64.b64decode(parse_qs(target.query)["SAMLRequest"][0])
        xml = zlib.decompress(raw, -zlib.MAX_WBITS).decode()
        assert "AuthnRequest" in xml
        assert SP_ENTITY in xml

    def test_passes_relay_state_through(self, client, configured):
        from urllib.parse import parse_qs, urlparse

        resp = client.get("/auth/saml/login?relay_state=/settings", follow_redirects=False)
        query = parse_qs(urlparse(resp.headers["location"]).query)
        assert query["RelayState"] == ["/settings"]


class TestAcs:
    """These stub the replay store: without Redis the handler refuses with 503
    before verification runs, which is the correct fail-closed behaviour and
    also means an unstubbed test would never reach the code it is aiming at."""

    @pytest.fixture(autouse=True)
    def _replay_store(self, monkeypatch):
        seen: set[str] = set()

        class _FakeRedis:
            def set(self, key, _value, nx=False, ex=None):
                if nx and key in seen:
                    return None
                seen.add(key)
                return True

        monkeypatch.setattr("app.routers.saml.get_redis", lambda: _FakeRedis())

    def test_rejects_a_bad_assertion_without_saying_which_check_failed(
        self, client, configured
    ):
        # Telling an unauthenticated caller exactly which check it failed helps
        # it pass next time.
        resp = _post(client, _response_xml().encode())  # unsigned
        assert resp.status_code == 401
        assert resp.json()["detail"] == "SAML authentication failed."
        assert "signature" not in resp.json()["detail"].lower()

    def test_records_the_rejection_with_its_reason(self, client, configured):
        _post(client, _response_xml().encode())
        records = audit_log.read()
        assert [r["action"] for r in records] == ["auth.saml"]
        assert records[0]["outcome"] == "denied"
        assert "signature" in records[0]["detail"]["reason"]

    def test_rejects_a_blank_payload(self, client, configured):
        assert client.post("/auth/saml/acs", data={"SAMLResponse": " "}).status_code == 401

    def test_refuses_to_authenticate_without_a_replay_store(
        self, client, configured, monkeypatch
    ):
        # Fail CLOSED. No Redis means no way to tell a first use of an
        # assertion from a twentieth, and an endpoint that cannot tell must not
        # authenticate. Previously an unhandled 500, which reads as a bug
        # rather than a decision.
        def down():
            raise RuntimeError("redis unreachable")

        monkeypatch.setattr("app.routers.saml.get_redis", down)
        resp = _post(client, _sign(_response_xml()))
        assert resp.status_code == 503
        assert audit_log.read()[0]["detail"]["reason"] == "replay store unavailable"
