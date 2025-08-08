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


class LineItem(BaseModel):
    id: str
    quantity: int
    refundableQuantity: int


class ReturnFulfillments(BaseModel):
    id: str
    name: str
    reverseFulfillmentOrders: List[ReverseFulfillmentOrder]


class RefundCreateResponse(BaseModel):
    id: str
    orderId: str
    orderName: str
    createdAt: str
    totalRefundedSet: MoneyBagSet


class TransactionKind(Enum):
    SALE = "SALE"
    REFUND = "REFUND"


class OrderTransaction(BaseModel):
    id: str
    gateway: str
    kind: TransactionKind
    amountSet: MoneyBagSet
    orderid: Optional[str] = Field(default=None)


class ShopifyOrder(BaseModel):
    id: str
    name: str
    tags: List[str]
    lineItems: List[LineItem]
    totalPriceSet: MoneyBagSet
    transactions: List[OrderTransaction]
    returns: List[ReturnFulfillments]

    def __str__(self):
        return f"ShopifyOrder: ({self.name}, {self.totalPriceSet.shopMoney.amount})"

    def __repr__(self):
        return f"ShopifyOrder(number={self.name}, priceAmount={self.totalPriceSet.shopMoney.amount})"

    @property
    def valid_return_shipment(self):
        for returns in self.returns:
            for rf in returns.reverseFulfillmentOrders:
                for rd in rf.reverseDeliveries:
                    if (
                        rd.deliverable.tracking.number
                        and rd.deliverable.tracking.carrierName
                    ):
                        return returns
