"""Outbox relay process entrypoint (ADR-004)."""

from __future__ import annotations

import asyncio
import signal

import structlog

from billing_platform.config import get_settings
from billing_platform.outbox_relay.publisher import poll_and_publish
from billing_platform.telemetry import configure_telemetry

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 1.0


async def _relay_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            published = await poll_and_publish(settings.outbox_batch_size, settings=settings)
            if published:
                logger.info("outbox_relay_batch_published", count=published)
        except Exception:
            logger.exception("outbox_relay_poll_failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except TimeoutError:
            continue


def main() -> None:
    configure_telemetry(service_name="outbox-relay")
    stop_event = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(*_args: object) -> None:
        logger.info("outbox_relay_shutdown_requested")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        loop.run_until_complete(_relay_loop(stop_event))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
