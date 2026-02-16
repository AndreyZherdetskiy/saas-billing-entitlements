import logging
import sys

import structlog

# Compose/Helm probes hit these every few seconds — keep them off INFO.
PROBE_PATHS = frozenset({"/health/live", "/health/ready"})


class SkipProbeAccessFilter(logging.Filter):
    """Drop uvicorn access lines for liveness/readiness probes."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(path in message for path in PROBE_PATHS)


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, SkipProbeAccessFilter) for item in access_logger.filters):
        access_logger.addFilter(SkipProbeAccessFilter())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
