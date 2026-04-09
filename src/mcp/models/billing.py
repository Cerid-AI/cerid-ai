# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Billing and subscription models — Stripe integration."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Subscription Plans
# ---------------------------------------------------------------------------
class SubscriptionTier(str, Enum):
    """Available subscription tiers."""
    COMMUNITY = "community"      # Free tier
    PRO = "pro"                  # Professional
    ENTERPRISE = "enterprise"    # Enterprise


class PlanFeature(BaseModel):
    """A feature available in a subscription plan."""
    name: str = Field(..., description="Feature name")
    description: str = Field(..., description="Feature description")
    limit: int | None = Field(None, description="Numeric limit if applicable")


class SubscriptionPlan(BaseModel):
    """A subscription plan offered by Cerid."""
    tier: SubscriptionTier
    name: str = Field(..., description="Display name (e.g., 'Cerid Pro')")
    description: str = Field(..., description="Plan description")
    price_cents: int = Field(..., description="Price in cents (e.g., 1999 = $19.99)")
    currency: str = Field(default="usd", description="ISO 4217 currency code")
    billing_period: str = Field(..., description="Billing period (monthly/yearly)")
    stripe_price_id: str = Field(..., description="Stripe price ID for this plan")
    features: list[PlanFeature] = Field(default_factory=list, description="Included features")
    trial_days: int = Field(default=14, description="Free trial duration in days")


# ---------------------------------------------------------------------------
# Checkout Session
# ---------------------------------------------------------------------------
class CheckoutSessionRequest(BaseModel):
    """Request to create a Stripe checkout session."""
    plan_id: str = Field(..., description="Stripe price ID or plan key")
    success_url: str = Field(..., description="URL to redirect to on success")
    cancel_url: str = Field(..., description="URL to redirect to on cancel")
    customer_email: str | None = Field(None, description="Pre-fill customer email")
    trial_days: int | None = Field(None, description="Override default trial days")


class CheckoutSession(BaseModel):
    """A Stripe checkout session."""
    session_id: str = Field(..., description="Stripe session ID")
    url: str = Field(..., description="Checkout URL for the user")
    expires_at: datetime = Field(..., description="Session expiration time")


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------
class SubscriptionStatus(str, Enum):
    """Subscription status."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"


class Subscription(BaseModel):
    """A user's subscription."""
    subscription_id: str = Field(..., description="Stripe subscription ID")
    customer_id: str = Field(..., description="Stripe customer ID")
    tier: SubscriptionTier = Field(..., description="Current tier")
    status: SubscriptionStatus = Field(..., description="Subscription status")
    current_period_start: datetime = Field(..., description="Current billing period start")
    current_period_end: datetime = Field(..., description="Current billing period end")
    trial_end: datetime | None = Field(None, description="Trial end date if applicable")
    canceled_at: datetime | None = Field(None, description="Cancellation date if applicable")
    cancel_at_period_end: bool = Field(default=False, description="Cancels at end of period")
    price_cents: int = Field(..., description="Current price in cents")
    currency: str = Field(default="usd", description="Billing currency")
    billing_period: str = Field(..., description="Billing period (monthly/yearly)")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubscriptionUpdateRequest(BaseModel):
    """Request to update a subscription."""
    plan_id: str | None = Field(None, description="New Stripe price ID")
    cancel_at_period_end: bool | None = Field(None, description="Cancel at end of period")
    billing_period: str | None = Field(None, description="Switch billing period (monthly/yearly)")


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
class Invoice(BaseModel):
    """A subscription invoice."""
    invoice_id: str = Field(..., description="Stripe invoice ID")
    subscription_id: str = Field(..., description="Related subscription ID")
    amount_cents: int = Field(..., description="Amount in cents")
    currency: str = Field(default="usd")
    status: str = Field(..., description="Invoice status (draft/open/paid/void/uncollectible)")
    created_at: datetime = Field(..., description="Creation timestamp")
    due_date: datetime | None = Field(None, description="Due date")
    pdf_url: str | None = Field(None, description="PDF invoice URL")
    paid_at: datetime | None = Field(None, description="Payment date")


# ---------------------------------------------------------------------------
# Webhook Events
# ---------------------------------------------------------------------------
class WebhookEvent(BaseModel):
    """Received Stripe webhook event."""
    event_id: str = Field(..., description="Stripe event ID")
    event_type: str = Field(..., description="Event type (e.g., customer.subscription.updated)")
    data: dict = Field(..., description="Event data")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Billing Portal
# ---------------------------------------------------------------------------
class BillingPortalSession(BaseModel):
    """Stripe billing portal session."""
    portal_url: str = Field(..., description="Billing portal URL")
    expires_at: datetime = Field(..., description="Session expiration time")


# ---------------------------------------------------------------------------
# Plan Listing
# ---------------------------------------------------------------------------
class AvailablePlans(BaseModel):
    """Available subscription plans for display."""
    current_tier: SubscriptionTier = Field(..., description="Current user tier")
    plans: list[SubscriptionPlan] = Field(..., description="Available plans")
    has_active_subscription: bool = Field(..., description="User has active subscription")
    subscription: Subscription | None = Field(None, description="Current subscription if active")


# ---------------------------------------------------------------------------
# Credits and Usage
# ---------------------------------------------------------------------------
class ProviderCredits(BaseModel):
    """Provider usage and credits."""
    openrouter: dict = Field(default_factory=dict, description="OpenRouter account info")
    anthropic: dict = Field(default_factory=dict, description="Anthropic account info")
    google: dict = Field(default_factory=dict, description="Google account info")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
