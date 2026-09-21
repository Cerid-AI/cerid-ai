# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The unregistered-consumer fallback must not be "every domain" (F347).

``_default`` carried ``allowed_domains: None``, which
``app.services.request_policy.build_request_context`` turns into "no domain
restriction". So an integration that forgot to register its ``X-Client-ID`` —
or typo'd it — queried the whole knowledge base, mail and iMessage included,
while ``/sdk/v1/query`` documents results as scoped by the consumer's
``allowed_domains``. Scoping was opt-in per client id; it has to be the
default, with registration the way to widen it.
"""

from __future__ import annotations

from config.settings import CONSUMER_REGISTRY

# Domains an unknown caller must never reach by default.
_SENSITIVE = ("messages", "mail", "personal", "finance", "trading", "audit")


def test_default_consumer_is_not_granted_every_domain():
    allowed = CONSUMER_REGISTRY["_default"]["allowed_domains"]
    assert allowed is not None, "None means unrestricted — the fallback must fail closed"
    assert allowed, "an empty allow-list would be indistinguishable from a broken registry"
    assert not set(allowed) & set(_SENSITIVE)


def test_default_consumer_does_not_bleed_across_domains():
    assert CONSUMER_REGISTRY["_default"]["strict_domains"] is True


def test_unregistered_client_id_resolves_to_the_closed_default():
    from app.services.request_policy import build_request_context

    ctx = build_request_context(client_id="audit-probe", private_level=0)

    assert ctx.allowed_domains is not None
    assert not set(ctx.allowed_domains) & set(_SENSITIVE)
    assert ctx.strict_domains is True


def test_registered_consumers_keep_their_declared_scope():
    """The fix must tighten only the fallback — registered consumers, including
    the GUI, keep exactly the scope the registry declares for them."""
    assert CONSUMER_REGISTRY["gui"]["allowed_domains"] is None
    assert CONSUMER_REGISTRY["cerid-finance"]["allowed_domains"] == ["finance"]
