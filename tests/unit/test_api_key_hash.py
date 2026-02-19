"""Unit tests for API key hashing."""

from __future__ import annotations

import hashlib
import hmac

from billing_platform.services.api_keys import hash_api_key, verify_api_key


def test_api_key_hash_is_sha256_hex_not_bcrypt() -> None:
    raw = "bp_test_secret_key_001"
    digest = hash_api_key(raw)
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert not digest.startswith("$2")
    assert raw not in digest
    assert verify_api_key(raw, digest) is True
    assert verify_api_key(raw + "x", digest) is False


def test_verify_api_key_uses_constant_time_compare_shape() -> None:
    raw = "bp_test_secret_key_001"
    digest = hash_api_key(raw)
    assert hmac.compare_digest(digest, hash_api_key(raw)) is True
