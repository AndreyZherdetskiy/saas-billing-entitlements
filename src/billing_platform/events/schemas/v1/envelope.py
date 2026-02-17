"""Kafka event envelope v1 (ADR-002)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventEnvelope:
    """Frozen integration event envelope (schema_version=1)."""

    schema_version: int
    event_id: str
    event_type: str
    occurred_at: str
    organization_id: str
    correlation_id: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "organization_id": self.organization_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }
