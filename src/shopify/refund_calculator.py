
from decimal import ROUND_DOWN, Decimal
from typing import Dict, List, Literal, Union

from pydantic import BaseModel

from src.logger import get_logger
from src.models.order import LineItem, ReturnLineItem, ShopifyOrder
from src.models.tracking import TrackingData

logger = get_logger(__name__)


class RefundCalculationResult(BaseModel):
    """Result of refund calculations."""

    refund_type: Literal["FULL", "PARTIAL"]
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
            # Default to full refund if no return line items are specified
            return self._calculate_full_refund(order)

        if self._is_full_return(order, returned_line_items):
            return self._calculate_full_refund(order)
        else:
            return self._calculate_partial_refund(order, returned_line_items)

    def _get_returned_line_items(self, order: ShopifyOrder) -> List[ReturnLineItem]:
        """
        Extract returned line items from the order's return data.
        """
        returned_items = []

        # Get the current tracking number to identify the specific return
        current_tracking_number = order.get_tracking_number()
        if not current_tracking_number:
            self.logger.warning(f"No tracking number found for order {order.name}")
            return returned_items

        # Find the specific return that matches the current tracking number
        matching_return = None

        """
        Filtering returned_line_items by the specific return that matches
        the current tracking to avoid including items from multiple simultaneous OPEN returns.
        """
        for return_fulfillment in order.returns:
            if return_fulfillment.status == "OPEN":
                for rfo in return_fulfillment.reverseFulfillmentOrders:
                    for rd in rfo.reverseDeliveries:
                        if (
                            rd.deliverable.tracking.number
                            and rd.deliverable.tracking.number
                            == current_tracking_number
                        ):
                            matching_return = return_fulfillment
                            break
                    if matching_return:
                        break
            if matching_return:
                break

        if not matching_return:
            self.logger.warning(
                f"No return found matching tracking number {current_tracking_number} for order {order.name}"
            )
            return returned_items

        # Only process line items from the matching return
        for li in matching_return.returnLineItems:
            original_qty = li.fulfillmentLineItem.lineItem.get("quantity")
            if li.refundableQuantity <= original_qty:
                returned_items.append(li)

        self.logger.debug(
            f"Filtered returned line items for order {order.name}: "
            f"found {len(returned_items)} items from return {matching_return.id} "
            f"with tracking {current_tracking_number}"
        )

        return returned_items

    def _is_full_return(
        self, order: ShopifyOrder, returned_line_items: List[ReturnLineItem]
    ) -> bool:
        """
        Determine if this is a full return by comparing returned quantities
        with original line item quantities.
        """
        # Create a map of line item ID to returned quantity
        returned_line_items_qty_map = {}
        for returned_item in returned_line_items:
            line_item_id = returned_item.fulfillmentLineItem.lineItem.get("id")
            if line_item_id:
                returned_line_items_qty_map[line_item_id] = (
                    returned_line_items_qty_map.get(line_item_id, 0)
                    + returned_item.refundableQuantity
                )

        # Check if all line items are fully returned
        for line_item in order.lineItems:
            returned_qty = returned_line_items_qty_map.get(line_item.id, 0)
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
            }
            for item in order.lineItems
        ]

        # Using suggested refund from Shopify which handles original payment method allocation
        total_refund_amount = (
            order.suggestedRefund.amountSet.presentmentMoney.amount
            - order.priorRefundAmount
        )

        transactions = self._prepare_refund_transactions(order)

        # Calculate shipping and tax refunds
        shipping_refund = (
            order.suggestedRefund.shipping.amountSet.presentmentMoney.amount
        )

        tax_refund = self._calculate_full_total_tax_refund(order.lineItems)

        return RefundCalculationResult(
            refund_type="FULL",
            total_refund_amount=self.__normalize_amount(total_refund_amount),
            shipping_refund=self.__normalize_amount(shipping_refund),
            tax_refund=self.__normalize_amount(tax_refund),
            line_items_to_refund=refund_line_items,
            transactions=transactions,
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
                    returned_qty_map.get(line_item_id, 0)
                    + returned_item.refundableQuantity
                )

        # Calculate proportional refund amounts for each line item
        refund_line_items = []
        returned_line_items_data = []
        total_line_item_refund = Decimal("0")

        for order_line_item in order.lineItems:
            returned_qty = returned_qty_map.get(order_line_item.id, 0)
            if returned_qty > 0:
                # Ensure we don't exceed refundable quantity
                refund_qty = min(returned_qty, order_line_item.refundableQuantity)
                return_line_item = {
                    "lineItemId": order_line_item.id,
                    "quantity": refund_qty,
                }
                refund_line_items.append(return_line_item)

                # Calculate proportional refund amount for this line item
                line_item_refund = self._calculate_line_item_proportional_refund(
                    order_line_item, refund_qty
                )
                total_line_item_refund += line_item_refund
                returned_line_items_data.append((order_line_item, return_line_item))

        # Calculate proportional shipping refund
        shipping_refund = self._calculate_proportional_shipping_refund(
            order, returned_line_items_data
        )

        # Calculate proportional tax refund
        tax_refund = self._calculate_proportional_tax_refund(returned_line_items_data)

        # Total refund amount
        total_refund_amount = float(
            (float(total_line_item_refund) + float(shipping_refund) + float(tax_refund))
            - order.priorRefundAmount
        )

        # Calculate proportional transactions
        transactions = self._calculate_proportional_transactions(
            order, total_refund_amount
        )

        return RefundCalculationResult(
            refund_type="PARTIAL",
            total_refund_amount=self.__normalize_amount(total_refund_amount),
            shipping_refund=self.__normalize_amount(shipping_refund),
            tax_refund=self.__normalize_amount(tax_refund),
            line_items_to_refund=refund_line_items,
            transactions=transactions,
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
        self,
        order: ShopifyOrder,
        returned_line_item_entries: List[tuple[LineItem, dict]],
    ):
        """Calculate proportional shipping refund based on returned items."""

        if not returned_line_item_entries:
            self.logger.debug("No returned line items for shipping calculation")
            return Decimal("0")

        original_shipping = Decimal(
            str(order.suggestedRefund.shipping.amountSet.presentmentMoney.amount)
        )

        if original_shipping <= 0:
            # TODO: this is not the only way to find out refundable shipping
            self.logger.debug(
                f"No refundable shipping for order {order.name}: {original_shipping}"
            )
            return Decimal("0")

        if order.totalRefundedShippingSet.presentmentMoney.amount:
            already_refunded_shipping = Decimal(
                str(order.totalRefundedShippingSet.presentmentMoney.amount)
            )

            if already_refunded_shipping >= original_shipping:
                self.logger.debug(
                    f"Shipping already fully refunded for order {order.name}"
                )
                return Decimal("0")

            # Adjust for partial shipping refunds already processed
            original_shipping -= already_refunded_shipping

        # Calculate total order value from line items (excluding shipping, taxes, etc.)
        total_order_value = Decimal("0")
        for line_item in order.lineItems:
            try:
                # Calculate net value per unit (original price minus discount per unit)
                original_total = Decimal(
                    str(line_item.originalTotalSet.presentmentMoney.amount)
                )

                # Calculate total discount for this line item
                total_discount = Decimal("0")
                for discount_allocation in line_item.discountAllocations:
                    total_discount += Decimal(
                        str(
                            discount_allocation.allocatedAmountSet.presentmentMoney.amount
                        )
                    )

                # Net value for this line item (after discounts)
                net_line_item_value = original_total - total_discount

                # Ensure non-negative values
                if net_line_item_value < 0:
                    self.logger.warning(
                        f"Negative net value for line item {line_item.id}: {net_line_item_value}. "
                        "Setting to 0."
                    )
                    net_line_item_value = Decimal("0")

                total_order_value += net_line_item_value
            except (ValueError, TypeError, AttributeError) as e:
                self.logger.error(
                    f"Error processing line item {line_item.id} for shipping calculation: {e}"
                )
                continue

        if total_order_value == 0:
            self.logger.warning(
                f"Total order value is 0 for order {order.name}, cannot calculate proportional shipping"
            )
            return Decimal("0")

        # Calculate returned items value based on actual returned quantities
        returned_items_value = Decimal("0")
        for line_item_entry in returned_line_item_entries:
            order_line_item, return_line_item = line_item_entry
            returned_quantity = return_line_item["quantity"]

            if returned_quantity <= 0:
                self.logger.warning(
                    f"Invalid returned quantity {returned_quantity} for line item {order_line_item.id}"
                )
                continue

            if returned_quantity > order_line_item.quantity:
                self.logger.warning(
                    f"Returned quantity {returned_quantity} exceeds original quantity {order_line_item.quantity} "
                    f"for line item {order_line_item.id}, capping to original quantity"
                )
                returned_quantity = order_line_item.quantity

            try:
                original_total = Decimal(
                    str(order_line_item.originalTotalSet.presentmentMoney.amount)
                )

                # Calculate total discount for this line item
                total_discount = Decimal("0")
                for discount_allocation in order_line_item.discountAllocations:
                    total_discount += Decimal(
                        str(
                            discount_allocation.allocatedAmountSet.presentmentMoney.amount
                        )
                    )

                # Net value per unit (after discounts)
                if order_line_item.quantity <= 0:
                    self.logger.error(
                        f"Invalid original quantity {order_line_item.quantity} for line item {order_line_item.id}"
                    )
                    continue

                net_value_per_unit = (original_total - total_discount) / Decimal(
                    str(order_line_item.quantity)
                )

                # Ensure non-negative unit value
                if net_value_per_unit < 0:
                    self.logger.warning(
                        f"Negative net unit value for line item {order_line_item.id}: {net_value_per_unit}. "
                        "Setting to 0."
                    )
                    net_value_per_unit = Decimal("0")

                # Add proportional value for returned quantity
                line_returned_value = net_value_per_unit * Decimal(
                    str(returned_quantity)
                )
                returned_items_value += line_returned_value

                self.logger.debug(
                    f"Line item {order_line_item.id}: net_unit_value={net_value_per_unit}, "
                    f"returned_qty={returned_quantity}, line_returned_value={line_returned_value}"
                ) 

            except (ValueError, TypeError, ZeroDivisionError) as e:
                self.logger.error(
                    f"Error calculating returned value for line item {order_line_item.id}: {e}"
                )
                continue

        if returned_items_value <= 0:
            self.logger.warning(
                f"No positive returned item value calculated for order {order.name}"
            )
            return Decimal("0")

        # Calculate shipping proportion with bounds checking
        shipping_proportion = returned_items_value / total_order_value

        # Ensure proportion doesn't exceed 100%
        if shipping_proportion > 1:
            self.logger.warning(
                f"Shipping proportion exceeds 100% ({shipping_proportion}) for order {order.name}, "
                "capping to 100%"
            )
            shipping_proportion = Decimal("1")

        proportional_shipping = original_shipping * shipping_proportion

        self.logger.debug(
            f"Proportional shipping calculation for order {order.name}: "
            f"returned_value={returned_items_value}, total_order_value={total_order_value}, "
            f"proportion={shipping_proportion}, original_shipping={original_shipping}, "
            f"proportional_shipping={proportional_shipping}"
        )

        return proportional_shipping

    def _calculate_proportional_tax_refund(
        self,
        returned_line_item_entries: List[tuple[LineItem, dict]],
    ):
        """Calculate proportional tax refund for returned line items based on quantities."""

        if not returned_line_item_entries:
            self.logger.debug("No returned line items for tax calculation")
            return Decimal("0")

        total_tax_refund = Decimal("0")
        for line_item_entry in returned_line_item_entries:
            order_line_item, return_line_item = line_item_entry
            returned_quantity = return_line_item["quantity"]

            # Validation: ensure returned quantity is valid
            if returned_quantity <= 0:
                self.logger.warning(
                    f"Invalid returned quantity {returned_quantity} for line item {order_line_item.id}"
                )
                continue

            if returned_quantity > order_line_item.quantity:
                self.logger.warning(
                    f"Returned quantity {returned_quantity} exceeds original quantity {order_line_item.quantity} "
                    f"for line item {order_line_item.id}, capping to original quantity"
                )
                returned_quantity = order_line_item.quantity

            # Process tax lines for this line item
            if not order_line_item.taxLines:
                self.logger.debug(
                    f"No tax lines found for line item {order_line_item.id}"
                )
                continue

            for tax_line in order_line_item.taxLines:
                try:
                    # Calculate tax per unit based on original quantity
                    total_tax_for_line_item = Decimal(
                        str(tax_line.priceSet.presentmentMoney.amount)
                    )

                    # Validation: ensure original quantity is valid
                    if order_line_item.quantity <= 0:
                        self.logger.error(
                            f"Invalid original quantity {order_line_item.quantity} for line item {order_line_item.id}"
                        )
                        continue

                    # Calculate tax per unit using original quantity
                    tax_per_unit = total_tax_for_line_item / Decimal(
                        str(order_line_item.quantity)
                    )

                    # Calculate proportional tax refund for returned quantity
                    line_item_tax_refund = tax_per_unit * Decimal(
                        str(returned_quantity)
                    )
                    total_tax_refund += line_item_tax_refund

                    self.logger.debug(
                        f"Partial refund - Line item {order_line_item.id} tax: "
                        f"total={total_tax_for_line_item}, per_unit={tax_per_unit}, "
                        f"returned_qty={returned_quantity}, refund={line_item_tax_refund} "
                        f"(title: {tax_line.title}, rate: {tax_line.rate})"
                    )

                except (ValueError, TypeError, ZeroDivisionError) as e:
                    self.logger.error(
                        f"Error calculating tax for line item {order_line_item.id}: {e}"
                    )
                    continue

        self.logger.info(
            f"Partial refund total tax calculated: LineItem({order_line_item.id}): {total_tax_refund}"
        )
        return total_tax_refund

    def _calculate_full_total_tax_refund(
        self, returned_line_item_entries: List[LineItem]
    ):
        """Calculate total tax refund for full refund - sum all tax amounts from all line items."""

        if not returned_line_item_entries:
            self.logger.debug("No line items for full refund tax calculation")
            return Decimal("0")

        total_tax_refund = Decimal("0")
        for line_item in returned_line_item_entries:
            if not line_item.taxLines:
                self.logger.debug(
                    f"No tax lines found for line item {line_item.id} in full refund"
                )
                continue

            # For full refunds, we refund all tax on each line item
            for tax_line in line_item.taxLines:
                try:
                    # Tax line already contains the total tax for this line item
                    tax_amount = Decimal(str(tax_line.priceSet.presentmentMoney.amount))

                    # Validation: ensure tax amount is non-negative
                    if tax_amount < 0:
                        self.logger.warning(
                            f"Negative tax amount {tax_amount} found for line item {line_item.id}, "
                            f"tax line: {tax_line.title}"
                        )
                        continue

                    total_tax_refund += tax_amount

                    self.logger.debug(
                        f"Full refund - Line item {line_item.id} tax: {tax_amount} "
                        f"(title: {tax_line.title}, rate: {tax_line.rate})"
                    )

                except (ValueError, TypeError) as e:
                    self.logger.error(
                        f"Error processing tax amount for line item {line_item.id}: {e}"
                    )
                    continue

        self.logger.info(f"Full refund total tax calculated: {total_tax_refund}")
        return total_tax_refund

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
                "amount": self.__normalize_amount(
                    proportional_amount
                ),
            }
            transactions.append(transaction_data)

        return transactions

    def _prepare_refund_transactions(self, order: ShopifyOrder) -> List[Dict]:
        """Prepare transaction data for full refund ."""

        transactions = []

        if not order.suggestedRefund.suggestedTransactions:
            return transactions

        for transaction in order.suggestedRefund.suggestedTransactions:
            data = {
                "orderId": order.id,
                "parentId": transaction.parentTransaction.id,
                "kind": "REFUND",
                "gateway": transaction.gateway,
                "amount": self.__normalize_amount(
                    transaction.amountSet.presentmentMoney.amount
                ),
            }
            transactions.append(data)

        return transactions

    def __normalize_amount(
        self, value: Union[str, int, float, Decimal], decimal_places: int = 2
    ) -> float:
        try:
            value_decimal = Decimal(str(value))
        except Exception as _:
            value_decimal = Decimal(value)
        quantize_str = f"1.{'0' * decimal_places}"
        normalized = value_decimal.quantize(Decimal(quantize_str), rounding=ROUND_DOWN)
        return float(normalized)


refund_calculator = RefundCalculator()
