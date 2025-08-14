from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MoneyBag(BaseModel):
    amount: float
    currencyCode: Optional[str] = Field(default=None)


class MoneyBagSet(BaseModel):
    presentmentMoney: MoneyBag
    shopMoney: MoneyBag = Field(default=None)


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


class SuggestedRefundRefundDuties(BaseModel):
    amountSet: MoneyBagSet
    originalDuty: Optional[str] = Field(default=None)


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
    refundDuties: list[SuggestedRefundRefundDuties]
    suggestedTransactions: Optional[List[SuggestedRefundSuggestedTransactions]]


class ShopifyOrder(BaseModel):
    id: str
    name: str
    tags: List[str]
    lineItems: List[LineItem]
    totalPriceSet: MoneyBagSet
    suggestedRefund: SuggestedRefund
    returns: List[ReturnFulfillments]
    transactions: List[OrderTransaction]

    def __str__(self):
        return f"ShopifyOrder: ({self.name}, {self.totalPriceSet.shopMoney.amount})"

    def __repr__(self):
        return f"ShopifyOrder(number={self.name}, priceAmount={self.totalPriceSet.shopMoney.amount})"

    @property
    def valid_return_shipment(self):
        for returns in self.returns:
            for rfo in returns.reverseFulfillmentOrders:
                for rd in rfo.reverseDeliveries:
                    if (
                        rd.deliverable.tracking.number
                        and rd.deliverable.tracking.carrierName
                    ):
                        return returns

        return None
