#!/usr/bin/env python3
"""Replay outbox DLQ rows — docs/runbooks/dlq-replay.md."""

from __future__ import annotations

import argparse
import asyncio

from billing_platform.db import get_session_factory
from billing_platform.outbox_relay.dlq_replay import (
    ReplayStatus,
    emit_audit_log,
    replay_dead_letters,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay outbox dead-letter rows for relay re-publish.",
    )
    parser.add_argument(
        "--dlq-id",
        type=int,
        action="append",
        dest="dlq_ids",
        metavar="ID",
        required=True,
        help="outbox_dead_letters.id to replay (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report actions without committing changes",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    session_factory = get_session_factory()
    results = await replay_dead_letters(
        session_factory,
        args.dlq_ids,
        dry_run=args.dry_run,
    )
    emit_audit_log(results)

    has_failure = any(
        r.status in (ReplayStatus.not_found, ReplayStatus.outbox_missing) for r in results
    )
    if has_failure:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
