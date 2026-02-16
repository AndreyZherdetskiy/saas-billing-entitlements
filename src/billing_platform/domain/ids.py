"""Identifier helpers (ADR-010)."""

from __future__ import annotations

import secrets
import time
import uuid


def generate_uuidv7() -> uuid.UUID:
    """Return a time-ordered UUID version 7 (RFC 9562)."""
    unix_ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    uuid_int = unix_ts_ms << 80
    uuid_int |= 7 << 76
    uuid_int |= rand_a << 64
    uuid_int |= 2 << 62
    uuid_int |= rand_b

    return uuid.UUID(int=uuid_int)
