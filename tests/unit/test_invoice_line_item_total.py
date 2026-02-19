"""Unit tests for invoice line item amount helpers."""

from __future__ import annotations

from billing_platform.services.invoices import line_total_cents


def test_line_item_total_cents() -> None:
    assert line_total_cents(quantity=3, unit_amount_cents=100) == 300
