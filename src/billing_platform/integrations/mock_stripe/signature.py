"""Stripe-compatible webhook signature verification (no Stripe SDK)."""

from __future__ import annotations

import hashlib
import hmac
import time


class InvalidWebhookSignature(Exception):
    """Raised when Stripe-Signature header verification fails."""


def sign_stripe_payload(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header value for the given payload."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signed_payload = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _v1_signature_matches(
    payload: bytes,
    timestamp: str,
    secret: str,
    signatures: list[str],
) -> bool:
    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def verify_stripe_signature(
    payload: bytes,
    header: str,
    secret: str,
    tolerance_seconds: int = 300,
    *,
    previous_secret: str | None = None,
) -> None:
    """Verify Stripe-Signature header (HMAC-SHA256 v1, ±tolerance clock skew).

    Accepts signatures produced with ``secret`` or, during rotation overlap,
    ``previous_secret``. Error messages do not reveal which secret was tried.

    See https://docs.stripe.com/webhooks/signatures
    """
    if not secret:
        raise InvalidWebhookSignature("webhook secret is not configured")

    timestamp: str | None = None
    signatures: list[str] = []

    for element in header.split(","):
        element = element.strip()
        if "=" not in element:
            continue
        prefix, value = element.split("=", 1)
        if prefix == "t":
            timestamp = value
        elif prefix == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise InvalidWebhookSignature("missing timestamp or v1 signature")

    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise InvalidWebhookSignature("invalid timestamp") from exc

    now = int(time.time())
    if abs(now - ts_int) > tolerance_seconds:
        raise InvalidWebhookSignature("timestamp outside tolerance window")

    secrets_to_try = [secret]
    if previous_secret:
        secrets_to_try.append(previous_secret)

    if not any(
        _v1_signature_matches(payload, timestamp, candidate, signatures)
        for candidate in secrets_to_try
    ):
        raise InvalidWebhookSignature("signature mismatch")
