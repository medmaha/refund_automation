import sys
from src.logger import get_logger
from src.shopify.refund import process_refund_automation
from src.config import DRY_RUN
from src.utils.slack import slack_notifier
from src.utils.audit import audit_logger
from src.utils.idempotency import idempotency_manager
from src.utils.timezone import timezone_handler

logger = get_logger(__name__)


def main(mode: str):
    
    logger.info(
        f"================================= [Refund Automation - {mode}] ================================="
    )
    
    # Log system information
    logger.info(
        "System initialization",
        extra={
            "mode": mode,
            "audit_stats": audit_logger.get_audit_stats(),
            "timezone_info": timezone_handler.get_timezone_info(),
            "idempotency_stats": idempotency_manager.get_stats()
        }
    )


    try:
        # Execute the refund automation function
        process_refund_automation()

        # audit_logger.mark_operation_completed()
        # slack_notifier.mark_operation_completed()
        # timezone_handler.mark_operation_completed()
        # idempotency_manager.mark_operation_completed()
        
        logger.info(
            f"Refund automation completed successfully in {mode} mode",
            extra={"mode": mode, "exit_code": 0}
        )
        
    except KeyboardInterrupt:
        logger.warning("Refund automation interrupted by user")
        slack_notifier.send_warning("Refund automation interrupted by user")
        sys.exit(130)  # Standard exit code for Ctrl+C
        
    except Exception as e:
        error_msg = f"Critical error in refund automation: {str(e)}"
        logger.exception(
            error_msg,
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "mode": mode
            }
        )
        
        # Send critical error notification
        slack_notifier.send_error(
            error_msg,
            details={
                "error_type": type(e).__name__,
                "mode": mode
            }
        )
        
        sys.exit(1)

    logger.info(
        f"=============================== [Completed Refund Automation - {mode}] ==============================="
    )


if __name__ == "__main__":
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    main(mode=mode)