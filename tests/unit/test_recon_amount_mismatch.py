"""Unit tests for reconciliation amount comparison."""

from __future__ import annotations

from billing_platform.services.reconciliation import compare_amounts


def test_amount_mismatch_detected() -> None:
    d = compare_amounts(expected_cents=1000, actual_cents=900)
    assert d is not None
    assert d.kind == "amount_mismatch"
    assert d.delta_cents == 100
    assert d.expected_cents == 1000
    assert d.actual_cents == 900


def test_amount_match_returns_none() -> None:
    assert compare_amounts(expected_cents=1000, actual_cents=1000) is None
