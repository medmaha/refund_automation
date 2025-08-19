import os

from dotenv import load_dotenv

load_dotenv()

# 17TRACK
TRACKING_API_KEY = os.getenv("TRACKING_API_KEY")
TRACKING_BASE_URL = os.getenv("TRACKING_API_URL")
DEFAULT_CARRIER_CODE = 7041  # DHL Paket

RETURN_TRACKING_STATUS = "Delivered"
RETURN_TRACKING_SUB_STATUS = "Delivered_Other"

# Shopify
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_TIMEZONE = os.getenv("SHOPIFY_TIMEZONE", "UTC")

# Slack Notifications
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#refund-automation")
SLACK_ENABLED = os.getenv("SLACK_ENABLED", "true").lower() == "true"

# Execution Mode
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Request Settings
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BASE_RETRY_DELAY = float(os.getenv("BASE_RETRY_DELAY", "1.0"))
MAX_RETRY_DELAY = float(os.getenv("MAX_RETRY_DELAY", "60.0"))

# Audit Settings
AUDIT_LOG_DIR = os.getenv("AUDIT_LOG_DIR", ".audit_logs")
AUDIT_LOG_ENABLED = os.getenv("AUDIT_LOG_ENABLED", "true").lower() == "true"


# Idempotency
IDEMPOTENCY_SAVE_ENABLED = (
    os.getenv("IDEMPOTENCY_SAVE_ENABLED", "true").lower() == "true" if DRY_RUN else True
)

__automation_id = None


def __get_automation_id():
    """Unique ID generated for this script execution."""
    global __automation_id

    if __automation_id:
        return __automation_id

    import uuid

    __automation_id = str(uuid.uuid4()).replace("-", "")[:16].upper()

    return __automation_id


AUTOMATION_ID = __get_automation_id()
