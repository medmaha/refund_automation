import pytest

from src.models.order import (
    TransactionKind,
)
from src.tests.uat.uat_constants import UATConstants
from src.tests.uat.uat_fixtures import UATFixtureBuilder, create_delivered_tracking

TEST_TRACKING_NUMBER = "DUMMY923456TEST"


@pytest.fixture
def dummy_order():
    return (
        UATFixtureBuilder()
        .with_line_item("gid://shopify/LineItem/BP1001", quantity=2, price=50.0)
        .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
        .with_return_tracking(UATConstants.TRACKING_NUMBER)
        .with_return_line_item("gid://shopify/LineItem/BP1001", refundable_qty=2)
        .with_shipping(amount=10)
        .build()
    )


@pytest.fixture
def dummy_orders_with_invalid_returns():
    return [
        (
            UATFixtureBuilder()
            .with_line_item(f"gid://shopify/LineItem/BP100{i}", quantity=2, price=50.0)
            .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE)
            .with_shipping(amount=10)
            .with_no_tracking_no()
            .build()
        )
        for i in range(5)
    ]


@pytest.fixture
def dummy_tracking():
    return create_delivered_tracking()
