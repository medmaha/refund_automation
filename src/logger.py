import logging
import os
from datetime import datetime

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Create the .logs dir if it does not exist
LOG_DIR = ".logs"
os.makedirs(LOG_DIR, exist_ok=True)


class Logger(logging.Logger):
    def __init__(self, name, level = 0):
        super().__init__(name, level)

    def debug(self, msg, *args, **kwargs):
        super().debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        super().info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        super().warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        super().error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        super().critical(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        super().critical(msg, *args, **kwargs)

    

def get_logger(name: str = __name__) -> logging.Logger:
    logger = Logger(name)
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
