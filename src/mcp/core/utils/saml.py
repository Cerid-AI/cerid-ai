# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""SAML 2.0 Service Provider — assertion verification (Enterprise ``sso_saml``).

This module is the security boundary. Everything an IdP sends arrives here as
attacker-influenceable XML, and the job is to decide whether it genuinely came
from the configured IdP and genuinely says what it appears to say.

**The signature is verified by signxml, not by hand.** XML-DSig has enough
sharp edges — canonicalisation, transforms, reference resolution, KeyInfo trust
— that a home-grown verifier is a well-known way to ship something that looks
finished and accepts forged assertions. ``python3-saml`` would have been the
other option; it needs the ``xmlsec1`` native library and so a Dockerfile
change, while signxml is pure Python on top of lxml + cryptography, both
already in the lock.

**Signature wrapping is the attack this file is shaped around.** The classic
break is: sign a benign assertion, then wrap a forged one around it so the
verifier checks the signed subtree and the *application* reads the forged one.
The defence is to never look at the document you were given after verifying —
only at the subtree signxml hands back as verified. :func:`verify_response`
therefore returns data extracted **exclusively** from ``verified_xml``, and
the caller never sees the original tree.

What is checked, in order, with every failure a refusal rather than a warning:

1. the XML parses, with entity resolution off (XXE / billion-laughs)
2. a signature exists — an unsigned assertion is rejected, never "trusted
   because the transport was TLS"
3. the signature verifies against the *pinned* IdP certificate
4. the status code is Success
5. the audience is us
6. the destination is our ACS URL
7. the assertion is inside its validity window, with bounded clock skew
8. the assertion ID has not been seen before (replay)

A missing check is indistinguishable from a passing one, which is why each has
its own test and each test has been watched fail.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger("ai-companion.saml")

UTC = timezone.utc

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}

STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"

#: Ceiling on the operator-set skew. A large skew widens the window in which a
#: stolen assertion still replays, so "just set it to an hour" is not offered.
MAX_CLOCK_SKEW_SECONDS = 300

#: Assertions larger than this are refused before parsing. An SP endpoint is
#: unauthenticated by definition — it is what you POST to in order to become
#: authenticated — so it must not be a place to hand the process 200 MB of XML.
MAX_RESPONSE_BYTES = 512 * 1024


class SamlError(Exception):
    """The response was not acceptable. The message is safe to log, not to
    return verbatim to the browser — it describes why verification failed."""


@dataclass(frozen=True)
class SamlConfig:
    """Everything needed to trust one IdP.

    ``idp_x509_cert`` is PEM and is *pinned*: the assertion's own KeyInfo is
    never trusted to supply the key. An SP that verifies a signature against a
    certificate the attacker embedded in the message has verified nothing.
    """

    sp_entity_id: str
    sp_acs_url: str
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    clock_skew_seconds: int = 60

    def skew(self) -> timedelta:
        return timedelta(seconds=min(max(self.clock_skew_seconds, 0), MAX_CLOCK_SKEW_SECONDS))


@dataclass
class SamlIdentity:
    """The verified subject. Every field here came out of the signed subtree."""

    name_id: str
    session_index: str | None = None
    attributes: dict[str, list[str]] = field(default_factory=dict)
    assertion_id: str = ""
    not_on_or_after: datetime | None = None

    def first(self, name: str) -> str | None:
        values = self.attributes.get(name) or []
        return values[0] if values else None

    def email(self) -> str | None:
        """Best-effort email, preferring the standard claim names.

        The NameID is the fallback because a great many IdPs are configured to
        emit the email address as the NameID and nothing else.
        """
        for key in (
            "email",
            "mail",
            "urn:oid:0.9.2342.19200300.100.1.3",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        ):
            value = self.first(key)
            if value:
                return value
        return self.name_id if "@" in self.name_id else None


def decode_saml_response(b64: str) -> bytes:
    """Base64-decode the ``SAMLResponse`` form field, with a size ceiling.

    The SAML 2.0 HTTP-POST binding mandates RFC 2045 line-wrapped base64
    (76-char lines separated by CRLF), so real IdPs send it that way. Strip
    the whitespace before decoding — ``validate=True`` rejects any character
    outside the base64 alphabet, whitespace included, so an unstripped
    wrapped payload would be misread as corrupt.
    """
    if not b64 or not b64.strip():
        raise SamlError("empty SAMLResponse")
    # Cap the ENCODED length too — decoding first would mean allocating the
    # attacker's chosen size before the check that exists to prevent it.
    if len(b64) > MAX_RESPONSE_BYTES * 2:
        raise SamlError("SAMLResponse is too large")
    stripped = re.sub(r"\s+", "", b64)
    try:
        raw = base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SamlError("SAMLResponse is not valid base64") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SamlError("SAMLResponse is too large")
    return raw


def _parse(xml: bytes) -> Any:
    """Parse with external entities and network access off.

    An SP consumes XML from outside the trust boundary; a default lxml parser
    would resolve entities and fetch DTDs, which is XXE and SSRF in one step.
    """
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SamlError(f"SAMLResponse is not well-formed XML: {exc}") from exc
    if root is None:
        raise SamlError("SAMLResponse is empty")
    return root


def _text(node: Any) -> str:
    return (node.text or "").strip() if node is not None else ""


def _parse_instant(value: str) -> datetime:
    """Parse a SAML xs:dateTime, which is always UTC with a trailing Z."""
    raw = value.strip()
    if not raw:
        raise SamlError("missing timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SamlError(f"unparseable timestamp {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _require_signature(root: Any) -> None:
    """Refuse a response carrying no signature at all.

    signxml raises on a missing signature, but the failure would arrive as a
    library exception rather than a decision this module made, and "the library
    happened to reject it" is not a check. It is also the single most important
    thing to get wrong: an SP that accepts unsigned assertions authenticates
    anyone who can reach the ACS URL.
    """
    if not root.findall(".//ds:Signature", NS):
        raise SamlError("the response carries no signature")


def verify_response(
    xml: bytes,
    config: SamlConfig,
    *,
    now: datetime | None = None,
    seen_assertion: Callable[[str], bool] | None = None,
) -> SamlIdentity:
    """Verify a SAML Response and return the subject it attests to.

    Raises :class:`SamlError` for anything short of a fully verified assertion.
    ``seen_assertion`` is asked whether an assertion ID has been consumed
    before; returning True rejects the replay.
    """
    from signxml import XMLVerifier

    now = now or datetime.now(UTC)
    root = _parse(xml)
    _require_signature(root)

    try:
        result = XMLVerifier().verify(root, x509_cert=config.idp_x509_cert)
        if isinstance(result, list):
            # A document carrying several signatures. Not a shape this SP
            # understands, and picking one of them is how you pick the
            # attacker's — the same reasoning as multiple assertions below.
            # mypy is what surfaced this: signxml's return type is
            # `VerifyResult | list[VerifyResult]`, and `.signed_xml` on the
            # list would have been an AttributeError at runtime, i.e. a 500 on
            # an attacker-chosen input rather than a refusal.
            if len(result) != 1:
                raise SamlError("the response carries multiple signatures")
            result = result[0]
        verified = result.signed_xml
    except SamlError:
        raise
    except Exception as exc:  # noqa: BLE001 — every verification failure is one refusal
        # Deliberately not narrowed to signxml's exception tree: a signature
        # that fails for a reason this code did not anticipate must still be a
        # refusal, and an unexpected exception type escaping here would become
        # a 500 that an operator could mistake for a transport problem.
        raise SamlError(f"signature verification failed: {exc}") from exc

    if verified is None:
        raise SamlError("signature verification returned nothing")

    # EVERYTHING below reads `verified`, never `root`. That is the whole
    # defence against signature wrapping: the attacker's forged assertion may
    # well still be sitting in the original document, and this never looks at
    # it again.
    assertion = _locate_assertion(verified)

    _check_status(verified, root)
    _check_conditions(assertion, config, now)
    _check_destination(verified, root, config)

    assertion_id = assertion.get("ID", "")
    if not assertion_id:
        raise SamlError("assertion has no ID, so replay cannot be prevented")
    if seen_assertion is not None and seen_assertion(assertion_id):
        raise SamlError("assertion has already been used")

    identity = _extract_identity(assertion)
    identity.assertion_id = assertion_id
    return identity


def _locate_assertion(verified: Any) -> Any:
    """The verified subtree is either the Assertion or a Response holding one."""
    tag = verified.tag
    if tag == f"{{{NS['saml']}}}Assertion":
        return verified
    assertions = verified.findall(f".//{{{NS['saml']}}}Assertion")
    if len(assertions) == 1:
        return assertions[0]
    if not assertions:
        raise SamlError("the signed content contains no assertion")
    # More than one signed assertion is not a shape this SP understands, and
    # picking the first is how you pick the attacker's.
    raise SamlError("the signed content contains multiple assertions")


def _check_status(verified: Any, root: Any) -> None:
    """Reject a non-Success status.

    The status lives on the Response, which is not necessarily the signed
    subtree — when the IdP signs only the assertion, the status is unsigned and
    an attacker could flip it either way. It is read here only to turn an
    IdP-reported failure into a clear message; a *forged* Success cannot help
    an attacker, because the assertion still had to verify.
    """
    for source in (verified, root):
        node = source.find(f".//{{{NS['samlp']}}}StatusCode")
        if node is not None:
            value = node.get("Value", "")
            if value and value != STATUS_SUCCESS:
                raise SamlError(f"IdP reported {value}")
            return


def _check_conditions(assertion: Any, config: SamlConfig, now: datetime) -> None:
    conditions = assertion.find(f"{{{NS['saml']}}}Conditions")
    if conditions is None:
        raise SamlError("assertion has no Conditions, so it never expires")

    skew = config.skew()
    not_before = conditions.get("NotBefore")
    not_on_or_after = conditions.get("NotOnOrAfter")
    if not not_on_or_after:
        raise SamlError("assertion has no NotOnOrAfter, so it never expires")

    if not_before and now + skew < _parse_instant(not_before):
        raise SamlError("assertion is not yet valid")
    if now - skew >= _parse_instant(not_on_or_after):
        raise SamlError("assertion has expired")

    restrictions = conditions.findall(f"{{{NS['saml']}}}AudienceRestriction")
    if not restrictions:
        raise SamlError("assertion has no AudienceRestriction")
    audiences = {
        _text(a)
        for r in restrictions
        for a in r.findall(f"{{{NS['saml']}}}Audience")
    }
    if config.sp_entity_id not in audiences:
        # An assertion minted for a different SP is a valid, correctly signed
        # assertion. Without this check, any SP sharing the IdP can replay its
        # users into this one.
        raise SamlError("assertion is addressed to a different audience")


def _check_destination(verified: Any, root: Any, config: SamlConfig) -> None:
    """Destination must be our ACS URL when the IdP sets it.

    Optional in the spec, so an absent Destination is not a failure; a *wrong*
    one is, because it means the response was minted for another endpoint.
    """
    for source in (verified, root):
        if source.tag == f"{{{NS['samlp']}}}Response":
            destination = source.get("Destination")
            if destination and destination.rstrip("/") != config.sp_acs_url.rstrip("/"):
                raise SamlError("response was addressed to a different endpoint")
            return


def _extract_identity(assertion: Any) -> SamlIdentity:
    name_id_node = assertion.find(
        f"{{{NS['saml']}}}Subject/{{{NS['saml']}}}NameID"
    )
    name_id = _text(name_id_node)
    if not name_id:
        raise SamlError("assertion carries no NameID")

    session_index = None
    statement = assertion.find(f"{{{NS['saml']}}}AuthnStatement")
    if statement is not None:
        session_index = statement.get("SessionIndex")

    attributes: dict[str, list[str]] = {}
    for attr in assertion.findall(
        f"{{{NS['saml']}}}AttributeStatement/{{{NS['saml']}}}Attribute"
    ):
        name = attr.get("Name") or ""
        if not name:
            continue
        values = [
            _text(v)
            for v in attr.findall(f"{{{NS['saml']}}}AttributeValue")
            if _text(v)
        ]
        if values:
            attributes.setdefault(name, []).extend(values)

    not_on_or_after = None
    conditions = assertion.find(f"{{{NS['saml']}}}Conditions")
    if conditions is not None and conditions.get("NotOnOrAfter"):
        not_on_or_after = _parse_instant(conditions.get("NotOnOrAfter"))

    return SamlIdentity(
        name_id=name_id,
        session_index=session_index,
        attributes=attributes,
        not_on_or_after=not_on_or_after,
    )


def sp_metadata(config: SamlConfig) -> str:
    """SP metadata XML, for pasting into the IdP.

    Built by hand rather than templated from user input: every value is
    attribute-escaped through lxml's serialiser.
    """
    from lxml import etree

    md = "urn:oasis:names:tc:SAML:2.0:metadata"
    root = etree.Element(f"{{{md}}}EntityDescriptor", nsmap={"md": md})
    root.set("entityID", config.sp_entity_id)

    sso = etree.SubElement(root, f"{{{md}}}SPSSODescriptor")
    sso.set("protocolSupportEnumeration", "urn:oasis:names:tc:SAML:2.0:protocol")
    # We verify the IdP's signature; we do not sign our AuthnRequests, and
    # saying otherwise in metadata makes an IdP reject every request.
    sso.set("AuthnRequestsSigned", "false")
    sso.set("WantAssertionsSigned", "true")

    nameid = etree.SubElement(sso, f"{{{md}}}NameIDFormat")
    nameid.text = "urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress"

    acs = etree.SubElement(sso, f"{{{md}}}AssertionConsumerService")
    acs.set("Binding", "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST")
    acs.set("Location", config.sp_acs_url)
    acs.set("index", "0")
    acs.set("isDefault", "true")

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()
