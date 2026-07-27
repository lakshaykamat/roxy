import logging

# Third-party loggers that spam INFO for every poll / health check.
# Raising them to WARNING keeps genuine problems (timeouts, retries) visible
# while removing the per-request noise that buries our own log lines.
NOISY_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "telegram.ext.Updater",
)


class _HealthCheckFilter(logging.Filter):
    """Drop uvicorn access logs for the /health endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())
