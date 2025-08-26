from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MoneyBag(BaseModel):
    amount: float
    currencyCode: str


class MoneyBagSet(BaseModel):
    presentmentMoney: MoneyBag
    shopMoney: Optional[MoneyBag] = Field(default=None)


class DiscountAllocation(BaseModel):
    allocatedAmountSet: MoneyBagSet


class TaxLine(BaseModel):
    title: str
    rate: float
    priceSet: MoneyBagSet


class LineItem(BaseModel):
    id: str
    quantity: int
    refundableQuantity: int
    originalTotalSet: MoneyBagSet
    discountAllocations: List[DiscountAllocation] = Field(default_factory=list)
    taxLines: List[TaxLine] = Field(default_factory=list)


class FulfillmentLineItem(BaseModel):
    lineItem: dict  # Contains id field


class ReturnLineItem(BaseModel):
    id: str
    quantity: int
    refundableQuantity: int = Field(default=0)
    fulfillmentLineItem: FulfillmentLineItem


class DeliverableTracking(BaseModel):
    url: Optional[str] = Field(default=None)
    number: Optional[str] = Field(default=None)
    carrierName: Optional[str] = Field(default=None)


class Deliverable(BaseModel):
    tracking: DeliverableTracking


class ReverseDeliveries(BaseModel):
    deliverable: Deliverable


class ReverseFulfillmentOrder(BaseModel):
    reverseDeliveries: List[ReverseDeliveries]


class ReverseFulfillment(BaseModel):
    id: str
    name: str
    status: Optional[str] = Field(default=None)
    returnLineItems: List[ReturnLineItem] = Field(default_factory=list)
    reverseFulfillmentOrders: List[ReverseFulfillmentOrder]

    returned_amount: float = Field(default=0.0)

    @property
    def tracking_number(self):
        for rfo in self.reverseFulfillmentOrders:
            for rd in rfo.reverseDeliveries:
                if rd.deliverable.tracking.number:
                    return rd.deliverable.tracking.number


class RefundCreateResponse(BaseModel):
    id: str
    orderId: str
    orderName: str
    createdAt: str
    totalRefundedSet: MoneyBagSet


class TransactionKind(str, Enum):
    VOID = "VOID"
    SALE = "SALE"
    REFUND = "REFUND"
    CAPTURE = "CAPTURE"
    CHANGE = "CHANGE"
    SUGGESTED_REFUND = "SUGGESTED_REFUND"
    _UNKNOWN = "UNKNOWN"


class OrderTransaction(BaseModel):
    id: str
    gateway: str
    kind: TransactionKind
    amountSet: MoneyBagSet
    orderId: Optional[str] = Field(default=None)

    def __missing__(self, v):
        return TransactionKind._UNKNOWN  # Fallback to default


class SuggestedRefundRefundShipping(BaseModel):
    amountSet: MoneyBagSet


class SuggestedRefundParentTransaction(BaseModel):
    id: str


class SuggestedRefundSuggestedTransactions(BaseModel):
    kind: str
    gateway: str
    amountSet: MoneyBagSet
    parentTransaction: SuggestedRefundParentTransaction


class SuggestedRefund(BaseModel):
    amountSet: MoneyBagSet
    shipping: SuggestedRefundRefundShipping
    suggestedTransactions: Optional[List[SuggestedRefundSuggestedTransactions]]


class RefundLineItems(BaseModel):
    lineItem: dict
    quantity: int


class Refund(BaseModel):
    createdAt: Optional[str] = Field(default=None)
    totalRefundedSet: Optional[MoneyBagSet] = Field(default=None)
    refundLineItems: list[RefundLineItems] = Field(default_factory=list)


class DiscountApplication(BaseModel):
    allocationMethod: str
    targetSelection: str
    targetType: str


class OrderDispute(BaseModel):
    status: str
    initiatedAs: str

    def is_chargeback(self):
        opened_statuses = ["NEEDS_RESPONSE", "UNDER_REVIEW"]

        # Make sure this dispute is open
        if self.status.upper() in opened_statuses:
            return "chargeback" == self.initiatedAs.lower()

        return False


class ShopifyOrder(BaseModel):
    id: str
    name: str
    tags: List[str]
    lineItems: List[LineItem]
    totalPriceSet: MoneyBagSet
    totalShippingPriceSet: MoneyBagSet
    totalRefundedShippingSet: MoneyBagSet
    discountApplications: List[dict] = Field(default_factory=list)
    suggestedRefund: SuggestedRefund
    refunds: List[Refund] = Field(default_factory=list)
    returns: List[ReverseFulfillment] = Field(default_factory=list)
    disputes: List[OrderDispute] = Field(default_factory=list)
    transactions: List[OrderTransaction] = Field(default_factory=list)

    return_id: Optional[str] = Field(default=None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # self.__filter_out_already_refunded_return_line_items()

    def __str__(self):
        return (
            f"ShopifyOrder: ({self.name}, {self.totalPriceSet.presentmentMoney.amount})"
        )

    def __repr__(self):
        return f"ShopifyOrder(number={self.name}, priceAmount={self.totalPriceSet.presentmentMoney.amount})"

    @property
    def tracking_number(self):
        # TODO Clear all occurrences
        return "DUMMY_TRACKING_NUMBER"

    @property
    def priorRefundAmount(self):
        total = 0.0
        for refund in self.refunds:
            amount = refund.totalRefundedSet.presentmentMoney.amount
            if refund.createdAt and amount is not None:
                total += amount
        return total

    def get_valid_return_shipment(self):
        """
        Helper method to get the all valid return shipment from the order.

        Returns:
            List[ReverseFulfillment]: A list of valid return fulfillments that have:
                - An "OPEN" status
                - Return line items
                - Reverse fulfillment orders with tracking numbers
        """

        valid_return_fulfillments: list[ReverseFulfillment] = []

        for return_fulfillment in self.returns:
            if (
                return_fulfillment.status == "OPEN"
                and return_fulfillment.returnLineItems
                and return_fulfillment.reverseFulfillmentOrders
            ):
                for rfo in return_fulfillment.reverseFulfillmentOrders:
                    for rd in rfo.reverseDeliveries:
                        if rd.deliverable.tracking.number:
                            valid_return_fulfillments.append(return_fulfillment)

        return valid_return_fulfillments

    def __filter_out_already_refunded_return_line_items(self):
        """
        Filters out return line items that have already been fully refunded.
        It iterates through the refunds and return fulfillments of the order,
        adjusting the quantities of return line items based on the refunded quantities.
        """

        refunded_line_item_quantities = {}

        for refund in self.refunds:
            # Skip refunds that don't have a creation date or a refunded amount.
            # This values are available only when refunds are actually made
            if (
                not refund.createdAt
                or not refund.totalRefundedSet.presentmentMoney.amount
            ):
                # This refund was not process
                continue

            for refund_line_item in refund.refundLineItems:
                line_item_id = refund_line_item.lineItem["id"]
                refunded_quantity = refund_line_item.quantity

                # Update the refunded quantity in the dictionary.
                if line_item_id in refunded_line_item_quantities:
                    refunded_line_item_quantities[line_item_id] += refunded_quantity
                else:
                    refunded_line_item_quantities[line_item_id] = refunded_quantity

        for return_fulfillment in self.returns:
            if not return_fulfillment.status == "OPEN":
                return_fulfillment.returnLineItems = []
                continue

            return_line_items = []

            # Iterate through each return line item in the return fulfillment.
            for return_line_item in return_fulfillment.returnLineItems:
                line_item_id = return_line_item.fulfillmentLineItem.lineItem["id"]
                return_quantity = return_line_item.quantity

                # Get the refunded quantity for the line item from the dictionary.
                refunded_quantity = refunded_line_item_quantities.get(line_item_id, 0)
                if refunded_quantity >= return_quantity:
                    continue
                elif refunded_quantity > 0:
                    return_line_item.quantity = return_quantity - refunded_quantity
                    refunded_line_item_quantities[line_item_id] = return_quantity

                return_line_items.append(return_line_item)

            # Update the return fulfillment's return line items with the filtered list.
            return_fulfillment.returnLineItems = return_line_items
