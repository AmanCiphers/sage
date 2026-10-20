import logging
import sys
import uuid
from pathlib import Path


def configure_logging(log_file: str | Path = "data/sage.log") -> None:
    """INFO+ goes to a log file; the console only shows warnings/errors so
    the interactive CLI stays clean."""

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Reset handlers from previous calls (tests, reloads).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Route structlog through stdlib so its records land in the same file.
    import structlog

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    import structlog
    return structlog.get_logger(name)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:8]}"
