import requests
import sys

from src.config import REQUEST_TIMEOUT, SHOPIFY_ACCESS_TOKEN, SHOPIFY_STORE_URL
from src.logger import get_logger
from src.models.order import RefundCreateResponse, ShopifyOrder, TransactionKind
from src.shopify.graph_ql_queries import REFUND_CREATE_MUTATION
from src.shopify.orders import retrieve_fulfilled_shopify_orders

logger = get_logger(__name__)

endpoint = f"https://{SHOPIFY_STORE_URL}.myshopify.com/admin/api/2025-07/graphql.json"
headers = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json",
}


def process_refund_automation():
    """Process fulfilled Shopify orders and handle refunds if eligible."""

    trackings = retrieve_fulfilled_shopify_orders()

    if not len(trackings):
        logger.warning("No Tracking Data", extra={"trackings": trackings})
        return sys.exit(0)

    refunded_orders = {}

    for idx, order_and_tracking in enumerate(trackings):
        logger.info(f"Processing tracking {idx+1}/{len(trackings)}")
        order, tracking = order_and_tracking
        latest_event = tracking.track_info.latest_event

        if not latest_event:
            logger.warning(
                "No latest event found for tracking.", extra={"order_id": order.id}
            )
            continue

        # TODO: log every refunded other in separate file for audition purposes

        refund = refund_order(order)
        if refund:
            logger.info(
                "Refund processed successfully.",
                extra={"refund_id": refund.id, "order_id": order.id},
            )
            refunded_orders[refund.id] = refund.model_dump_json(indent=2)
        else:
            logger.warning("Refund not processed.", extra={"order_id": order.id})


    logger.info(
        f"[Refunded] -> {refunded_orders.values()}",
    )


def refund_order(order: ShopifyOrder):
    logger.info(
        "Initiating refund for order.",
        extra={"order_id": order.id, "order_name": order.name},
    )
    _transactions = []
    try:
        for transaction in order.transactions:
            if transaction.kind == TransactionKind.SALE:
                transaction.orderid = order.id
                data = {
                    "orderId": order.id,
                    "parentId": transaction.id,
                    "kind": TransactionKind.REFUND,
                    "gateway": transaction.gateway,
                    "amount": transaction.amountSet.presentmentMoney.amount,
                }
                _transactions.append(data)

        if not _transactions:
            logger.error(
                "[Found order with no Sale transactions]", extra={"order_id": order.id}
            )
            return

        refund_line_items = [
            {"lineItemId": item.id, "quantity": item.quantity}
            for item in order.lineItems
        ]
        variables = {
            "input": {
                "orderId": order.id,
                "transactions": _transactions,
                "refundLineItems": refund_line_items,
            }
        }

        logger.info("Sending refund request to Shopify.", extra={"order_id": order.id})
        response = requests.post(
            endpoint,
            headers=headers,
            json={"query": REFUND_CREATE_MUTATION, "variables": variables},
            timeout=REQUEST_TIMEOUT,
        )

        logger.debug(
            "Shopify response received.", extra={"status_code": response.status_code}
        )

        response.raise_for_status()
        data = response.json()
        logger.debug("Shopify response JSON parsed.", extra={"response_data": data})

        data = data.get("data", {})

        user_errors = data.get("refundCreate", {}).get("userErrors", [])
        error_messages = [err["message"] for err in user_errors]

        if error_messages:
            logger.error(
                f"❌ [Refund Error - {order.name}]: ${error_messages}",
                extra={"order_id": order.id},
            )
            return None

        refund_data = data.get("refundCreate", {}).get("refund")

        if not refund_data:
            logger.error(
                "No refund data returned from Shopify.", extra={"order_id": order.id}
            )
            return None

        refund_data["orderId"] = order.id
        refund_data["orderName"] = order.name

        refund = RefundCreateResponse(**refund_data)
        logger.info(
            "Refund object created successfully.",
            extra={"refund_id": refund.id, "order_id": order.id},
        )

        return refund

    except Exception as e:
        logger.error(
            f"[Refund Failed] - Order({order.name}), [{e}]",
            extra={"order_id": order.id},
        )
        logger.error(
            f"Refund Creation Failed: {e}", exc_info=True, extra={"order_id": order.id}
        )
        return None
