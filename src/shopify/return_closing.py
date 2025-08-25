from concurrent.futures import ThreadPoolExecutor

import requests

from src.config import DRY_RUN, SHOPIFY_API_HEADERS, SHOPIFY_API_URL
from src.logger import get_logger
from src.models.order import ReverseFulfillment, ShopifyOrder
from src.shopify.graph_ql_queries import RETURN_CLOSE_MUTATION
from src.utils.retry import exponential_backoff_retry

logger = get_logger(__name__)


@exponential_backoff_retry(
    exceptions=[
        requests.exceptions.RequestException,
        HTTPError,
        ConnectionError,
        Timeout,
    ]
)
def execute_refund(reverse_fulfillment: ReverseFulfillment):
    response = requests.post(
        SHOPIFY_API_URL,
        headers=SHOPIFY_API_HEADERS,
        json={
            "query": RETURN_CLOSE_MUTATION,
            "variables": {"returnId": reverse_fulfillment.id},
        },
    )
    response.raise_for_status()
    data = response.json().get("data", {}).get("returnClose", {})

    user_errors = data.get("userErrors")
    if user_errors:
        raise Exception(f"Error closing return {reverse_fulfillment.id}: {user_errors}")

    return data.get("return", {})


def close_processed_returns(
    order: ShopifyOrder, reverse_fulfilments: list[ReverseFulfillment]
):

    if DRY_RUN:
        return

    open_returns = [rf for rf in reverse_fulfilments if rf.status == "OPEN"]
    return_ids = [rf.id for rf in open_returns]
    logger.info(
        f"Closing Returns for Order({order.name}) Returns([{', '.join(return_ids)}])"
    )

    if not open_returns:
        logger.info(f"No open returns to close for Order({order.name})")
        return

    # Use ThreadPoolExecutor to process refunds in parallel
    with ThreadPoolExecutor() as executor:
        # Map execute_refund to all open returns
        executor.map(execute_refund, open_returns)

        # Wait for all threads to complete
        executor.shutdown(wait=True)
        logger.info(
            f"Returns Closed for Order({order.name}) Returns([{', '.join(return_ids)}])"
        )
