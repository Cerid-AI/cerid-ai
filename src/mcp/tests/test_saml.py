# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the SAML SP verification core (Enterprise ``sso_saml``).

These sign real assertions with a real key and then attack them, because the
only interesting question about an SP is what it *refuses*. A verifier that
accepts everything passes any test written from the happy path, and an SP that
accepts a forged assertion is a total authentication bypass — anyone who can
reach the ACS URL becomes any user.

The signature-wrapping case is the one that matters most: sign a benign
assertion, wrap a forged one around it, and see whether the identity that comes
back is the signed one or the attacker's.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.utils.saml import (
    MAX_CLOCK_SKEW_SECONDS,
    SamlConfig,
    SamlError,
    decode_saml_response,
    sp_metadata,
    verify_response,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)

SP_ENTITY = "https://cerid.local/saml/metadata"
ACS_URL = "https://cerid.local/auth/saml/acs"
IDP_ENTITY = "https://idp.example.com/metadata"


def _keypair():
    """A throwaway RSA key + self-signed cert, generated per test module."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, cert_pem


IDP_KEY, IDP_CERT = _keypair()
OTHER_KEY, OTHER_CERT = _keypair()


def config(**overrides) -> SamlConfig:
    base = {
        "sp_entity_id": SP_ENTITY,
        "sp_acs_url": ACS_URL,
        "idp_entity_id": IDP_ENTITY,
        "idp_x509_cert": IDP_CERT,
        "idp_sso_url": "https://idp.example.com/sso",
        "clock_skew_seconds": 60,
    }
    base.update(overrides)
    return SamlConfig(**base)


def _response_xml(
    *,
    name_id: str = "alice@example.com",
    audience: str = SP_ENTITY,
    destination: str | None = ACS_URL,
    not_before: datetime | None = None,
    not_on_or_after: datetime | None = None,
    status: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
    assertion_id: str = "_assertion-1",
    with_conditions: bool = True,
    with_expiry: bool = True,
) -> str:
    nb = (not_before or NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    noa = (not_on_or_after or NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    dest = f' Destination="{destination}"' if destination else ""
    if with_conditions:
        expiry = f' NotOnOrAfter="{noa}"' if with_expiry else ""
        conditions = (
            f'<saml:Conditions NotBefore="{nb}"{expiry}>'
            f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience>"
            f"</saml:AudienceRestriction></saml:Conditions>"
        )
    else:
        conditions = ""
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"'
        ' xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
        f' ID="_response-1" Version="2.0" IssueInstant="{nb}"{dest}>'
        f"<saml:Issuer>{IDP_ENTITY}</saml:Issuer>"
        f'<samlp:Status><samlp:StatusCode Value="{status}"/></samlp:Status>'
        f'<saml:Assertion ID="{assertion_id}" Version="2.0" IssueInstant="{nb}">'
        f"<saml:Issuer>{IDP_ENTITY}</saml:Issuer>"
        f'<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:2.0:'
        f'nameid-format:emailAddress">{name_id}</saml:NameID></saml:Subject>'
        f"{conditions}"
        f'<saml:AuthnStatement AuthnInstant="{nb}" SessionIndex="_session-1">'
        "<saml:AuthnContext><saml:AuthnContextClassRef>"
        "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
        "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
        '<saml:AttributeStatement><saml:Attribute Name="email">'
        f"<saml:AttributeValue>{name_id}</saml:AttributeValue></saml:Attribute>"
        '<saml:Attribute Name="groups">'
        "<saml:AttributeValue>eng</saml:AttributeValue>"
        "<saml:AttributeValue>admin</saml:AttributeValue>"
        "</saml:Attribute></saml:AttributeStatement>"
        "</saml:Assertion></samlp:Response>"
    )


def _sign(xml: str, key_pem: str = IDP_KEY, cert_pem: str = IDP_CERT) -> bytes:
    """Sign the assertion the way a real IdP does — enveloped, over the
    Assertion element, referenced by its ID."""
    from lxml import etree
    from signxml import XMLSigner

    root = etree.fromstring(xml.encode())
    assertion = root.find("{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")
    signed = XMLSigner().sign(assertion, key=key_pem, cert=cert_pem)
    root.replace(assertion, signed)
    return etree.tostring(root)


@pytest.fixture()
def signed() -> bytes:
    return _sign(_response_xml())


class TestHappyPath:
    def test_verifies_a_correctly_signed_assertion(self, signed):
        identity = verify_response(signed, config(), now=NOW)
        assert identity.name_id == "alice@example.com"
        assert identity.assertion_id == "_assertion-1"
        assert identity.session_index == "_session-1"

    def test_extracts_multi_valued_attributes(self, signed):
        identity = verify_response(signed, config(), now=NOW)
        assert identity.attributes["groups"] == ["eng", "admin"]
        assert identity.email() == "alice@example.com"

    def test_falls_back_to_the_NameID_for_email(self):
        xml = _response_xml().replace('<saml:Attribute Name="email">', '<saml:Attribute Name="x">')
        identity = verify_response(_sign(xml), config(), now=NOW)
        assert identity.email() == "alice@example.com"


class TestSignature:
    def test_rejects_an_unsigned_response(self):
        # The single most important refusal: an SP that accepts unsigned
        # assertions authenticates anyone who can reach the ACS URL.
        with pytest.raises(SamlError, match="no signature"):
            verify_response(_response_xml().encode(), config(), now=NOW)

    def test_rejects_a_signature_from_the_wrong_key(self):
        # A correctly-formed assertion signed by someone who is not the IdP.
        forged = _sign(_response_xml(), key_pem=OTHER_KEY, cert_pem=OTHER_CERT)
        with pytest.raises(SamlError, match="signature verification failed"):
            verify_response(forged, config(), now=NOW)

    def test_rejects_content_changed_after_signing(self, signed):
        tampered = signed.replace(b"alice@example.com", b"admin@example.com")
        with pytest.raises(SamlError, match="signature verification failed"):
            verify_response(tampered, config(), now=NOW)

    def test_does_not_trust_a_certificate_carried_in_the_message(self):
        # The forged assertion embeds its own cert in KeyInfo. Verifying
        # against THAT rather than the pinned one verifies nothing at all.
        forged = _sign(_response_xml(name_id="attacker@evil.com"),
                       key_pem=OTHER_KEY, cert_pem=OTHER_CERT)
        assert b"X509Certificate" in forged  # the attacker's cert really is in there
        with pytest.raises(SamlError):
            verify_response(forged, config(), now=NOW)


def _wrapped(victim: str = "alice@example.com", attacker: str = "attacker@evil.com") -> bytes:
    """A signature-wrapping payload: the legitimately signed assertion kept
    intact so its signature still verifies, with a forged one inserted FIRST —
    where a naive ``.//Assertion`` search reaches it."""
    from lxml import etree

    root = etree.fromstring(_sign(_response_xml(name_id=victim)))
    forged = etree.fromstring(
        _response_xml(name_id=attacker, assertion_id="_assertion-evil").encode()
    ).find("{urn:oasis:names:tc:SAML:2.0:assertion}Assertion")
    root.insert(0, forged)
    return etree.tostring(root)


class TestSignatureWrapping:
    def test_returns_the_signed_subject_from_a_wrapped_document(self):
        """The classic XSW break, stated as the one outcome that discriminates.

        A wrapped document must verify and yield **alice** — the subject that
        was actually signed. Both wrong implementations are excluded: reading
        the original tree instead of the verified subtree makes this raise
        (two assertions in the document), and reading the original tree while
        picking the first assertion returns the attacker.

        Two earlier versions of this test were vacuous. The first allowed
        "raises OR returns alice", so pointing the extractor at the original
        tree — the actual bug — still passed, because the forgery only changed
        which error came out. The second asserted a refusal that a correct
        implementation never produces here: with assertion-level signing the
        verified subtree IS the assertion, so the multiple-assertion branch is
        never reached. A test that tolerates two outcomes cannot tell them
        apart, and a test asserting the wrong one fails against working code.
        """
        identity = verify_response(_wrapped(), config(), now=NOW)
        assert identity.name_id == "alice@example.com", (
            "signature wrapping succeeded — the forged subject was returned"
        )
        assert identity.assertion_id == "_assertion-1"

    def test_the_identity_comes_from_the_signed_subtree(self):
        # Single, unambiguous assertion: everything returned must match what
        # was signed, field for field.
        identity = verify_response(_sign(_response_xml()), config(), now=NOW)
        assert (identity.name_id, identity.assertion_id) == ("alice@example.com", "_assertion-1")


class TestConditions:
    def test_rejects_an_expired_assertion(self):
        with pytest.raises(SamlError, match="expired"):
            verify_response(
                _sign(_response_xml(not_on_or_after=NOW - timedelta(hours=1))),
                config(), now=NOW,
            )

    def test_rejects_an_assertion_that_is_not_yet_valid(self):
        with pytest.raises(SamlError, match="not yet valid"):
            verify_response(
                _sign(_response_xml(not_before=NOW + timedelta(hours=1))),
                config(), now=NOW,
            )

    def test_allows_a_small_clock_difference(self):
        # 30s in the future, 60s of configured skew.
        identity = verify_response(
            _sign(_response_xml(not_before=NOW + timedelta(seconds=30))),
            config(), now=NOW,
        )
        assert identity.name_id == "alice@example.com"

    def test_caps_the_configured_skew(self):
        # An hour of skew is an hour in which a stolen assertion still replays.
        assert config(clock_skew_seconds=99999).skew().total_seconds() == MAX_CLOCK_SKEW_SECONDS
        with pytest.raises(SamlError, match="expired"):
            verify_response(
                _sign(_response_xml(not_on_or_after=NOW - timedelta(hours=1))),
                config(clock_skew_seconds=99999), now=NOW,
            )

    def test_rejects_an_assertion_with_no_expiry(self):
        with pytest.raises(SamlError, match="never expires"):
            verify_response(_sign(_response_xml(with_expiry=False)), config(), now=NOW)

    def test_rejects_an_assertion_with_no_conditions_at_all(self):
        with pytest.raises(SamlError, match="never expires"):
            verify_response(_sign(_response_xml(with_conditions=False)), config(), now=NOW)


class TestAudience:
    def test_rejects_an_assertion_minted_for_another_sp(self):
        # A correctly signed, in-date assertion for a DIFFERENT service. Without
        # this check any SP sharing the IdP can replay its users into this one.
        with pytest.raises(SamlError, match="different audience"):
            verify_response(
                _sign(_response_xml(audience="https://other-service.example.com")),
                config(), now=NOW,
            )


class TestDestination:
    def test_rejects_a_response_addressed_elsewhere(self):
        with pytest.raises(SamlError, match="different endpoint"):
            verify_response(
                _sign(_response_xml(destination="https://elsewhere.example.com/acs")),
                config(), now=NOW,
            )

    def test_tolerates_an_absent_destination(self):
        # Optional in the spec. Absent is not a failure; wrong is.
        identity = verify_response(_sign(_response_xml(destination=None)), config(), now=NOW)
        assert identity.name_id == "alice@example.com"


class TestStatus:
    def test_rejects_a_non_success_status(self):
        with pytest.raises(SamlError, match="AuthnFailed"):
            verify_response(
                _sign(_response_xml(status="urn:oasis:names:tc:SAML:2.0:status:AuthnFailed")),
                config(), now=NOW,
            )


class TestReplay:
    def test_rejects_an_assertion_that_has_already_been_used(self, signed):
        with pytest.raises(SamlError, match="already been used"):
            verify_response(signed, config(), now=NOW, seen_assertion=lambda _id: True)

    def test_accepts_an_assertion_not_seen_before(self, signed):
        seen: set[str] = set()
        identity = verify_response(signed, config(), now=NOW, seen_assertion=lambda i: i in seen)
        assert identity.assertion_id == "_assertion-1"


class TestParsing:
    def test_rejects_a_response_with_no_assertion_id(self):
        # Without an ID there is nothing to record, so replay cannot be stopped.
        with pytest.raises(SamlError):
            verify_response(_sign(_response_xml(assertion_id="")), config(), now=NOW)

    def test_does_not_expand_external_entities(self, tmp_path):
        """XXE: a default lxml parser reads the file and inlines it.

        Aimed at ``_parse`` rather than ``verify_response``, and asserting the
        file contents are ABSENT rather than that something raised. Routed
        through the full pipeline this passed with entity resolution turned
        back ON — the payload is unsigned, so it was refused for having no
        signature and the parser setting was never reached. "It raised" is not
        the guarantee; "the secret did not come back" is.
        """
        from core.utils.saml import _parse

        secret = tmp_path / "secret.txt"
        secret.write_text("SUPER-SECRET-CONTENT")
        xxe = (
            '<?xml version="1.0"?>'
            f'<!DOCTYPE r [<!ENTITY xxe SYSTEM "file://{secret}">]>'
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">'
            "&xxe;</samlp:Response>"
        ).encode()

        try:
            root = _parse(xxe)
        except SamlError:
            return  # refusing the document outright also keeps the file out
        assert "SUPER-SECRET-CONTENT" not in (root.text or ""), "XXE: the file was inlined"

    def test_an_entity_bearing_response_still_fails_verification(self):
        xxe = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol">'
            "&xxe;</samlp:Response>"
        ).encode()
        with pytest.raises(SamlError):
            verify_response(xxe, config(), now=NOW)

    def test_rejects_malformed_xml(self):
        with pytest.raises(SamlError, match="well-formed"):
            verify_response(b"<not xml", config(), now=NOW)


class TestDecoding:
    def test_decodes_a_base64_payload(self):
        import base64

        assert decode_saml_response(base64.b64encode(b"<x/>").decode()) == b"<x/>"

    def test_rejects_an_empty_payload(self):
        with pytest.raises(SamlError, match="empty"):
            decode_saml_response("")

    def test_rejects_non_base64(self):
        with pytest.raises(SamlError, match="base64"):
            decode_saml_response("!!!! not base64 !!!!")

    def test_decodes_rfc2045_line_wrapped_base64(self):
        # The SAML 2.0 HTTP-POST binding mandates RFC 2045 line wrapping
        # (76-char lines, CRLF-separated) — real IdPs send it this way.
        import base64

        payload = b"<samlp:Response>" + b"x" * 100 + b"</samlp:Response>"
        encoded = base64.b64encode(payload).decode()
        wrapped = "\r\n".join(
            encoded[i : i + 76] for i in range(0, len(encoded), 76)
        )
        assert decode_saml_response(wrapped) == payload

    def test_rejects_an_oversized_payload_before_decoding_it(self):
        # The ACS endpoint is unauthenticated by definition — it is what you
        # POST to in order to become authenticated — so it must not be a place
        # to hand the process an arbitrary allocation.
        with pytest.raises(SamlError, match="too large"):
            decode_saml_response("A" * (512 * 1024 * 2 + 4))


class TestMetadata:
    def test_advertises_the_acs_url_and_entity_id(self):
        xml = sp_metadata(config())
        assert SP_ENTITY in xml
        assert ACS_URL in xml
        assert 'WantAssertionsSigned="true"' in xml

    def test_escapes_values_rather_than_interpolating_them(self):
        xml = sp_metadata(config(sp_entity_id='https://x/?a=1&b="2"'))
        assert "&amp;" in xml
        assert 'entityID="https://x/?a=1&amp;b=&quot;2&quot;"' in xml
