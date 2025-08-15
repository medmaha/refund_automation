import json
import logging
import os
from datetime import datetime, timezone

from src.config import DRY_RUN

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class ISO8601Formatter(logging.Formatter):
    """Custom formatter that uses ISO8601 timestamps with timezone."""

    def formatTime(self, record, datefmt=None):
        """Format timestamp as ISO8601 with timezone."""
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.isoformat()

    def format(self, record):
        """Format log record with extra fields."""
        # Standard formatting
        formatted = super().format(record)

        # Add extra fields if present
        if hasattr(record, "extra_fields") and record.extra_fields:
            try:
                extra_json = json.dumps(record.extra_fields, separators=(",", ":"))
                formatted += f" | EXTRA: {extra_json}"
            except (TypeError, ValueError) as e:
                formatted += f" | EXTRA_ERROR: {e}"

        return formatted


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Create the .logs dir if it does not exist
LOG_DIR = ".logs"
os.makedirs(LOG_DIR, exist_ok=True)


class Logger(logging.Logger):
    """Enhanced logger with extra field support."""

    def __init__(self, name, level=0):
        super().__init__(name, level)

    def _log_with_extra(self, level, msg, *args, extra=None, **kwargs):
        """Internal method to handle extra fields."""
        if extra:
            # Create a copy to avoid modifying the original
            log_kwargs = kwargs.copy()
            log_kwargs["extra"] = {"extra_fields": extra}
            super()._log(level, msg, args, **log_kwargs)
        else:
            super()._log(level, msg, args, **kwargs)

    def debug(self, msg, *args, extra=None, **kwargs):
        if self.isEnabledFor(logging.DEBUG):
            self._log_with_extra(logging.DEBUG, msg, *args, extra=extra, **kwargs)

    def info(self, msg, *args, extra=None, **kwargs):
        if self.isEnabledFor(logging.INFO):
            self._log_with_extra(logging.INFO, msg, *args, extra=extra, **kwargs)

    def warning(self, msg, *args, extra=None, **kwargs):
        if self.isEnabledFor(logging.WARNING):
            self._log_with_extra(logging.WARNING, msg, *args, extra=extra, **kwargs)

    def error(self, msg, *args, extra=None, **kwargs):
        if self.isEnabledFor(logging.ERROR):
            self._log_with_extra(logging.ERROR, msg, *args, extra=extra, **kwargs)

    def critical(self, msg, *args, extra=None, **kwargs):
        if self.isEnabledFor(logging.CRITICAL):
            self._log_with_extra(logging.CRITICAL, msg, *args, extra=extra, **kwargs)

    def exception(self, msg, *args, extra=None, **kwargs):
        kwargs["exc_info"] = True
        self.error(msg, *args, extra=extra, **kwargs)


def get_logger(name: str = __name__) -> logging.Logger:
    logger = Logger(name)
    if not logger.hasHandlers():
        # Use ISO8601 formatter for both stream and file handlers
        formatter = ISO8601Formatter(LOG_FORMAT)

        # Stream handler
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        # File handler with dynamic filename based on current date
        log_filename = f"log_{datetime.now().strftime('%Y-%m-%d')}.log"

        if DRY_RUN:
            log_filename = "dry_run." + log_filename

        file_handler = logging.FileHandler(os.path.join(LOG_DIR, log_filename))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger.setLevel(LOG_LEVEL)

    return logger
