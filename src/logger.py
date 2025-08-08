import logging
import os
from datetime import datetime

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Create the .logs dir if it does not exist
LOG_DIR = ".logs"
os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name: str = __name__) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        # Stream handler
        stream_handler = logging.StreamHandler()
        formatter = logging.Formatter(LOG_FORMAT)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        # File handler with dynamic filename based on current date
        log_filename = f"log_{datetime.now().strftime('%Y-%m-%d')}.log"
        file_handler = logging.FileHandler(os.path.join(LOG_DIR, log_filename))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger.setLevel(LOG_LEVEL)

    return logger
