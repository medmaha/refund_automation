"""
Refund calculation logic for handling partial returns and proportional refunds.

This module implements the business rules:
- B1. Full Return → Full Original Amount
- B2. Partial Return → Proportional Refund
- B3. Split Fulfillment → Only refund items received back
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List

from pydantic import BaseModel

from src.logger import get_logger
from src.models.order import LineItem, ReturnLineItem, ShopifyOrder
from src.models.tracking import TrackingData

logger = get_logger(__name__)


class RefundCalculationResult(BaseModel):
    """Result of refund calculations."""

    refund_type: str
    total_refund_amount: float
    line_items_to_refund: List[Dict]
    transactions: List[Dict]
    shipping_refund: float = 0.0
    tax_refund: float = 0.0
    discount_deduction: float = 0.0


class RefundCalculator:
    """Handles refund calculations based on business rules."""

    def __init__(self):
        self.logger = logger

    def calculate_refund(
        self, order: ShopifyOrder, tracking=TrackingData
    ) -> RefundCalculationResult:
        """
        Calculate refund based on the return type and business rules.

        Args:
            order: ShopifyOrder containing all order data
            tracking: Optional tracking information

        Returns:
            RefundCalculationResult with calculated amounts and line items
        """
        self.logger.info(f"Calculating refund for order {order.name}")

        # Determine if this is a full or partial return
        returned_line_items = self._get_returned_line_items(order)

        if not returned_line_items:
            self.logger.warning(f"No returned line items found for order {order.name}")
            # Default to full refund if no return line items are specified
            return self._calculate_full_refund(order)

        if self._is_full_return(order, returned_line_items):
            self.logger.info(f"Calculating full refund for order {order.name}")
            return self._calculate_full_refund(order)
        else:
            self.logger.info(f"Calculating partial refund for order {order.name}")
            return self._calculate_partial_refund(order, returned_line_items)

    def _get_returned_line_items(self, order: ShopifyOrder) -> List[ReturnLineItem]:
        """Extract returned line items from the order's return data."""
        returned_items = []
        for return_fulfillment in order.returns:
            for li in return_fulfillment.returnLineItems:
                if li.refundableQuantity:
                    returned_items.append(li)
        return returned_items

    def _is_full_return(
        self, order: ShopifyOrder, returned_line_items: List[ReturnLineItem]
    ) -> bool:
        """
        Determine if this is a full return by comparing returned quantities
        with original line item quantities.
        """
        # Create a map of line item ID to returned quantity
        returned_qty_map = {}
        for returned_item in returned_line_items:
            line_item_id = returned_item.fulfillmentLineItem.lineItem.get("id")
            if line_item_id:
                returned_qty_map[line_item_id] = (
                    returned_qty_map.get(line_item_id, 0) + returned_item.quantity
                )

        # Check if all line items are fully returned
        for line_item in order.lineItems:
            returned_qty = returned_qty_map.get(line_item.id, 0)
            if returned_qty < line_item.quantity:
                return False

        return True

    def _calculate_full_refund(self, order: ShopifyOrder) -> RefundCalculationResult:
        """
        Calculate full refund - return exactly what customer paid back to original payment methods.
        Business Rule B1: Full Return → Full Original Amount
        """
        self.logger.info(f"Calculating full refund for order {order.name}")

        # For full refund, refund all line items with their full quantities
        refund_line_items = [
            {
                "lineItemId": item.id,
                "quantity": item.refundableQuantity,
                # "restockType": "RETURN",
            }
            for item in order.lineItems
        ]

        # Use suggested refund from Shopify which handles original payment method allocation
        total_refund_amount = order.suggestedRefund.amountSet.presentmentMoney.amount
        transactions = self._prepare_refund_transactions(order)

        # Calculate shipping and tax refunds
        shipping_refund = (
            order.suggestedRefund.shipping.amountSet.presentmentMoney.amount
        )
        tax_refund = self._calculate_total_tax_refund(order.lineItems, order.lineItems)

        return RefundCalculationResult(
            refund_type="FULL",
            total_refund_amount=total_refund_amount,
            line_items_to_refund=refund_line_items,
            transactions=transactions,
            shipping_refund=shipping_refund,
            tax_refund=tax_refund,
        )

    def _calculate_partial_refund(
        self, order: ShopifyOrder, returned_line_items: List[ReturnLineItem]
    ) -> RefundCalculationResult:
        """
        Calculate partial refund with proportional amounts.
        Business Rule B2: Partial Return → Proportional Refund
        """
        self.logger.info(f"Calculating partial refund for order {order.name}")

        # Create mapping of returned quantities by line item ID
        returned_qty_map = {}
        for returned_item in returned_line_items:
            line_item_id = returned_item.fulfillmentLineItem.lineItem.get("id")
            if line_item_id:
                returned_qty_map[line_item_id] = (
                    returned_qty_map.get(line_item_id, 0) + returned_item.quantity
                )

        # Calculate proportional refund amounts for each line item
        refund_line_items = []
        returned_line_items_data = []
        total_line_item_refund = Decimal("0")

        for line_item in order.lineItems:
            returned_qty = returned_qty_map.get(line_item.id, 0)
            if returned_qty > 0:
                # Ensure we don't exceed refundable quantity
                refund_qty = min(returned_qty, line_item.refundableQuantity)
                refund_line_items.append(
                    {
                        "lineItemId": line_item.id,
                        "quantity": refund_qty,
                        # "restockType": "RETURN",
                    }
                )

                # Calculate proportional refund amount for this line item
                line_item_refund = self._calculate_line_item_proportional_refund(
                    line_item, refund_qty
                )
                total_line_item_refund += line_item_refund
                returned_line_items_data.append(line_item)

        # Calculate proportional shipping refund
        shipping_refund = self._calculate_proportional_shipping_refund(
            order, returned_line_items_data
        )

        # Calculate proportional tax refund
        tax_refund = self._calculate_total_tax_refund(
            order.lineItems, returned_line_items_data
        )

        # Total refund amount
        total_refund_amount = (
            float(total_line_item_refund) + shipping_refund + tax_refund
        )

        # Calculate proportional transactions
        transactions = self._calculate_proportional_transactions(
            order, total_refund_amount
        )

        return RefundCalculationResult(
            refund_type="PARTIAL",
            total_refund_amount=total_refund_amount,
            line_items_to_refund=refund_line_items,
            transactions=transactions,
            shipping_refund=shipping_refund,
            tax_refund=tax_refund,
        )

    def _calculate_line_item_proportional_refund(
        self, line_item: LineItem, refund_qty: int
    ) -> Decimal:
        """Calculate proportional refund amount for a line item considering discounts."""

        # Base amount per unit (before discounts)
        base_amount_per_unit = Decimal(
            str(line_item.originalTotalSet.presentmentMoney.amount)
        ) / Decimal(str(line_item.quantity))

        # Calculate discount per unit
        total_discount = Decimal("0")
        for discount_allocation in line_item.discountAllocations:
            total_discount += Decimal(
                str(discount_allocation.allocatedAmountSet.presentmentMoney.amount)
            )

        discount_per_unit = (
            total_discount / Decimal(str(line_item.quantity))
            if line_item.quantity > 0
            else Decimal("0")
        )

        # Net amount per unit (after discount)
        net_amount_per_unit = base_amount_per_unit - discount_per_unit

        # Total refund for this line item
        line_item_refund = net_amount_per_unit * Decimal(str(refund_qty))

        self.logger.debug(
            f"Line item {line_item.id}: base={base_amount_per_unit}, discount={discount_per_unit}, net={net_amount_per_unit}, qty={refund_qty}, refund={line_item_refund}"
        )

        return line_item_refund

    def _calculate_proportional_shipping_refund(
        self, order: ShopifyOrder, returned_line_items: List[LineItem]
    ) -> float:
        """Calculate proportional shipping refund based on returned items."""

        if not returned_line_items:
            return 0.0

        # Calculate proportion of returned items by value
        total_order_value = Decimal(str(order.totalPriceSet.presentmentMoney.amount))
        returned_items_value = Decimal("0")

        for line_item in returned_line_items:
            line_item_value = Decimal(
                str(line_item.originalTotalSet.presentmentMoney.amount)
            )
            returned_items_value += line_item_value

        if total_order_value == 0:
            return 0.0

        shipping_proportion = returned_items_value / total_order_value
        original_shipping = Decimal(
            str(order.suggestedRefund.shipping.amountSet.presentmentMoney.amount)
        )
        proportional_shipping = original_shipping * shipping_proportion

        return float(
            proportional_shipping.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    def _calculate_total_tax_refund(
        self, all_line_items: List[LineItem], returned_line_items: List[LineItem]
    ) -> float:
        """Calculate total tax refund for returned line items."""

        total_tax_refund = Decimal("0")

        for line_item in returned_line_items:
            for tax_line in line_item.taxLines:
                # Calculate proportional tax based on returned quantity vs original quantity
                original_line_item = next(
                    (item for item in all_line_items if item.id == line_item.id), None
                )
                if original_line_item:
                    tax_per_unit = Decimal(
                        str(tax_line.priceSet.presentmentMoney.amount)
                    ) / Decimal(str(original_line_item.quantity))
                    # For partial refunds, we need to determine how many units are being returned
                    # This would need to be passed from the calling function
                    total_tax_refund += tax_per_unit / Decimal(str(len(all_line_items)))

        return float(total_tax_refund.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _calculate_proportional_transactions(
        self, order: ShopifyOrder, refund_amount: float
    ) -> List[Dict]:
        """Calculate proportional transaction amounts for partial refunds."""

        if not order.suggestedRefund.suggestedTransactions:
            return []

        # Calculate the proportion of the refund relative to the original order
        original_amount = Decimal(str(order.totalPriceSet.presentmentMoney.amount))
        refund_amount_decimal = Decimal(str(refund_amount))

        if original_amount == 0:
            return []

        proportion = refund_amount_decimal / original_amount

        transactions = []
        for suggested_transaction in order.suggestedRefund.suggestedTransactions:
            original_transaction_amount = Decimal(
                str(suggested_transaction.amountSet.presentmentMoney.amount)
            )
            proportional_amount = original_transaction_amount * proportion

            transaction_data = {
                "orderId": order.id,
                "parentId": suggested_transaction.parentTransaction.id,
                "kind": "REFUND",
                "gateway": suggested_transaction.gateway,
                "amount": float(
                    proportional_amount.quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                ),
            }
            transactions.append(transaction_data)

        return transactions

    def _prepare_refund_transactions(self, order: ShopifyOrder) -> List[Dict]:
        """Prepare transaction data for full refund (existing logic)."""

        transactions = []

        if not order.suggestedRefund or not order.suggestedRefund.suggestedTransactions:
            return transactions

        for transaction in order.suggestedRefund.suggestedTransactions:
            data = {
                "orderId": order.id,
                "parentId": transaction.parentTransaction.id,
                "kind": "REFUND",
                "gateway": transaction.gateway,
                "amount": transaction.amountSet.presentmentMoney.amount,
            }
            transactions.append(data)

        return transactions


refund_calculator = RefundCalculator()
