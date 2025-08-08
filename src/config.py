import os

from dotenv import load_dotenv

load_dotenv()

# 17TRACK
TRACKING_API_KEY = os.getenv("TRACKING_API_KEY")
TRACKING_BASE_URL = os.getenv("TRACKING_API_URL")


# Shopify
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")
SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")


REQUEST_TIMEOUT = 15


RETURN_TRACKING_STATUS = "Delivered"
RETURN_TRACKING_SUB_STATUS = "Delivered_Other"
