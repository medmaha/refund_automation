from src.shopify.orders import parse_graphql_order_data


def test_parse_graphql_order_data_flattens_dict_nodes():
    """Test that function flattens nested GraphQL {"nodes": [...]} structures."""
    node = {
        "id": "order123",
        "lineItems": {"nodes": [{"id": "item1"}]},
        "returns": {"nodes": [{"reverseFulfillmentOrders": {"nodes": [{"reverseDeliveries": {"nodes": []}}]}}]}
    }
    
    result = parse_graphql_order_data(node)
    
    assert isinstance(result["lineItems"], list)
    assert result["lineItems"] == [{"id": "item1"}]
    assert isinstance(result["returns"], list)
    assert isinstance(result["returns"][0]["reverseFulfillmentOrders"], list)


def test_parse_graphql_order_data_preserves_direct_lists():
    """Test that function preserves already-flattened list structures."""
    node = {
        "id": "order123",
        "lineItems": [{"id": "item1"}],
        "returns": [{"reverseFulfillmentOrders": [{"reverseDeliveries": []}]}]
    }
    
    result = parse_graphql_order_data(node)
    
    assert result["lineItems"] == [{"id": "item1"}]
    assert result["returns"] == [{"reverseFulfillmentOrders": [{"reverseDeliveries": []}]}]


def test_parse_graphql_order_data_handles_missing_fields():
    """Test that function handles missing lineItems and returns fields."""
    node = {"id": "order123", "name": "#1001"}
    
    result = parse_graphql_order_data(node)
    
    assert result["lineItems"] == []
    assert result["returns"] == []
    assert result["id"] == "order123"  # Original fields preserved
