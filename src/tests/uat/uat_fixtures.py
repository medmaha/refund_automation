"""
Comprehensive UAT test fixtures for refund automation scenarios.

This module provides all fixtures needed to test the UAT scenarios matrix:
- Full/Partial & Splits
- Discounts/Taxes/Shipping
- Tenders & Currencies
- Timing & Idempotency
- Tracking & Exceptions
- Blocks & Overrides
- Failures

Each fixture is designed to test specific business rules and edge cases.
"""

import uuid
from typing import Literal
from unittest.mock import Mock

from src.models.order import (
    ShopifyOrder,
    TransactionKind,
)
from src.models.tracking import (
    LatestEvent,
    LatestStatus,
    TrackInfo,
    TrackingData,
    TrackingStatus,
    TrackingSubStatus,
)
from src.tests.uat.fixture_builder import FixtureBuilder
from src.tests.uat.uat_constants import UATConstants
from src.utils.timezone import timezone_handler


class UATFixtureBuilder(FixtureBuilder):
    """Builder class for creating complex UAT test fixtures."""

    def __init__(self, status=None, sub_status=None, base_amount: int = None):
        self.__base_amount = base_amount
        self.__return_tracking_number = UATConstants.TRACKING_NUMBER

        self._order_data = {
            "id": f"gid://shopify/Order/{uuid.uuid4().hex[:8]}",
            "name": f"UAT-{uuid.uuid4().hex[:6].upper()}",
            "tags": [],
            "currency": UATConstants.USD,
            "base_amount": (self.__base_amount if self.__base_amount else 100.0),
            "line_items": [],
            "transactions": [],
            "returns": [],
            "refunds": [],
            "discounts": [],
            "tax_rate": UATConstants.VAT_RATE,
            "tax_amount": 0.0,
            "shipping_amount": 0.0,
        }

    def with_id_and_name(self, order_id: str = None, name: str = None):
        """Set custom order ID and name."""
        if order_id:
            self._order_data["id"] = order_id
        if name:
            self._order_data["name"] = name
        return self

    def with_currency(self, currency: str):
        """Set order currency."""
        self._order_data["currency"] = currency
        return self

    def with_base_amount(self, amount: float):
        """Set base order amount."""
        self._order_data["base_amount"] = amount
        return self

    def with_line_item(
        self,
        item_id: str = None,
        quantity: int = 1,
        price: float = 50.0,
        refundable_qty: int = None,
    ):
        """Add a line item to the order."""
        if item_id is None:
            item_id = f"gid://shopify/LineItem/{uuid.uuid4().hex[:8]}"
        if refundable_qty is None:
            refundable_qty = quantity

        self._order_data["line_items"].append(
            {
                "id": item_id,
                "quantity": quantity,
                "price": price,
                "refundableQuantity": refundable_qty,
            }
        )
        return self

    def with_return_line_item(
        self,
        item_id: str,
        refundable_qty: int = None,
    ):
        """Add a line item to the order returns."""

        for return_fulfillment in self._order_data["returns"]:
            return_line_items = return_fulfillment.get("returnLineItems", [])
            return_line_items.append(
                {
                    "id": item_id,
                    "quantity": refundable_qty,
                    "refundableQuantity": refundable_qty,
                }
            )
            return_fulfillment["returnLineItems"] = return_line_items

        return self

    def with_transaction(
        self,
        gateway: str,
        kind: TransactionKind,
        amount: float = None,
        parent_id: str = None,
    ):
        """Add a transaction to the order."""
        if amount is None:
            amount = self._order_data["base_amount"]

        transaction_id = f"gid://shopify/Transaction/{uuid.uuid4().hex[:8]}"
        self._order_data["transactions"].append(
            {
                "id": transaction_id,
                "gateway": gateway,
                "kind": kind,
                "amount": amount,
                "parent_id": parent_id,
            }
        )
        return self

    def with_gift_card_payment(self, amount: float):
        """Add gift card payment."""
        return self.with_transaction(
            UATConstants.GIFT_CARD, TransactionKind.SALE, amount
        )

    def with_store_credit_payment(self, amount: float):
        """Add store credit payment."""
        return self.with_transaction(
            UATConstants.STORE_CREDIT, TransactionKind.SALE, amount
        )

    def with_mixed_payment(
        self, gift_card_amount: float = None, card_amount: float = None
    ):
        """Add mixed payment (gift card + regular card)."""
        if gift_card_amount:
            self.with_gift_card_payment(gift_card_amount)
        if card_amount:
            self.with_transaction(
                UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, card_amount
            )
        return self

    def with_order_level_discount(self, percentage: float):
        """Add order-level percentage discount."""
        discount_amount = self._order_data["base_amount"] * (percentage / 100)
        self._order_data["discounts"].append(
            {
                "type": UATConstants.ORDER_LEVEL_PERCENTAGE,
                "amount": discount_amount,
                "percentage": percentage,
            }
        )
        return self

    def with_line_level_discount(self, line_item_index: int, fixed_amount: float):
        """Add line-level fixed discount."""
        self._order_data["discounts"].append(
            {
                "type": UATConstants.LINE_LEVEL_FIXED,
                "amount": fixed_amount,
                "line_item_index": line_item_index,
            }
        )
        return self

    def with_vat(self, rate: float = UATConstants.VAT_RATE):
        """Add VAT to the order."""
        base_amount = sum(
            item["price"] * item["quantity"] for item in self._order_data["line_items"]
        )
        if not base_amount:
            base_amount = self._order_data["base_amount"]

        self._order_data["tax_rate"] = rate
        self._order_data["tax_amount"] = base_amount * rate
        return self

    def with_shipping(self, amount: float = 10.0, refundable: bool = True):
        """Set shipping amount and policy."""
        self._order_data["shipping_amount"] = amount
        self._order_data["shipping_refundable"] = refundable
        return self

    def with_restocking_fee(self, amount: float):
        """Add restocking fee."""
        return self

    def with_no_tracking_no(self):
        returns = self._order_data.get("returns", [])
        for _return in returns:
            _return["tracking_number"] = None
        return self

    def with_return_tracking(
        self, tracking_number=None, carrier: str = "DHL", status="OPEN"
    ):
        """Add return tracking information."""
        return_id = f"gid://shopify/Return/{uuid.uuid4().hex[:8]}"
        self.returns = self._order_data.get("returns", [])

        self._order_data["returns"].append(
            {
                "id": return_id,
                "carrier": carrier,
                "status": status,
                "returnLineItems": [],
                "reverseFulfillmentOrders": [],
                "tracking_number": (
                    tracking_number
                    if tracking_number is not None
                    else UATConstants.TRACKING_NUMBER
                ),
            }
        )
        return self

    def with_prior_partial_refund(self, amount: float):
        """Add prior partial refund."""
        refund_id = f"gid://shopify/Refund/{uuid.uuid4().hex[:8]}"
        self._order_data["refunds"].append(
            {
                "id": refund_id,
                "amount": amount,
                "created_at": (
                    timezone_handler.format_iso8601_with_tz(
                        timezone_handler.get_added_store_time(days=1)
                    )
                )
                + "Z",
            }
        )
        return self

    def with_tags(self, *tags: str):
        """Add tags to the order."""
        self._order_data["tags"].extend(tags)
        return self

    def with_chargeback_tag(self):
        """Add chargeback tag to block refunds."""
        return self.with_tags("chargeback")

    def with_refund_auto_off_tag(self):
        """Add tag to skip automatic refunds."""
        return self.with_tags("refund:auto:off")

    def with_refund_force_now_tag(self):
        """Add tag to force immediate refund."""
        return self.with_tags("refund:force:now")


class UATTrackingBuilder:
    """Builder for creating tracking data for UAT tests."""

    def __init__(
        self,
        tracking_number: str = None,
        carrier_code: str = None,
        delivered_at: str = None,
        carrier_disagreement: dict = None,
    ):
        self.tag = "UAT"
        self.latest_event = False
        self.status = TrackingStatus.DELIVERED
        self.sub_status = TrackingSubStatus.DELIVERED_OTHER
        self.delivered_at = delivered_at
        self.carrier_disagreement = carrier_disagreement or {}

        self.carrier_code = carrier_code or UATConstants.CARRIER_CODE
        self.tracking_number = (
            tracking_number
            if tracking_number is not None
            else UATConstants.TRACKING_NUMBER
        )

    def with_delivered_status(self, days_ago: int = 6, with_latest_event=True):
        """Set as delivered with specific timing."""
        self.status = TrackingStatus.DELIVERED
        self.sub_status = TrackingSubStatus.DELIVERED_OTHER
        self.delivered_at = timezone_handler.format_iso8601_with_tz(
            timezone_handler.get_subtracted_store_time(days_ago)
        )

        if with_latest_event:
            return self.with_latest_event()
        return self

    def with_latest_event(self):
        """Set latest event with valid status."""
        self.delivered_at = self.delivered_at or None
        self.latest_event = True
        return self

    def with_attempted_delivery(self):
        """Set as delivery attempted only."""
        self.status = TrackingStatus.IN_TRANSIT
        self.sub_status = TrackingSubStatus.IN_TRANSIT_OTHER
        return self

    def with_carrier_mismatch(self):
        """Set up carrier system disagreement."""
        self.carrier_disagreement = {
            **(self.carrier_disagreement or {}),
            "primary_says": TrackingStatus.DELIVERED,
            "secondary_says": "in_transit",
            "mismatch": True,
        }
        return self

    def with_no_tracking(self):
        """Set up scenario with no tracking number."""
        self.tracking_number = None
        return self

    def with_early_delivery(self, hours_ago: int = 100):
        """Set delivery time to be too early for refund."""
        self.delivered_at = timezone_handler.format_iso8601_with_tz(
            timezone_handler.get_added_store_time(hours=hours_ago)
        )
        return self.with_latest_event()

    def with_delivered_at(self, with_latest_event=True, **kwargs):
        """Set custom delivery time using keyword arguments for time adjustments."""
        self.delivered_at = timezone_handler.format_iso8601_with_tz(
            timezone_handler.get_subtracted_store_time(**kwargs)
        )
        if with_latest_event:
            self.with_latest_event()
        return self

    def with_no_delivered_at(self, with_latest_event=True):
        """Clear delivered_at to represent no delivery information."""
        self.delivered_at = None
        if with_latest_event:
            self.with_latest_event()
        return self

    def build(self) -> TrackingData:
        """Build mock tracking object."""
        latest_event = None
        if self.latest_event:
            latest_event = LatestEvent(
                description="",
                location=None,
                time_iso=self.delivered_at,
                time_utc=self.delivered_at,
                stage=TrackingStatus.DELIVERED,
                sub_status=TrackingSubStatus.DELIVERED_OTHER,
            )
            latest_status = LatestStatus(
                status=TrackingStatus.DELIVERED,
                sub_status=TrackingSubStatus.DELIVERED_OTHER,
                sub_status_descr="",
            )
        else:
            latest_status = LatestStatus(
                status=self.status, sub_status=self.sub_status, sub_status_descr=""
            )
        track_info = TrackInfo(
            latest_event=latest_event,
            latest_status=latest_status,
            milestone=[],
        )
        tracking = TrackingData(
            tag=self.tag,
            track_info=track_info,
            carrier=self.carrier_code,
            number=self.tracking_number,
            carrier_disagreement=self.carrier_disagreement,
        )
        return tracking


def get_mock_success_refund_response(
    refund_id: str = None,
    amount=100.0,
    currency=UATConstants.USD,
    created_at: str = None,
    user_errors: list[dict] = None,
):
    success_response = Mock()
    success_response.status_code = 200
    success_response.json.return_value = {
        "data": {
            "refundCreate": {
                "refund": {
                    "id": (
                        refund_id
                        if refund_id
                        else "gid://shopify/Refund/292ZNUZN8YZ2Z9N2N"
                    ),
                    "createdAt": (
                        created_at
                        if created_at
                        else timezone_handler.format_iso8601_with_tz(
                            timezone_handler.get_current_time_store()
                        )
                    ),
                    "totalRefundedSet": {
                        "presentmentMoney": {
                            "amount": str(amount),
                            "currencyCode": (
                                currency if currency is not None else UATConstants.USD
                            ),
                        },
                        "shopMoney": {
                            "amount": str(amount),
                            "currencyCode": (
                                currency if currency is not None else UATConstants.USD
                            ),
                        },
                    },  # Partial
                },
                "userErrors": user_errors if user_errors is not None else [],
            }
        }
    }
    success_response.raise_for_status = Mock()
    return success_response


def get_mock_failure_refund_response(user_errors: list[dict] = None):
    error_response = Mock()
    error_response.status_code = 200
    error_response.json.return_value = {
        "data": {
            "refundCreate": {
                "refund": None,
                "userErrors": (
                    user_errors
                    if user_errors is not None
                    else [{"message": "Payment method declined"}]
                ),
            }
        }
    }
    error_response.raise_for_status = Mock()
    return error_response


# Convenience functions for common scenarios
def create_b_h1_order(tracking_number=None, full_refund=False):
    """B-H1: Full return, single payment, single fulfillment."""
    fx = UATFixtureBuilder()

    fx = (
        fx.with_shipping(amount=10)
        .with_line_item(quantity=2, price=50.0)
        .with_line_item(quantity=2, price=50.0)
        .with_return_tracking(tracking_number=tracking_number)
        .with_return_line_item(fx._order_data["line_items"][0]["id"], refundable_qty=2)
    )

    if full_refund:
        fx = fx.with_return_line_item(
            fx._order_data["line_items"][1]["id"], refundable_qty=2
        )

    order = fx.with_transaction(
        gateway=UATConstants.SHOPIFY_PAYMENTS,
        kind=TransactionKind.SALE,
    ).build()

    return (order, {})


def create_b_h2_order(tracking_number=None, shipping_amount=0):
    """B-H2: Partial return (subset of lines/qty), single payment."""

    fx = UATFixtureBuilder()
    fx = (
        fx.with_id_and_name("gid://shopify/Order/BH1001")
        .with_shipping(shipping_amount)
        .with_line_item(quantity=2, price=50)
        .with_line_item(quantity=2, price=50)
        .with_return_tracking(tracking_number)
        .with_return_line_item(fx._order_data["line_items"][0]["id"], refundable_qty=2)
        .with_transaction(
            gateway=UATConstants.SHOPIFY_PAYMENTS,
            kind=TransactionKind.SALE,
            amount=(200 + shipping_amount),
        )
    )

    return fx.build()


def create_b_h3_order(tracking_number=None) -> ShopifyOrder:
    """B-H3: Split fulfillment; return only from shipment A."""
    return (
        UATFixtureBuilder()
        .with_shipping(10)
        .with_id_and_name("gid://shopify/Order/BH3001", "BH3-SPLIT-001")
        .with_line_item("gid://shopify/LineItem/1", quantity=1, price=50.0)
        .with_line_item(
            "gid://shopify/LineItem/2", quantity=1, price=50.0, refundable_qty=0
        )
        .with_return_tracking(tracking_number)
        .with_return_line_item("gid://shopify/LineItem/1", refundable_qty=1)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .build()
    )


def create_b_d1_order(tracking_number=None, shipping_amount=0) -> ShopifyOrder:
    """B-D1: Order-level discount (percentage)."""

    fx = UATFixtureBuilder()
    return (
        fx.with_line_item(
            "gid://shopify/LineItem/KJS9I02I02J92", quantity=2, price=50.0
        )
        .with_order_level_discount(15.0)  # 15% discount
        .with_shipping(shipping_amount, refundable=bool(shipping_amount))
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 85.0)
        .with_return_tracking(tracking_number=tracking_number)
        .with_return_line_item("gid://shopify/LineItem/KJS9I02I02J92", refundable_qty=2)
        .build()
    )


def create_b_d2_order(
    full_refund=False, with_shipping=False
) -> tuple[ShopifyOrder, dict]:
    """B-D2: Line-level fixed discount."""
    item_1_qty = 2
    item_2_qty = 1
    item_1_amount = 50.0  # 50 * 2 = 100
    item_2_amount = 70.0  # 70 * 1 = 70

    expected_refund = item_1_amount

    fx = UATFixtureBuilder()

    if with_shipping:
        expected_refund += item_2_amount

        fx = fx.with_shipping(
            refundable=expected_refund,
            amount=expected_refund,
        )

    fx = (
        fx.with_id_and_name("gid://shopify/Order/BD2001", "BD2-LINE-DISCOUNT-001")
        .with_line_item(quantity=item_1_qty, price=item_1_amount)
        .with_line_item(quantity=item_2_qty, price=item_2_amount)
        .with_line_level_discount(0, 10.0)  # $10 off first item
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 90.0)
        .with_return_tracking()
        .with_return_line_item(
            fx._order_data["line_items"][0]["id"], refundable_qty=item_1_qty
        )
    )

    if full_refund:
        expected_refund += item_2_amount
        fx = fx.with_return_line_item(
            fx._order_data["line_items"][1]["id"], refundable_qty=item_2_qty
        )

    fx = fx.build()

    return fx


def create_b_t1_order(full_refund=False):
    """B-T1: VAT-inclusive pricing."""

    fx = UATFixtureBuilder()
    fx = (
        fx.with_id_and_name("gid://shopify/Order/BT1001", "BT1-VAT-001")
        .with_currency(UATConstants.EUR)
        .with_line_item(quantity=1, price=50)
        .with_line_item(quantity=1, price=50)
        .with_vat()
        .with_shipping(10)
        .with_return_tracking()
        .with_return_line_item(fx._order_data["line_items"][0]["id"], refundable_qty=1)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
    )

    if full_refund:
        fx = fx.with_return_line_item(
            fx._order_data["line_items"][1]["id"], refundable_qty=1
        )

    return fx.build(), {}


def create_b_s1_order(
    full_refund=True, tracking_number=None, shipping_amount=20.0
) -> ShopifyOrder:
    """B-S1: Shipping refundable per policy ON."""

    return (
        UATFixtureBuilder()
        .with_id_and_name("gid://shopify/Order/BS1001", "BS1-SHIP-REFUND-001")
        .with_line_item("gid://shopify/LineItem/BS100A1", quantity=2, price=50.0)
        .with_shipping(shipping_amount, refundable=True)
        .with_return_tracking(tracking_number=tracking_number)
        .with_return_line_item(
            "gid://shopify/LineItem/BS100A1", refundable_qty=(2 if full_refund else 1)
        )
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .build()
    )


def create_b_s2_order(shipping_amount=15.0) -> ShopifyOrder:
    """B-S2: Shipping refundable per policy OFF."""
    return (
        UATFixtureBuilder()
        .with_line_item(quantity=2, price=50.0)
        .with_shipping(shipping_amount, refundable=False)
        .with_return_tracking()
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .build()
    )


def create_b_p1_order(
    tracking_number=None,
    item_qty=2,
    item_price=50,
    return_qty: Literal[1, 2] = 2,
    shipping_amount=0,
    vat_rate=0,
) -> ShopifyOrder:
    """B-P1: Gift card 100% payment."""
    fx = UATFixtureBuilder()
    fx = (
        fx.with_id_and_name("gid://shopify/Order/BP1001", "BP1-GIFT-CARD-001")
        .with_line_item(quantity=item_qty, price=item_price)  # Two (2) gift-cards
        .with_mixed_payment(
            gift_card_amount=item_price * item_qty
        )  # creates a transaction with the given amount
        .with_vat(vat_rate)
        .with_shipping(shipping_amount)
        .with_return_tracking(tracking_number)
        .with_return_line_item(
            fx._order_data["line_items"][0]["id"], refundable_qty=return_qty
        )
    )

    return fx.build()


def create_b_p2_order(
    tracking_number=None, full_refund=True, shipping_amount=10
) -> ShopifyOrder:
    """B-P2: Gift card + card mix."""
    fx = UATFixtureBuilder()
    fx = (
        fx.with_id_and_name("gid://shopify/Order/BP2001", "BP2-MIXED-PAYMENT-001")
        .with_line_item(
            "gid://shopify/LineItem/BP2001A1", quantity=2, price=30
        )  # Gift-Card
        .with_line_item("gid://shopify/LineItem/BP2001A2", quantity=2, price=20)
        .with_shipping(shipping_amount)
        .with_mixed_payment(gift_card_amount=60.0)  # Gift-Card
        .with_return_tracking(tracking_number)
        .with_return_line_item("gid://shopify/LineItem/BP2001A1", 2)  # Gift-Card
    )

    if full_refund:
        fx = fx.with_return_line_item(
            "gid://shopify/LineItem/BP2001A2", 2
        ).with_mixed_payment(card_amount=40.0)

    return fx.build()


def create_b_p3_order(shipping_amount=10, tracking_number=None) -> ShopifyOrder:
    """B-P3: Store credit used."""
    return (
        UATFixtureBuilder()
        .with_line_item("gid://shopify/LineItem/BP1001", quantity=2, price=50.0)
        .with_shipping(shipping_amount, refundable=True)
        .with_store_credit_payment(100.0)
        .with_return_tracking(tracking_number)
        .with_return_line_item("gid://shopify/LineItem/BP1001", refundable_qty=2)
        .build()
    )


def create_b_c1_order(shipping_amount=10, tracking_number=None) -> ShopifyOrder:
    """B-C1: Multi-currency store (order in CHF)."""
    return (
        UATFixtureBuilder()
        .with_currency(UATConstants.CHF)
        .with_line_item("gid://shopify/LineItem/BP1001", quantity=2, price=50.0)
        .with_return_tracking(tracking_number)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .with_return_line_item("gid://shopify/LineItem/BP1001", refundable_qty=2)
        .with_shipping(amount=shipping_amount)
        .build()
    )


def create_b_c2_order(tracking_number=None) -> ShopifyOrder:
    """B-C2: Prior partial refund exists."""

    fx = UATFixtureBuilder()

    return (
        fx.with_line_item(quantity=2, price=50.0)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0)
        .with_prior_partial_refund(30.0)
        .with_return_tracking(tracking_number)
        .with_return_line_item(fx._order_data["line_items"][0]["id"], refundable_qty=2)
        .build()
    )


# Tracking fixtures
def create_delivered_tracking(days_ago: int = 6, tracking_number: str = None):
    """Create tracking data for delivered package."""
    return (
        UATTrackingBuilder(tracking_number=tracking_number)
        .with_delivered_at(days=days_ago)
        .build()
    )


def create_tracking_with_returns_line_items(
    days_ago: int = 6, tracking_number=None
) -> Mock:
    """Create tracking data for delivered package."""
    return (
        UATTrackingBuilder(tracking_number=tracking_number)
        .with_delivered_status(days_ago)
        .build()
    )


def create_early_delivery_tracking(tracking_number=None):
    """Create tracking for package delivered too early."""
    return (
        UATTrackingBuilder(tracking_number=tracking_number)
        .with_early_delivery(hours_ago=100)
        .build()
    )


def create_attempted_delivery_tracking(tracking_number=None):
    """Create tracking for delivery attempt only."""
    return (
        UATTrackingBuilder(tracking_number=tracking_number)
        .with_attempted_delivery()
        .build()
    )


def create_carrier_mismatch_tracking(tracking_number=None):
    """Create tracking with carrier system disagreement."""
    return (
        UATTrackingBuilder(tracking_number=tracking_number)
        .with_carrier_mismatch()
        .build()
    )


def create_no_tracking(tracking_number=None) -> Mock:
    """Create scenario with no tracking number."""
    return (
        UATTrackingBuilder(tracking_number=tracking_number).with_no_tracking().build()
    )


# Tag-based orders
def create_chargeback_order(tracking_number=None) -> ShopifyOrder:
    """B-F1: Order under chargeback."""
    return (
        UATFixtureBuilder()
        .with_line_item(quantity=2, price=50.0)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0)
        .with_chargeback_tag()
        .with_return_tracking()
        .build()
    )


def create_refund_auto_off_order(tracking_number=None) -> ShopifyOrder:
    """B-TAG1: refund:auto:off."""
    return (
        UATFixtureBuilder()
        .with_id_and_name("gid://shopify/Order/BTAG1001", tracking_number)
        .with_line_item(quantity=2, price=50.0)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0)
        .with_refund_auto_off_tag()
        .with_return_tracking()
        .build()
    )


def create_refund_force_now_order(tracking_number=None) -> ShopifyOrder:
    """B-TAG2: refund:force:now."""
    return (
        UATFixtureBuilder()
        .with_line_item("gid://shopify/LineItem/BTAG2001", quantity=2, price=50.0)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .with_refund_force_now_tag()
        .with_return_tracking()
        .with_return_line_item("gid://shopify/LineItem/BTAG2001", refundable_qty=2)
        .build()
    )
