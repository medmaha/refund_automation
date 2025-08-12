from src.models.order import (
    DeliverableTracking,
    Deliverable,
    MoneyBag,
    MoneyBagSet,
    ReturnFulfillments,
    ReverseDeliveries,
    ReverseFulfillmentOrder,
    ShopifyOrder,
)


def test_valid_return_shipment_with_tracking():
    """Test valid_return_shipment returns return when both tracking number and carrier exist."""
    tracking = DeliverableTracking(number="123456", carrierName="DHL")
    deliverable = Deliverable(tracking=tracking)
    reverse_delivery = ReverseDeliveries(deliverable=deliverable)
    rfo = ReverseFulfillmentOrder(reverseDeliveries=[reverse_delivery])
    return_fulfillment = ReturnFulfillments(
        id="return_123", name="Return 1", reverseFulfillmentOrders=[rfo]
    )
    
    order = _create_order([return_fulfillment])
    assert order.valid_return_shipment.id == "return_123"


def test_valid_return_shipment_without_tracking():
    """Test valid_return_shipment returns None when tracking number or carrier is missing."""
    incomplete_tracking = DeliverableTracking(number="123456")  # Missing carrier
    deliverable = Deliverable(tracking=incomplete_tracking)
    reverse_delivery = ReverseDeliveries(deliverable=deliverable)
    rfo = ReverseFulfillmentOrder(reverseDeliveries=[reverse_delivery])
    return_fulfillment = ReturnFulfillments(
        id="return_123", name="Return 1", reverseFulfillmentOrders=[rfo]
    )
    
    order = _create_order([return_fulfillment])
    assert order.valid_return_shipment is None


def test_valid_return_shipment_no_returns():
    """Test valid_return_shipment returns None when order has no returns."""
    order = _create_order([])
    assert order.valid_return_shipment is None


def _create_order(returns):
    """Helper to create minimal ShopifyOrder for testing."""
    money_set = MoneyBagSet(presentmentMoney=MoneyBag(amount=100.0))
    return ShopifyOrder(
        id="test", name="#TEST", tags=[], lineItems=[], 
        totalPriceSet=money_set, transactions=[], returns=returns
    )
