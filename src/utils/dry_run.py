import time

from src.models.order import (
    RefundCreateResponse,
    ShopifyOrder,
)
from src.shopify.refund_calculator import RefundCalculationResult
from src.utils.timezone import get_current_time_iso8601


def create_dry_run_refund(
    order: ShopifyOrder, refund_calculation: RefundCalculationResult, return_id: str
) -> RefundCreateResponse:
    """Create a mock refund for dry run mode using refund calculation."""
    from src.models.order import MoneyBag, MoneyBagSet

    amount = refund_calculation.total_refund_amount
    currencyCode = order.totalPriceSet.presentmentMoney.currencyCode

    # Create refund money set based on calculated amount
    refund_money_bag = MoneyBag(amount=amount, currencyCode=currencyCode)
    refund_money_set = MoneyBagSet(
        presentmentMoney=refund_money_bag, shopMoney=refund_money_bag
    )

    refund_type_suffix = f"-{refund_calculation.refund_type}"

    return RefundCreateResponse(
        id=f"gid://shopify/Refund/{order.id}-{int(time.time())}-dry-run{refund_type_suffix}",
        orderId=order.id,
        orderName=f"{order.name}-R1 | DRY_RUN | {refund_calculation.refund_type}",
        totalRefundedSet=refund_money_set,
        createdAt=get_current_time_iso8601(),
    )
