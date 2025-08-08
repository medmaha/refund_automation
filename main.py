from src.logger import get_logger
from src.shopify.refund import process_refund_automation

logger = get_logger(__name__)

if __name__ == "__main__":

    logger.debug(
        "--------------------------------- [Refund Automation] --------------------------------------"
    )

    try:
        # Execute the refund automation function
        process_refund_automation()
    except Exception as e:
        logger.exception(
            "An error occurred during the refund automation process.",
            extra={"detail": e},
        )

    logger.debug(
        "-------------------------------- [Completed Refund Automation] -------------------------------"
    )
