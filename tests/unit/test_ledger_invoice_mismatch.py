"""Unit tests for ledger↔invoice reconciliation compare helper."""

from __future__ import annotations

from billing_platform.services.reconciliation import compare_ledger_to_invoice


def test_ledger_invoice_mismatch_detected() -> None:
    d = compare_ledger_to_invoice(ledger_total_cents=1000, invoice_total_cents=900)
    assert d is not None and d.kind == "ledger_invoice_mismatch"


def test_ledger_invoice_match_returns_none() -> None:
    d = compare_ledger_to_invoice(ledger_total_cents=1000, invoice_total_cents=1000)
    assert d is None


def test_ledger_invoice_mismatch_delta_and_amounts() -> None:
    d = compare_ledger_to_invoice(ledger_total_cents=1000, invoice_total_cents=900)
    assert d is not None
    assert d.expected_amount_cents == 900
    assert d.actual_amount_cents == 1000
    assert d.delta_cents == -100
