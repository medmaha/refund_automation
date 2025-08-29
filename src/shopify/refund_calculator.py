from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel

from src.config import REFUND_FULL_SHIPPING, REFUND_PARTIAL_SHIPPING
from src.logger import get_logger
from src.models.order import (
    LineItem,
    ReturnLineItem,
    ReverseFulfillment,
    ShopifyOrder,
    TransactionKind,
)

logger = get_logger(__name__)


class RefundType(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"


@dataclass
class LineItemRefundData:
    """Data structure for line item refund calculations"""

    line_item: LineItem
    refund_quantity: int
    base_amount_per_unit: Decimal
    discount_per_unit: Decimal
    net_amount_per_unit: Decimal
    total_refund_amount: Decimal
    tax_refund_amount: Decimal


@dataclass
class RefundAmounts:
    """Data structure for calculated refund amounts"""

    line_items_total: Decimal
    shipping: Decimal
    tax: Decimal
    total: Decimal


@dataclass
class OrderFinancials:
    """Data structure for order financial data"""

    original_total: Decimal
    original_shipping: Decimal
    prior_refund_total: Decimal
    prior_refund_shipping: Decimal
    remaining_total: Decimal
    remaining_shipping: Decimal


class RefundCalculationResult(BaseModel):
    """Result of refund calculations."""

    refund_type: Literal["FULL", "PARTIAL"]

    refund_type = RefundType
    order_total: float = 0.0
    prior_refund: float = 0.0
    tax_refund: float = 0.0
    shipping_refund: float = 0.0
    discount_deduction: float = 0.0
    total_refund_amount: float
    is_last_partial: bool = False
    full_return_shipping: Optional[str] = None
    partial_return_shipping: Optional[str] = None
    line_items_to_refund: List[Dict]
    transactions: List[Dict]
    currency: str

    def __init__(self, **kwargs):
        kwargs.setdefault(
            "full_return_shipping",
            "Policy OFF" if not REFUND_FULL_SHIPPING else "Policy ON",
        )
        kwargs.setdefault(
            "partial_return_shipping",
            "Policy OFF" if not REFUND_PARTIAL_SHIPPING else "Policy ON",
        )
        super().__init__(**kwargs)


class RefundCalculator:
    """Handles refund calculations based on business rules."""

    def __init__(self):
        self.logger = logger

    def calculate_refund(
        self, order: ShopifyOrder, reverse_fulfillment: ReverseFulfillment
    ) -> RefundCalculationResult:
        """Main entry point for refund calculations."""
        returned_line_items = self._get_returned_line_items(reverse_fulfillment)

        if not returned_line_items or self._is_full_return(order, returned_line_items):
            refund_type = RefundType.FULL
            self.logger.info(
                f"Calculating full refund for Order {order.name} Refund({reverse_fulfillment.name})"
            )
        else:
            refund_type = RefundType.PARTIAL
            self.logger.info(
                f"Calculating partial refund for Order {order.name} Refund({reverse_fulfillment.name})"
            )

        return self._calculate_refund_by_type(
            order, reverse_fulfillment, returned_line_items, refund_type
        )

    def _calculate_refund_by_type(
        self,
        order: ShopifyOrder,
        reverse_fulfillment: ReverseFulfillment,
        returned_line_items: List[ReturnLineItem],
        refund_type: RefundType,
    ) -> RefundCalculationResult:
        """Calculate refund based on type with unified logic."""

        # Get order financial data
        order_financials = self._get_order_financials(order)

        # Determine if last partial refund
        is_last_partial = (
            refund_type == RefundType.PARTIAL
            and self._is_last_partial_refund(order, reverse_fulfillment)
        )

        # Calculate line item refunds
        line_item_refunds = self._calculate_line_item_refunds(
            order, returned_line_items, refund_type
        )

        # Calculate refund amounts
        refund_amounts = self._calculate_refund_amounts(
            order, order_financials, line_item_refunds, refund_type, is_last_partial
        )

        # Prepare line items for refund
        refund_line_items = self._prepare_refund_line_items(line_item_refunds)

        # Calculate transactions
        transactions = self._calculate_transactions(
            order, refund_amounts, order_financials, refund_type
        )

        return RefundCalculationResult(
            refund_type=refund_type.value,
            total_refund_amount=self._normalize_amount(refund_amounts.total),
            shipping_refund=self._normalize_amount(refund_amounts.shipping),
            tax_refund=self._normalize_amount(refund_amounts.tax),
            prior_refund=self._normalize_amount(order_financials.prior_refund_total),
            order_total=self._normalize_amount(order_financials.original_total),
            line_items_to_refund=refund_line_items,
            transactions=transactions,
            is_last_partial=is_last_partial,
            currency=order.totalPriceSet.presentmentMoney.currencyCode,
        )

    def _get_order_financials(self, order: ShopifyOrder) -> OrderFinancials:
        """Extract and calculate all order financial data."""
        original_total = Decimal(str(order.totalPriceSet.presentmentMoney.amount))
        original_shipping = Decimal(
            str(order.totalShippingPriceSet.presentmentMoney.amount)
        )
        prior_refund_total = Decimal(str(order.priorRefundAmount))
        prior_refund_shipping = Decimal(
            str(order.totalRefundedShippingSet.presentmentMoney.amount)
        )

        return OrderFinancials(
            original_total=original_total,
            original_shipping=original_shipping,
            prior_refund_total=prior_refund_total,
            prior_refund_shipping=prior_refund_shipping,
            remaining_total=max(original_total - prior_refund_total, Decimal("0")),
            remaining_shipping=max(
                original_shipping - prior_refund_shipping, Decimal("0")
            ),
        )

    def _calculate_line_item_refunds(
        self,
        order: ShopifyOrder,
        returned_line_items: List[ReturnLineItem],
        refund_type: RefundType,
    ) -> List[LineItemRefundData]:
        """Calculate refund data for line items with unified logic."""

        line_item_refunds: list[LineItemRefundData] = []

        if refund_type == RefundType.FULL:
            # Full refund: refund all line items
            for line_item in order.lineItems:
                refund_data = self._calculate_line_item_refund_data(
                    line_item, line_item.refundableQuantity
                )
                line_item_refunds.append(refund_data)
        else:
            # Partial refund: only returned items
            returned_qty_map = self._build_returned_quantity_map(returned_line_items)

            for line_item in order.lineItems:
                returned_qty = returned_qty_map.get(line_item.id, 0)
                if returned_qty > 0:
                    refund_qty = min(returned_qty, line_item.refundableQuantity)
                    refund_data = self._calculate_line_item_refund_data(
                        line_item, refund_qty
                    )
                    line_item_refunds.append(refund_data)

        return line_item_refunds

    def _calculate_line_item_refund_data(
        self, line_item: LineItem, refund_qty: int
    ) -> LineItemRefundData:
        """Calculate detailed refund data for a single line item."""
        # Calculate base amounts
        base_total = Decimal(str(line_item.originalTotalSet.presentmentMoney.amount))
        base_amount_per_unit = (
            base_total / Decimal(str(line_item.quantity))
            if line_item.quantity > 0
            else Decimal("0")
        )

        # Calculate discount per unit
        total_discount = sum(
            Decimal(str(alloc.allocatedAmountSet.presentmentMoney.amount))
            for alloc in line_item.discountAllocations
        )
        discount_per_unit = (
            total_discount / Decimal(str(line_item.quantity))
            if line_item.quantity > 0
            else Decimal("0")
        )

        # Calculate net amount
        net_amount_per_unit = base_amount_per_unit - discount_per_unit
        total_refund_amount = net_amount_per_unit * Decimal(str(refund_qty))

        # Calculate tax refund
        tax_refund_amount = self._calculate_line_item_tax_refund(line_item, refund_qty)

        return LineItemRefundData(
            line_item=line_item,
            refund_quantity=refund_qty,
            base_amount_per_unit=base_amount_per_unit,
            discount_per_unit=discount_per_unit,
            net_amount_per_unit=net_amount_per_unit,
            total_refund_amount=total_refund_amount,
            tax_refund_amount=tax_refund_amount,
        )

    def _calculate_line_item_tax_refund(
        self, line_item: LineItem, refund_qty: int
    ) -> Decimal:
        """Calculate tax refund for a specific line item and quantity."""
        if not line_item.taxLines or line_item.quantity <= 0:
            return Decimal("0")

        total_tax = Decimal("0")
        for tax_line in line_item.taxLines:
            try:
                tax_amount = Decimal(str(tax_line.priceSet.presentmentMoney.amount))
                if tax_amount < 0:
                    self.logger.warning(
                        f"Negative tax amount {tax_amount} for line item {line_item.id}"
                    )
                    continue

                tax_per_unit = tax_amount / Decimal(str(line_item.quantity))
                line_tax_refund = tax_per_unit * Decimal(str(refund_qty))
                total_tax += line_tax_refund

            except (ValueError, TypeError, ZeroDivisionError) as e:
                self.logger.error(
                    f"Error calculating tax for line item {line_item.id}: {e}"
                )
                continue

        return total_tax

    def _calculate_refund_amounts(
        self,
        order: ShopifyOrder,
        order_financials: OrderFinancials,
        line_item_refunds: List[LineItemRefundData],
        refund_type: RefundType,
        is_last_partial: bool,
    ) -> RefundAmounts:
        """Calculate all refund amounts with capping logic."""

        # Sum line item amounts
        line_items_total = sum(
            refund.total_refund_amount for refund in line_item_refunds
        )
        tax_total = sum(refund.tax_refund_amount for refund in line_item_refunds)

        # Calculate shipping refund
        shipping_refund = self._calculate_shipping_refund(
            order, order_financials, line_item_refunds, refund_type
        )

        # Calculate total
        total_refund = Decimal(
            str(self._normalize_amount(line_items_total + shipping_refund + tax_total))
        )

        # Apply capping for last partial refund
        if is_last_partial:
            total_refund, shipping_refund = self._apply_last_partial_capping(
                order, order_financials, total_refund, shipping_refund
            )

        return RefundAmounts(
            line_items_total=line_items_total,
            shipping=shipping_refund,
            tax=tax_total,
            total=total_refund,
        )

    def _calculate_shipping_refund(
        self,
        order: ShopifyOrder,
        order_financials: OrderFinancials,
        line_item_refunds: List[LineItemRefundData],
        refund_type: RefundType,
    ) -> Decimal:
        """Calculate shipping refund based on refund type and policies."""

        if refund_type == RefundType.FULL:
            return (
                order_financials.original_shipping
                if REFUND_FULL_SHIPPING
                else Decimal("0")
            )

        # Partial refund shipping calculation
        if not REFUND_PARTIAL_SHIPPING or order_financials.original_shipping <= 0:
            return Decimal("0")

        if order_financials.prior_refund_shipping >= order_financials.original_shipping:
            return Decimal("0")

        return self._calculate_proportional_shipping(order, line_item_refunds)

    def _calculate_proportional_shipping(
        self, order: ShopifyOrder, line_item_refunds: List[LineItemRefundData]
    ) -> Decimal:
        """Calculate proportional shipping refund."""
        if not line_item_refunds:
            return Decimal("0")

        # Calculate total order value (net of discounts)
        total_order_value = sum(
            self._calculate_line_item_net_value(line_item)
            for line_item in order.lineItems
        )

        if total_order_value <= 0:
            return Decimal("0")

        # Calculate returned items value
        returned_items_value = sum(
            refund.total_refund_amount for refund in line_item_refunds
        )

        if returned_items_value <= 0:
            return Decimal("0")

        # Calculate proportion and apply to shipping
        proportion = min(returned_items_value / total_order_value, Decimal("1"))
        original_shipping = Decimal(
            str(order.totalShippingPriceSet.presentmentMoney.amount)
        )

        return Decimal(str(self._normalize_amount(original_shipping * proportion)))

    def _calculate_line_item_net_value(self, line_item: LineItem) -> Decimal:
        """Calculate net value of a line item after discounts."""
        try:
            original_total = Decimal(
                str(line_item.originalTotalSet.presentmentMoney.amount)
            )
            total_discount = sum(
                Decimal(str(alloc.allocatedAmountSet.presentmentMoney.amount))
                for alloc in line_item.discountAllocations
            )
            return max(original_total - total_discount, Decimal("0"))
        except (ValueError, TypeError) as e:
            self.logger.error(
                f"Error calculating net value for line item {line_item.id}: {e}"
            )
            return Decimal("0")

    def _apply_last_partial_capping(
        self,
        order: ShopifyOrder,
        order_financials: OrderFinancials,
        total_refund: Decimal,
        shipping_refund: Decimal,
    ) -> Tuple[Decimal, Decimal]:
        """Apply capping logic for last partial refund."""

        self.logger.info(
            f"Last partial refund detected for order {order.name}. "
            f"Calculated: {total_refund}, Remaining: {order_financials.remaining_total}"
        )

        # Cap total refund
        if total_refund != order_financials.remaining_total:
            self.logger.warning(
                f"Capping total refund from {total_refund} to {order_financials.remaining_total}"
            )
            total_refund = order_financials.remaining_total

        # Cap shipping refund
        if shipping_refund != order_financials.remaining_shipping:
            self.logger.warning(
                f"Capping shipping refund from {shipping_refund} to {order_financials.remaining_shipping}"
            )
            shipping_refund = order_financials.remaining_shipping

        return total_refund, shipping_refund

    def _calculate_transactions(
        self,
        order: ShopifyOrder,
        refund_amounts: RefundAmounts,
        order_financials: OrderFinancials,
        refund_type: RefundType,
    ) -> List[Dict]:
        """Calculate transaction allocations."""

        if not order.suggestedRefund.suggestedTransactions:
            return []

        transactions = []

        for transaction in order.suggestedRefund.suggestedTransactions:
            if transaction.kind not in [
                TransactionKind.SALE,
                TransactionKind.SUGGESTED_REFUND,
            ]:
                continue

            original_amount = Decimal(
                str(transaction.amountSet.presentmentMoney.amount)
            )

            if refund_type == RefundType.FULL:
                # Full refund: use original amount minus shipping if policy is off
                refund_amount = original_amount
                if not REFUND_FULL_SHIPPING:
                    refund_amount -= order_financials.original_shipping
            else:
                # Partial refund: calculate proportional amount
                if order_financials.original_total > 0:
                    proportion = (
                        refund_amounts.total + order_financials.prior_refund_total
                    ) / order_financials.original_total
                    refund_amount = max(original_amount * proportion, Decimal("0"))
                else:
                    refund_amount = Decimal("0")

            transactions.append(
                {
                    "orderId": order.id,
                    "parentId": transaction.parentTransaction.id,
                    "kind": TransactionKind.REFUND,
                    "gateway": transaction.gateway,
                    "amount": self._normalize_amount(refund_amount),
                }
            )

        return transactions

    # Utility methods
    def _get_returned_line_items(
        self, reverse_fulfillment: ReverseFulfillment
    ) -> List[ReturnLineItem]:
        """Extract returned line items from reverse fulfillment."""
        returned_items = []

        for li in reverse_fulfillment.returnLineItems:
            original_qty = li.fulfillmentLineItem.lineItem.get("quantity", 0)
            refundable_qty = li.refundableQuantity
            if refundable_qty > 0 and refundable_qty <= original_qty:
                returned_items.append(li)

        return returned_items

    def _is_full_return(
        self, order: ShopifyOrder, returned_line_items: List[ReturnLineItem]
    ) -> bool:
        """Determine if this is a full return."""

        returned_qty_map = self._build_returned_quantity_map(returned_line_items)

        return all(
            returned_qty_map.get(line_item.id, 0) >= line_item.quantity
            for line_item in order.lineItems
        )

    def _build_returned_quantity_map(
        self, returned_line_items: List[ReturnLineItem]
    ) -> Dict[str, int]:
        """Build a map of line item ID to returned quantity."""
        qty_map = {}
        for returned_item in returned_line_items:
            line_item_id = returned_item.fulfillmentLineItem.lineItem.get("id")
            if line_item_id:
                qty_map[line_item_id] = (
                    qty_map.get(line_item_id, 0) + returned_item.refundableQuantity
                )
        return qty_map

    def _is_last_partial_refund(
        self, order: ShopifyOrder, reverse_fulfillment: ReverseFulfillment
    ) -> bool:
        """Determine if this is the last partial refund possible."""
        current_return_qty_map = self._build_returned_quantity_map(
            reverse_fulfillment.returnLineItems
        )
        refunded_qty_map = self._build_refunded_quantity_map(order)
        other_pending_qty_map = self._build_other_pending_returns_map(
            order, reverse_fulfillment.id
        )

        # Determine if processing this will exhaust available returns
        for line_item in order.lineItems:
            line_item_id = line_item.id
            original_qty = line_item.quantity

            # Get quantities from maps
            already_refunded = refunded_qty_map.get(line_item_id, 0)
            current_return_qty = current_return_qty_map.get(line_item_id, 0)
            other_pending_qty = other_pending_qty_map.get(line_item_id, 0)

            # Calculate remaining quantity after current return
            remaining_qty = original_qty - already_refunded - current_return_qty

            # If there's remaining quantity and other pending returns, it's not the last partial
            if remaining_qty > 0 and other_pending_qty > 0:
                return False

            # If there's remaining quantity but no other pending returns, it's the last partial
            if remaining_qty > 0 and other_pending_qty == 0:
                return False

        return True

    def _build_refunded_quantity_map(self, order: ShopifyOrder) -> Dict[str, int]:
        """Build map of already refunded quantities."""
        refunded_qty_map = {}
        for refund in order.refunds:
            if (
                not refund.createdAt
                or not refund.totalRefundedSet
                or not refund.totalRefundedSet.presentmentMoney.amount
            ):
                continue
            for refund_line_item in refund.refundLineItems:
                line_item_id = refund_line_item.lineItem.get("id")
                if line_item_id:
                    refunded_qty_map[line_item_id] = (
                        refunded_qty_map.get(line_item_id, 0)
                        + refund_line_item.quantity
                    )
        return refunded_qty_map

    def _build_other_pending_returns_map(
        self, order: ShopifyOrder, current_return_id: str
    ) -> Dict[str, int]:
        """Build map of quantities in other pending returns."""
        other_pending_qty_map = {}
        for other_return in order.returns:
            if (
                other_return.id == current_return_id
                or other_return.status != "OPEN"
                or not other_return.returnLineItems
            ):
                continue
            for return_item in other_return.returnLineItems:
                line_item_id = return_item.fulfillmentLineItem.lineItem.get("id")
                if line_item_id:
                    other_pending_qty_map[line_item_id] = (
                        other_pending_qty_map.get(line_item_id, 0)
                        + return_item.refundableQuantity
                    )
        return other_pending_qty_map

    def _prepare_refund_line_items(
        self, line_item_refunds: List[LineItemRefundData]
    ) -> List[Dict]:
        """Prepare line items for refund API."""
        return [
            {
                "lineItemId": refund.line_item.id,
                "quantity": refund.refund_quantity,
            }
            for refund in line_item_refunds
            if refund.refund_quantity
        ]

    def _normalize_amount(
        self, value: Union[str, int, float, Decimal], decimal_places: int = 2
    ) -> float:
        """Normalize monetary amounts to consistent format."""

        value_decimal = Decimal(str(value))
        quantize_str = f"1.{'0' * decimal_places}"
        normalized = value_decimal.quantize(Decimal(quantize_str), rounding=ROUND_DOWN)
        return float(normalized)


refund_calculator = RefundCalculator()
