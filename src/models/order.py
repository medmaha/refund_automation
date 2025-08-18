from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MoneyBag(BaseModel):
    amount: float
    currencyCode: Optional[str] = Field(default=None)


class MoneyBagSet(BaseModel):
    presentmentMoney: MoneyBag
    shopMoney: Optional[MoneyBag] = Field(default=None)


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
    returnReason: Optional[str] = Field(default=None)
    returnReasonNote: Optional[str] = Field(default=None)
    fulfillmentLineItem: FulfillmentLineItem


class ReturnFulfillments(BaseModel):
    id: str
    name: str
    status: Optional[str] = Field(default=None)
    returnLineItems: List[ReturnLineItem] = Field(default_factory=list)
    reverseFulfillmentOrders: List[ReverseFulfillmentOrder]


class RefundCreateResponse(BaseModel):
    id: str
    orderId: str
    orderName: str
    createdAt: str
    totalRefundedSet: MoneyBagSet


class TransactionKind(Enum):
    VOID = "VOID"
    SALE = "SALE"
    REFUND = "REFUND"
    CAPTURE = "CAPTURE"
    CHANGE = "CHANGE"
    SUGGESTED_REFUND = "SUGGESTED_REFUND"


class OrderTransaction(BaseModel):
    id: str
    gateway: str
    kind: TransactionKind
    amountSet: MoneyBagSet
    orderId: Optional[str] = Field(default=None)


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
    restockType: str


class OrderRefunds(BaseModel):
    createdAt: Optional[str] = Field(default=None)
    totalRefundedSet: Optional[MoneyBagSet] = Field(default=None)
    refundLineItems: Optional[list[RefundLineItems]] = Field(default_factory=list)
    refundShippingLines: list[dict] = Field(default_factory=list)


class DiscountApplication(BaseModel):
    allocationMethod: str
    targetSelection: str
    targetType: str


class ShopifyOrder(BaseModel):
    id: str
    name: str
    tags: List[str]
    lineItems: List[LineItem]
    totalPriceSet: MoneyBagSet
    totalRefundedShippingSet: Optional[MoneyBagSet] = Field(default=None)
    discountApplications: List[dict] = Field(default_factory=list)
    suggestedRefund: SuggestedRefund
    refunds: List[OrderRefunds]
    returns: List[ReturnFulfillments]
    transactions: List[OrderTransaction]

    return_id: Optional[str] = Field(default=None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.__filter_out_already_refunded_return_line_items()

    def __str__(self):
        return (
            f"ShopifyOrder: ({self.name}, {self.totalPriceSet.presentmentMoney.amount})"
        )

    def __repr__(self):
        return f"ShopifyOrder(number={self.name}, priceAmount={self.totalPriceSet.presentmentMoney.amount})"

    @property
    def tracking_number(self):
        return self.get_tracking_number()

    def get_tracking_number(self):
        for _return in self.returns:
            if _return.status == "OPEN":
                for rfo in _return.reverseFulfillmentOrders:
                    for rd in rfo.reverseDeliveries:
                        if rd.deliverable.tracking.number:
                            return rd.deliverable.tracking.number
        return None

    @property
    def priorRefundAmount(self):
        total = 0.0
        for refund in self.refunds:
            amount = refund.totalRefundedSet.presentmentMoney.amount
            if refund.createdAt and amount is not None:
                total += amount
        return total

    @property
    def valid_return_shipment(self):
        """
        Helper method to get the first valid return shipment from the order.
        * Can also be used as a flag.
        """

        for return_fulfillment in self.returns:
            if (
                return_fulfillment.status == "OPEN"
                and return_fulfillment.returnLineItems
                and return_fulfillment.reverseFulfillmentOrders
            ):
                for rfo in return_fulfillment.reverseFulfillmentOrders:
                    for rd in rfo.reverseDeliveries:
                        if rd.deliverable.tracking.number:
                            return return_fulfillment

        return None

    def __filter_out_already_refunded_return_line_items(self):
        refunded_line_item_quantities = {}

        for refund in self.refunds:
            if (
                not refund.createdAt
                or not refund.totalRefundedSet.presentmentMoney.amount
            ):
                continue

            if refund.refundLineItems:
                for refund_line_item in refund.refundLineItems:
                    line_item_id = refund_line_item.lineItem["id"]
                    refunded_quantity = refund_line_item.quantity
                    if line_item_id in refunded_line_item_quantities:
                        refunded_line_item_quantities[line_item_id] += refunded_quantity
                    else:
                        refunded_line_item_quantities[line_item_id] = refunded_quantity

        for return_fulfillment in self.returns:
            return_line_items = []
            for return_line_item in return_fulfillment.returnLineItems:
                line_item_id = return_line_item.fulfillmentLineItem.lineItem["id"]
                return_quantity = return_line_item.quantity

                refunded_quantity = refunded_line_item_quantities.get(line_item_id, 0)

                if refunded_quantity >= return_quantity:
                    continue
                elif refunded_quantity > 0:
                    return_line_item.quantity = return_quantity - refunded_quantity
                    refunded_line_item_quantities[line_item_id] = return_quantity

                return_line_items.append(return_line_item)
            return_fulfillment.returnLineItems = return_line_items
