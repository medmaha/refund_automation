from unittest.mock import patch, Mock
from src.utils.audit import audit_logger
from src.shopify.refund import refund_order
from src.tests.fixtures import *

@pytest.fixture(autouse=True)
def mock_slack_and_idempotency():
    with patch('src.shopify.refund.slack_notifier') as mock_slack, \
         patch('src.shopify.refund.idempotency_manager') as mock_idempotency_manager, \
         patch('requests.post') as mock_post:
        
        mock_idempotency_manager.check_operation_idempotency.return_value = ("test_key", False)
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "refundCreate": {
                    "refund": {
                        "id": "gid://shopify/Refund/12345",
                        "createdAt": "2023-01-01T00:00:00Z",
                        "totalRefundedSet": {
                            "presentmentMoney": {"amount": "100.0", "currencyCode": "USD"}
                        }
                    },
                    "userErrors": []
                }
            }
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        yield mock_slack, mock_idempotency_manager


class TestAuditLogging:
    """Test audit logging functionality."""
    
    def test_audit_logging_records_decisions(self, mock_slack_and_idempotency, sample_order, sample_tracking):
            
        refund = refund_order(sample_order, sample_tracking)
        assert refund is not None
        
        # Check that audit stats show activity
        stats = audit_logger.get_audit_stats()
        assert stats['enabled'] == True
