from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from src.config import REQUEST_TIMEOUT, SHOPIFY_API_HEADERS, SHOPIFY_API_URL
from src.logger import get_logger
from src.models.order import ReverseFulfillment, ShopifyOrder
from src.shopify.graph_ql_queries import RETURN_CLOSE_MUTATION
from src.utils.retry import exponential_backoff_retry

logger = get_logger(__name__)


@exponential_backoff_retry(
    exceptions=[
        requests.exceptions.RequestException,
        requests.exceptions.HTTPError,
        requests.exceptions.Timeout,
        ValueError,
    ]
)
def close_return(reverse_fulfillment: ReverseFulfillment):
    variables = {
        "returnId": reverse_fulfillment.id
    }
    
    response = requests.post(
        SHOPIFY_API_URL,
        headers=SHOPIFY_API_HEADERS,
        json={"query": RETURN_CLOSE_MUTATION, "variables": variables},
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    response_json = response.json()
    data = response_json.get("data", {}).get("returnClose", {})

    errors = response_json.get("errors", None)
    if errors:
        raise ValueError(f"Error closing return {reverse_fulfillment.name}: {errors}")

    user_errors = data.get("userErrors", None)
    if user_errors:
        raise ValueError(f"Error closing return {reverse_fulfillment.name}: {user_errors}")
    
    return data.get("return", None)



def close_processed_returns(
    order: ShopifyOrder, reverse_fulfilments: list[ReverseFulfillment]
):
    open_returns = [rf for rf in reverse_fulfilments if rf.status == "OPEN"]
    return_ids = [rf.name for rf in open_returns]

    if not open_returns:
        logger.info(f"No open returns to close for Order({order.name})")
        return

    logger.info(
        f"Closing Returns for Order({order.name}) Returns[{', '.join(return_ids)}]"
    )

    # Use ThreadPoolExecutor to process refunds in parallel with proper error handling
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit tasks and get futures
        future_to_return = {
            executor.submit(close_return, reverse_fulfillment=rf): rf for rf in open_returns
        }

        # Process completed futures as they complete
        for future in as_completed(future_to_return):
            reverse_fulfillment = future_to_return[future]
            try:
                result = future.result()
                if not result:
                    logger.error(f"Failed to close return: Order({order.name}) Return({reverse_fulfillment.name})")
                else:
                    logger.info(f"Successfully closed return: Order({order.name}) Return({reverse_fulfillment.name})")
            except Exception as e:
                logger.error(f"Exception occurred while closing return {reverse_fulfillment.name}", extra={"Error": str(e)})
