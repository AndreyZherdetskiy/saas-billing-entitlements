"""Unit tests for Stripe-compatible webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from billing_platform.integrations.mock_stripe.signature import (
    InvalidWebhookSignature,
    sign_stripe_payload,
    verify_stripe_signature,
)


@pytest.fixture
def raw_payload() -> bytes:
    return b'{"id":"evt_test","type":"invoice.paid"}'


def test_invalid_signature_rejected(raw_payload: bytes) -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_stripe_signature(
            raw_payload,
            "t=1,v1=deadbeef",
            secret="whsec_test",
            tolerance_seconds=300,
        )


def test_valid_signature_accepted(raw_payload: bytes) -> None:
    secret = "whsec_test"
    header = sign_stripe_payload(raw_payload, secret)
    verify_stripe_signature(raw_payload, header, secret=secret, tolerance_seconds=300)


def test_expired_timestamp_rejected(raw_payload: bytes) -> None:
    secret = "whsec_test"
    old_ts = str(int(time.time()) - 400)
    signed_payload = f"{old_ts}.".encode() + raw_payload
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    header = f"t={old_ts},v1={digest}"
    with pytest.raises(InvalidWebhookSignature):
        verify_stripe_signature(raw_payload, header, secret=secret, tolerance_seconds=300)
