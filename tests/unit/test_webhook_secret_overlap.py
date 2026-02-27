"""Unit tests for webhook secret overlap rotation (current + previous)."""

from __future__ import annotations

import time

import pytest

from billing_platform.integrations.mock_stripe.signature import (
    InvalidWebhookSignature,
    sign_stripe_payload,
    verify_stripe_signature,
)

CURRENT_SECRET = "whsec_current"
PREVIOUS_SECRET = "whsec_previous"


@pytest.fixture
def raw_payload() -> bytes:
    return b'{"id":"evt_overlap","type":"invoice.paid"}'


def test_previous_secret_accepted_when_previous_configured(raw_payload: bytes) -> None:
    header = sign_stripe_payload(raw_payload, PREVIOUS_SECRET)
    verify_stripe_signature(
        raw_payload,
        header,
        secret=CURRENT_SECRET,
        previous_secret=PREVIOUS_SECRET,
        tolerance_seconds=300,
    )


def test_previous_secret_rejected_without_overlap_config(raw_payload: bytes) -> None:
    header = sign_stripe_payload(raw_payload, PREVIOUS_SECRET)
    with pytest.raises(InvalidWebhookSignature, match="signature mismatch"):
        verify_stripe_signature(
            raw_payload,
            header,
            secret=CURRENT_SECRET,
            previous_secret=None,
            tolerance_seconds=300,
        )


def test_invalid_signature_rejected_for_both_secrets(raw_payload: bytes) -> None:
    ts = str(int(time.time()))
    header = f"t={ts},v1=deadbeef"
    with pytest.raises(InvalidWebhookSignature, match="signature mismatch"):
        verify_stripe_signature(
            raw_payload,
            header,
            secret=CURRENT_SECRET,
            previous_secret=PREVIOUS_SECRET,
            tolerance_seconds=300,
        )


def test_empty_previous_means_current_only(raw_payload: bytes) -> None:
    header_current = sign_stripe_payload(raw_payload, CURRENT_SECRET)
    verify_stripe_signature(
        raw_payload,
        header_current,
        secret=CURRENT_SECRET,
        previous_secret=None,
    )

    header_previous = sign_stripe_payload(raw_payload, PREVIOUS_SECRET)
    with pytest.raises(InvalidWebhookSignature):
        verify_stripe_signature(
            raw_payload,
            header_previous,
            secret=CURRENT_SECRET,
            previous_secret=None,
        )


def test_current_secret_still_accepted_with_overlap(raw_payload: bytes) -> None:
    header = sign_stripe_payload(raw_payload, CURRENT_SECRET)
    verify_stripe_signature(
        raw_payload,
        header,
        secret=CURRENT_SECRET,
        previous_secret=PREVIOUS_SECRET,
    )
