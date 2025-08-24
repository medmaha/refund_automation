import json
import os
from typing import Optional

import requests

from src.config import (
    REQUEST_TIMEOUT,
    SHOPIFY_ACCESS_TOKEN,
    SHOPIFY_STORE_URL,
)
from src.logger import get_logger
from src.models.order import RefundCreateResponse, ShopifyOrder
from src.shopify.graph_ql_queries import REFUND_CREATE_MUTATION
from src.utils.audit import audit_logger
from src.utils.retry import exponential_backoff_retry
from src.utils.slack import slack_notifier

logger = get_logger(__name__)

endpoint = f"https://{SHOPIFY_STORE_URL}.myshopify.com/admin/api/2025-07/graphql.json"
headers = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json",
}

TEST_SCENARIO_STATUS_CODES = os.getenv("TEST_SCENARIO_STATUS_CODES", "")


@exponential_backoff_retry(
    exceptions=(
        requests.exceptions.RequestException,
        requests.exceptions.Timeout,
        Exception,
    )
)
def execute_shopify_refund(
    order: ShopifyOrder, variables: dict, request_id: str, current_return_id: str
) -> Optional[RefundCreateResponse]:
    """Execute the Shopify refund API call with retry mechanism."""

    # Log API request for audit
    audit_logger.log_api_interaction(
        request_type="POST", endpoint=endpoint, order_id=order.id, request_id=request_id
    )

    try:
        # Simulate 500 error for test scenario
        if "500" in TEST_SCENARIO_STATUS_CODES:
            raise Exception(
                "500 Server Error: Internal Server Error for url: " + endpoint,
            )

        # Actual Shopify Refund Mutation
        response = requests.post(
            endpoint,
            headers=headers,
            json={"query": REFUND_CREATE_MUTATION, "variables": variables},
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        shopify_response = response.json()

        # Handle null JSON response
        if shopify_response is None:
            logger.error(
                f"Received null JSON response from Shopify for order {order.name}",
                extra={"order_id": order.id, "request_id": request_id},
            )
            return None

        if "errors" in shopify_response:
            user_errors = {"errors": shopify_response["errors"]}

            error_msg = (
                f"Shopify API error: Order({order.name}) Return({current_return_id})"
            )

            if (
                "extensions" in shopify_response
                and "userErrors" in shopify_response["extensions"]
            ):
                user_errors.update(shopify_response["extensions"]["userErrors"])

            logger.error(
                error_msg,
                extra={
                    "order_id": order.id,
                    "order_name": order.name,
                    "request_id": request_id,
                    **user_errors,
                },
            )

            slack_notifier.send_error(
                error_msg,
                details={
                    "order_id": order.id,
                    "order_name": order.name,
                    "request_id": request_id,
                    "return_id": current_return_id,
                    **user_errors,
                },
                request_id=request_id,
            )

        # Process Shopify response
        response_data = shopify_response.get("data") if shopify_response else None
        if response_data is None:
            logger.error(
                f"No 'data' field in Shopify response for order {order.name}",
                extra={
                    "order_id": order.id,
                    "request_id": request_id,
                    "response": shopify_response,
                },
            )
            return None

        response_time_ms = response.elapsed.total_seconds() * 1000

        logger.debug(
            f"Shopify API response received for order {order.name}",
            extra={
                "status_code": response.status_code,
                "response_time_ms": response_time_ms,
                "order_id": order.id,
                "request_id": request_id,
            },
        )

        response.raise_for_status()

        # Log API response for audit
        audit_logger.log_api_interaction(
            request_type="POST",
            endpoint=endpoint,
            order_id=order.id,
            request_id=request_id,
            status_code=response.status_code,
            response_time_ms=response_time_ms,
        )

        user_errors = response_data.get("refundCreate", {}).get("userErrors", [])
        refund_data = response_data.get("refundCreate", {}).get("refund", None)

        if user_errors:
            error_messages = [err["message"] for err in user_errors]
            error_msg = f"Shopify API errors for order {order.name}: {error_messages}"

            logger.error(
                error_msg,
                extra={
                    "order_id": order.id,
                    "order_name": order.name,
                    "shopify_errors": error_messages,
                    "request_id": request_id,
                },
            )
            # Log API error for audit
            audit_logger.log_api_interaction(
                request_type="POST",
                endpoint=endpoint,
                order_id=order.id,
                request_id=request_id,
                status_code=response.status_code,
                response_time_ms=response_time_ms,
                error="Shopify user errors: " + "; ".join(error_messages),
            )
            slack_notifier.send_error(error_msg)

            if refund_data:
                error_msg = (
                    f"Shopify API errors for order {order.name}: {error_messages}"
                )
                logger.warning(
                    error_msg,
                    extra={
                        "order_id": order.id,
                        "order_name": order.name,
                        "shopify_errors": error_messages,
                        "request_id": request_id,
                    },
                )
                slack_notifier.send_warning(
                    error_msg,
                    details={
                        "message": "An error accurred while mutating refunds",
                        "request_id": request_id,
                        "order_id": order.id,
                        "order_name": order.name,
                    },
                )

        if not refund_data:
            logger.error(
                f"No refund data returned from Shopify for order {order.name}",
                extra={"order_id": order.id, "request_id": request_id},
            )
            slack_notifier.send_error(
                f"No refund data returned from Shopify for order {order.name}",
                details={
                    "order_id": order.id,
                    "request_id": request_id,
                    "response_data": (json.dumps(shopify_response)),
                },
            )
            return None

        # Enrich refund data
        refund_data["orderId"] = order.id
        refund_data["orderName"] = order.name

        return RefundCreateResponse(**refund_data)

    except Exception as e:
        # Log API error for audit
        audit_logger.log_api_interaction(
            request_type="POST",
            endpoint=endpoint,
            order_id=order.id,
            request_id=request_id,
            error=str(e),
        )
        raise
