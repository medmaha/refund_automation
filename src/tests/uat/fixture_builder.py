import uuid

from src.models.order import (
    Deliverable,
    DeliverableTracking,
    DiscountAllocation,
    FulfillmentLineItem,
    LineItem,
    MoneyBag,
    MoneyBagSet,
    OrderRefunds,
    OrderTransaction,
    ReturnFulfillments,
    ReturnLineItem,
    ReverseDeliveries,
    ReverseFulfillmentOrder,
    ShopifyOrder,
    SuggestedRefund,
    SuggestedRefundParentTransaction,
    SuggestedRefundRefundShipping,
    SuggestedRefundSuggestedTransactions,
    TaxLine,
    TransactionKind,
)
from src.tests.uat.uat_constants import UATConstants


class FixtureBuilder:
    _order_data = {}

    def build(self) -> ShopifyOrder:
        """Build the complete ShopifyOrder object."""
        # Calculate total amounts
        line_items_total = sum(
            item["price"] * item["quantity"] for item in self._order_data["line_items"]
        )
        if not line_items_total:
            line_items_total = self._order_data["base_amount"]

        discount_total = sum(d["amount"] for d in self._order_data["discounts"])
        tax_rate = self._order_data["tax_rate"]
        tax_amount = self._order_data["tax_amount"]
        shipping_amount = self._order_data["shipping_amount"]

        total_amount = line_items_total - discount_total + tax_amount + shipping_amount

        # Build money sets
        currency = self._order_data["currency"]
        total_money_set = MoneyBagSet(
            shopMoney=MoneyBag(amount=total_amount, currencyCode=currency),
            presentmentMoney=MoneyBag(amount=total_amount, currencyCode=currency),
        )

        # Build line items
        line_items = []
        for i, item_data in enumerate(self._order_data["line_items"]):
            item_total = item_data["price"] * item_data.get(
                "quantity", "refundableQuantity"
            )

            # Apply line-level discounts
            discounts = []
            for discount in self._order_data["discounts"]:
                if (
                    discount["type"] == UATConstants.LINE_LEVEL_FIXED
                    and discount["line_item_index"] == i
                ):
                    discount_money = MoneyBagSet(
                        presentmentMoney=MoneyBag(
                            amount=discount["amount"], currencyCode=currency
                        ),
                        shopMoney=MoneyBag(
                            amount=discount["amount"], currencyCode=currency
                        ),
                    )
                    discounts.append(
                        DiscountAllocation(allocatedAmountSet=discount_money)
                    )

            # Add taxes if applicable
            tax_lines = []
            if tax_amount > 0:
                item_tax = (item_total / line_items_total) * tax_amount
                tax_money = MoneyBagSet(
                    shopMoney=MoneyBag(amount=item_tax, currencyCode=currency),
                    presentmentMoney=MoneyBag(amount=item_tax, currencyCode=currency),
                )
                tax_lines.append(
                    TaxLine(
                        title="VAT" if currency in ["EUR", "GBP"] else "Sales Tax",
                        rate=(
                            UATConstants.VAT_RATE
                            if currency in ["EUR", "GBP"]
                            else UATConstants.SALES_TAX_RATE
                        ),
                        priceSet=tax_money,
                    )
                )

            original_total_set = MoneyBagSet(
                shopMoney=MoneyBag(amount=item_total, currencyCode=currency),
                presentmentMoney=MoneyBag(amount=item_total, currencyCode=currency),
            )

            line_items.append(
                LineItem(
                    id=item_data["id"],
                    quantity=item_data["quantity"],
                    originalTotalSet=original_total_set,
                    discountAllocations=discounts,
                    taxLines=tax_lines,
                    refundableQuantity=item_data.get("quantity", "refundableQuantity"),
                )
            )

        # Build transactions
        transactions = []
        for trans_data in self._order_data["transactions"]:
            amount_set = MoneyBagSet(
                shopMoney=MoneyBag(amount=trans_data["amount"], currencyCode=currency),
                presentmentMoney=MoneyBag(
                    amount=trans_data["amount"], currencyCode=currency
                ),
            )
            transactions.append(
                OrderTransaction(
                    id=trans_data["id"],
                    gateway=trans_data["gateway"],
                    kind=trans_data["kind"],
                    amountSet=amount_set,
                )
            )

        # Build suggested refund with transactions
        suggested_transactions = []
        for trans in transactions:
            if trans.kind == TransactionKind.SALE:
                suggested_transactions.append(
                    SuggestedRefundSuggestedTransactions(
                        kind="SUGGESTED_REFUND",
                        gateway=trans.gateway,
                        amountSet=trans.amountSet,
                        parentTransaction=SuggestedRefundParentTransaction(id=trans.id),
                    )
                )

        shipping_refund_set = MoneyBagSet(
            shopMoney=MoneyBag(
                amount=(
                    shipping_amount
                    if self._order_data.get("shipping_refundable", True)
                    else 0.0
                ),
                currencyCode=currency,
            ),
            presentmentMoney=MoneyBag(
                amount=(
                    shipping_amount
                    if self._order_data.get("shipping_refundable", True)
                    else 0.0
                ),
                currencyCode=currency,
            ),
        )

        suggested_refund = SuggestedRefund(
            amountSet=total_money_set,
            shipping=SuggestedRefundRefundShipping(amountSet=shipping_refund_set),
            suggestedTransactions=suggested_transactions,
        )

        # Build returns
        returns = []
        for return_fulfillment in self._order_data["returns"]:
            # Create return line items for all line items
            return_line_items = [
                ReturnLineItem(
                    id=f"gid://shopify/ReturnLineItem/{uuid.uuid4().hex[:8]}",
                    quantity=line_item.get("refundableQuantity"),
                    refundableQuantity=line_item.get("refundableQuantity"),
                    fulfillmentLineItem=FulfillmentLineItem(
                        lineItem={
                            "id": line_item.get("id"),
                            "quantity": line_item.get("refundableQuantity"),
                        },
                    ),
                    returnReason="",
                    returnReasonNote="",
                )
                for line_item in return_fulfillment.get("returnLineItems", [])
            ]

            # Create deliverable tracking
            tracking = DeliverableTracking(
                number=return_fulfillment["tracking_number"],
                carrierName=return_fulfillment["carrier"],
            )
            deliverable = Deliverable(tracking=tracking)
            reverse_delivery = ReverseDeliveries(deliverable=deliverable)
            reverse_fulfillment_order = ReverseFulfillmentOrder(
                reverseDeliveries=[reverse_delivery]
            )

            returns.append(
                ReturnFulfillments(
                    id=return_fulfillment["id"],
                    name=f"R-{return_fulfillment['id'].split('/')[-1][:6]}",
                    status=return_fulfillment.get("status", "CLOSED"),
                    returnLineItems=return_line_items,
                    reverseFulfillmentOrders=[reverse_fulfillment_order],
                )
            )

        # Build existing refunds
        refunds = []
        for refund_data in self._order_data["refunds"]:
            refund_money_set = MoneyBagSet(
                shopMoney=MoneyBag(amount=refund_data["amount"], currencyCode=currency),
                presentmentMoney=MoneyBag(
                    amount=refund_data["amount"], currencyCode=currency
                ),
            )
            refunds.append(
                OrderRefunds(
                    createdAt=refund_data["created_at"],
                    totalRefundedSet=refund_money_set,
                    refundLineItems=[],
                )
            )

        return ShopifyOrder(
            id=self._order_data["id"],
            name=self._order_data["name"],
            tags=self._order_data["tags"],
            lineItems=line_items,
            totalPriceSet=total_money_set,
            suggestedRefund=suggested_refund,
            refunds=refunds,
            returns=returns,
            transactions=transactions,
        )
