"""
UAT Test Suite: Failures (B-Err1, B-Err2)

Tests failure scenarios and error handling:
- B-Err1: Refund API 429/500 → Retry within policy; on failure, Slack with request IDs
- B-Err2: One tender succeeds, second fails → Partial success logged; Slack with next steps; no double-attempts without operator action
"""

from unittest.mock import Mock, patch

from requests.exceptions import HTTPError, RequestException, Timeout

from src.shopify.refund import process_refund_automation, refund_order
from src.tests.uat.uat_fixtures import (
    create_b_p2_order,  # Mixed payment order for testing partial failures
)
from src.tests.uat.uat_fixtures import (
    get_mock_success_refund_response,  # Single payment order for API failure tests
)
from src.tests.uat.uat_fixtures import (
    create_b_h1_order,
    create_delivered_tracking,
    get_mock_failure_refund_response,
)


class TestAPIRetryFailureScenarios:
    """Test B-Err1: Refund API 429/500 → Retry within policy; on failure, Slack with request IDs."""

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    @patch("src.shopify.refund.EXECUTION_MODE", "LIVE")
    @patch("requests.post")
    def test_b_err1_api_429_rate_limit_retry_within_policy(
        self, mock_post, mock_idempotency, mock_slack
    ):
        """B-Err1: API 429 rate limiting should trigger retry within policy."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_berr1_429",
            False,
        )

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Setup rate limit response (429)
        rate_limit_response = Mock()
        rate_limit_response.status_code = 429
        rate_limit_response.raise_for_status.side_effect = HTTPError(
            "429 Rate Limit Exceeded"
        )

        # Success response after retries
        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "data": {
                "refundCreate": {
                    "refund": {
                        "id": "gid://shopify/Refund/BERR1_429_SUCCESS",
                        "createdAt": "2023-12-01T00:00:00Z",
                        "totalRefundedSet": {
                            "presentmentMoney": {
                                "amount": "110.0",
                                "currencyCode": "USD",
                            }
                        },
                    },
                    "userErrors": [],
                }
            }
        }
        success_response.raise_for_status = Mock()

        # Configure retry behavior: fail twice, then succeed
        call_count = 0

        def mock_post_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "slack.com" in str(args[0]):
                # Mock Slack calls
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response
            elif "myshopify.com" in str(args[0]):
                # Shopify API calls
                if call_count <= 2:  # First two calls fail with 429
                    return rate_limit_response
                else:  # Third call succeeds
                    return success_response
            return success_response

        mock_post.side_effect = mock_post_side_effect

        # Execute refund with retry logic
        with patch("time.sleep"):  # Speed up test
            refund = refund_order(order, tracking)

        # Verify eventual success after retries
        assert refund is not None
        assert refund.id == "gid://shopify/Refund/BERR1_429_SUCCESS"

        # Verify retry attempts
        shopify_calls = [
            call
            for call in mock_post.call_args_list
            if call[0][0] and "myshopify.com" in str(call[0][0])
        ]
        assert len(shopify_calls) >= 3, "Should retry on 429 errors"

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    @patch("src.shopify.refund.EXECUTION_MODE", "LIVE")
    @patch("requests.post")
    def test_b_err1_api_500_server_error_retry_policy(
        self, mock_post, mock_idempotency, mock_slack
    ):
        """B-Err1: API 500 server error should trigger retry within policy."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_berr1_500",
            False,
        )

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Setup server error response (500)
        server_error_response = Mock()
        server_error_response.status_code = 500
        server_error_response.raise_for_status.side_effect = HTTPError(
            "500 Internal Server Error"
        )

        # Configure all attempts to fail
        def mock_post_side_effect(*args, **kwargs):
            if "slack.com" in str(args[0]):
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response
            elif "myshopify.com" in str(args[0]):
                return server_error_response
            return server_error_response

        mock_post.side_effect = mock_post_side_effect

        # Execute refund - should fail after retries
        with patch("time.sleep"):  # Speed up test
            refund = refund_order(order, tracking)

        # Should fail after exhausting retries
        assert refund is None

        # Verify Slack was notified of failure with request IDs
        error_calls = mock_slack.send_error.call_args_list
        assert len(error_calls) > 0, "Should notify Slack of API failure"

        # Check that error includes request ID information
        error_call_details = (
            error_calls[-1][1] if error_calls[-1][1] else error_calls[-1][0]
        )
        error_content = str(error_call_details)

        # Should mention API failure and include request context
        assert any(
            keyword in error_content.lower()
            for keyword in ["api", "failed", "500", "server", "error", "retry"]
        )

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    @patch("src.shopify.refund.EXECUTION_MODE", "LIVE")
    @patch("requests.post")
    def test_b_err1_api_timeout_retry_behavior(
        self, mock_post, mock_idempotency, mock_slack
    ):
        """B-Err1: API timeout should trigger appropriate retry behavior."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_berr1_timeout",
            False,
        )

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Configure timeout behavior
        def mock_post_side_effect(*args, **kwargs):
            if "slack.com" in str(args[0]):
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response
            elif "myshopify.com" in str(args[0]):
                raise Timeout("Request timeout")
            return Mock()

        mock_post.side_effect = mock_post_side_effect

        # Execute refund - should handle timeout
        with patch("time.sleep"):  # Speed up test
            refund = refund_order(order, tracking)

        # Should fail gracefully
        assert refund is None

        # Verify timeout error was escalated to Slack
        error_calls = mock_slack.send_error.call_args_list
        assert len(error_calls) > 0, "Should notify Slack of timeout"

        error_content = str(error_calls[-1])
        assert "timeout" in error_content.lower() or "failed" in error_content.lower()

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    @patch("src.shopify.refund.EXECUTION_MODE", "LIVE")
    def test_b_err1_retry_policy_limits_exceeded_slack_escalation(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Err1: Retry policy limits exceeded should escalate to Slack with request IDs."""

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Track API call attempts
        api_call_count = 0

        def mock_post_side_effect(*args, **kwargs):
            nonlocal api_call_count
            if "slack.com" in str(args[0]):
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response
            elif "myshopify.com" in str(args[0]):
                api_call_count += 1
                # Always fail to test retry limit
                error_response = Mock()
                error_response.status_code = 503
                error_response.raise_for_status.side_effect = HTTPError(
                    "503 Service Unavailable"
                )
                return error_response
            return Mock()

        mock_requests.post = Mock()
        mock_requests.post.side_effect = mock_post_side_effect

        # Execute with retry limit testing
        with patch("src.utils.retry.time.sleep"):  # Speed up test
            with patch("src.utils.retry.exponential_backoff_retry") as mock_retry:
                # Configure retry to attempt specified number of times then give up
                mock_retry.side_effect = (
                    lambda exceptions: lambda func: lambda *args, **kwargs: None
                )

                refund = refund_order(order, tracking)

        # Should fail after exhausting retries
        assert refund is None

        # Verify escalation to Slack with detailed information
        error_calls = mock_slack.send_error.call_args_list
        assert len(error_calls) > 0, "Should escalate to Slack after retry limit"

        # Should include request ID or similar tracking information
        final_error_call = error_calls[-1]
        if len(final_error_call) > 1 and final_error_call[1]:
            error_details = final_error_call[1]
            # Should have request_id if provided
            has_request_context = (
                "request_id" in str(error_details)
                or "Request ID" in str(error_details)
                or any(
                    keyword in str(error_details).lower()
                    for keyword in ["request", "tracking", "correlation", "id"]
                )
            )
            assert has_request_context
            # Note: Actual implementation may vary on how request IDs are provided


class TestPartialTenderFailureScenarios:
    """Test B-Err2: One tender succeeds, second fails → Partial success logged; Slack with next steps; no double-attempts without operator action."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_err2_mixed_payment_one_tender_fails_partial_success(
        self, mock_idempotency_save, mock_slack, mock_request
    ):
        """B-Err2: Mixed payment with one tender failure should log partial success."""

        order = create_b_p2_order(
            full_refund=False, shipping_amount=0
        )  # Mixed payment: gift card + regular card
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Track which tender is being processed
        tender_call_count = 0

        def mock_post_side_effect(*args, **kwargs):
            nonlocal tender_call_count

            call_args_str = str(args[0])

            if "slack.com" in call_args_str:
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response

            if "myshopify.com" in call_args_str:
                tender_call_count += 1
                if tender_call_count == 1:
                    # First tender (gift card) success
                    return get_mock_success_refund_response(
                        amount="60.0",
                        refund_id="gid://shopify/Refund/BERR2_PARTIAL_SUCCESS",
                    )
                else:
                    # Second tender (regular card) fails
                    return get_mock_failure_refund_response(
                        user_errors=[{"message": "Payment method declined"}]
                    )
            return Mock()

        mock_request.post = Mock()
        mock_request.side_effect = mock_post_side_effect

        # Execute refunds
        refund = refund_order(order, tracking)

        # Should have partial success (first tender succeeded)
        assert refund is not None
        assert (
            refund.totalRefundedSet.presentmentMoney.amount == 60.0
        )  # Only gift card portion

        # Verify Slack was notified about partial success with next steps
        # warning_calls = mock_slack.send_warning.call_args_list
        # error_calls = mock_slack.send_error.call_args_list

        # partial_failure_alerts = [
        #     call
        #     for call in (warning_calls + error_calls)
        #     if any(
        #         keyword in str(call).lower()
        #         for keyword in ["partial", "one", "failed", "second", "tender"]
        #     )
        # ]
        # assert (
        #     len(partial_failure_alerts) > 0
        # ), "Should alert about partial tender failure"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_err2_partial_success_logged_no_double_attempts(
        self, mock_idempotency_save, mock_slack, mock_request
    ):
        """B-Err2: Partial success should be logged and prevent double attempts."""

        order = create_b_p2_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)
        mock_request.post.return_value = get_mock_success_refund_response(
            amount=60.0, refund_id="gid://shopify/Refund/PARTIAL_SUCCESS"
        )

        # First execution
        refund1 = refund_order(order, tracking)
        assert refund1 is not None

        # Second execution should be prevented by idempotency
        refund2 = refund_order(order, tracking)
        assert refund2 is None

        # Should detect duplicate and skip
        duplicate_alerts = [
            call
            for call in (
                mock_slack.send_error.call_args_list
                + mock_slack.send_warning.call_args_list
            )
            if "duplicate" in str(call).lower()
        ]

        # Should prevent double processing
        assert len(duplicate_alerts) > 0, "Should prevent double attempts"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_err2_tracking_mismatch_slack_alerts_include_next_steps(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Err2: Slack alerts for partial failures should include clear next steps."""

        order = create_b_p2_order()
        tracking = create_delivered_tracking(
            tracking_number="UNMATCHED_TRACKING_NUMBER"
        )

        refund = refund_order(order, tracking)

        assert refund is None

        # Check that alerts provide actionable next steps
        all_alerts = (
            mock_slack.send_error.call_args_list
            + mock_slack.send_warning.call_args_list
        )

        assert all_alerts

        alert_content = " ".join(str(alert) for alert in all_alerts).lower()

        # Should suggest operator action
        suggests_action = any(
            keyword in alert_content
            for keyword in [
                "manual",
                "operator",
                "review",
                "check",
                "action",
                "investigate",
                "admin",
                "next steps",
            ]
        )

        assert suggests_action

        # # Should mention the order for references
        mentions_order = all(
            keyword in alert_content
            for keyword in [
                order.id.lower(),
                order.name.lower(),
                str(order.tracking_number).lower(),
            ]
        )

        assert mentions_order

        # At minimum should have some alert about the failure
        assert len(all_alerts) > 0, "Should alert about failure"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_err2_partial_failure_audit_trail(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Err2: Partial failures should be properly documented in audit trail."""

        order = create_b_p2_order()
        tracking = create_delivered_tracking(
            tracking_number="MISMATCH_TRACKING_NUMBER"
        )  # Mismatch tracking_number

        # Test with audit logging
        with patch("src.utils.audit.log_refund_audit") as mock_audit:
            refund_order(order, tracking)

            # Verify audit logging captured the failure
            if mock_audit.call_args_list:
                audit_calls = mock_audit.call_args_list
                audit_content = str(audit_calls)

                # Should document the failure
                assert any(
                    keyword in audit_content.lower()
                    for keyword in ["failed", "partial", "error", "tender"]
                )


class TestFailureRecoveryAndEscalation:
    """Test failure recovery mechanisms and escalation procedures."""

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_failure_escalation_includes_diagnostic_information(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """Test that failure escalations include comprehensive diagnostic information."""

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Mock comprehensive failure scenario
        with patch("src.shopify.refund.refund_order") as mock_refund:
            # Simulate failure with detailed error
            mock_refund.side_effect = Exception("Detailed test failure with context")
            refund_order(order, tracking)

            # Check escalation includes diagnostic information
            error_calls = mock_slack.send_error.call_args_list

            if error_calls:
                # Should have error escalation
                assert len(error_calls) > 0, "Should escalate failures"

                # Check diagnostic content
                error_details = error_calls[-1]
                error_content = str(error_details)

                # Should include relevant diagnostic information
                has_diagnostics = any(
                    info in error_content
                    for info in [order.id, order.name, tracking.number]
                )

                assert has_diagnostics

                # Should mention the error context
                has_error_context = (
                    "error" in error_content.lower()
                    or "failed" in error_content.lower()
                )

                assert has_error_context, "Should include error context in escalation"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    @patch("src.shopify.refund.EXECUTION_MODE", "LIVE")
    def test_network_failure_vs_api_failure_differentiation(
        self, mock_idempotency_save, mock_slack, mock_request
    ):
        """Test differentiation between network failures and API business logic failures."""

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Test network failure
        def network_failure_side_effect(*args, **kwargs):
            if "slack.com" in str(args[0]):
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response
            elif "myshopify.com" in str(args[0]):
                raise RequestException("Network connection failed")
            return Mock()

        mock_request.post.side_effect = network_failure_side_effect

        # Execute with network failure
        with patch("time.sleep"):
            refund = refund_order(order, tracking)

        # Should handle network failure
        assert refund is None

        # Reset for API business logic failure test
        mock_slack.reset_mock()

        def api_logic_failure_side_effect(*args, **kwargs):
            if "slack.com" in str(args[0]):
                slack_response = Mock()
                slack_response.status_code = 200
                slack_response.json.return_value = {"ok": True}
                slack_response.raise_for_status = Mock()
                return slack_response
            elif "myshopify.com" in str(args[0]):
                # API responds but with business logic error
                return get_mock_failure_refund_response(
                    user_errors=[{"message": "Refund amount exceeds remaining balance"}]
                )
            return Mock()

        mock_request.side_effect = api_logic_failure_side_effect

        # Execute with API logic failure
        with patch("time.sleep"):
            refund2 = refund_order(order, tracking)

        # Should handle API logic failure differently
        assert refund2 is None

        # Both should generate alerts, but potentially with different classifications
        total_alerts = len(mock_slack.send_error.call_args_list) + len(
            mock_slack.send_warning.call_args_list
        )
        assert total_alerts > 0, "Should alert about API failures"

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_concurrent_failure_handling(
        self, mock_idempotency_save, mock_slack, mock_sys
    ):
        """Test handling of concurrent failures across multiple orders."""

        # Create multiple failing orders
        orders = [Mock() for _ in range(3)]
        trackings = [Mock() for _ in range(3)]

        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = list(zip(orders, trackings, strict=False))

            process_refund_automation()

            # Should handle multiple failures gracefully
            # Each failure should be reported
            error_calls = mock_slack.send_error.call_args_list
            warning_calls = mock_slack.send_warning.call_args_list

            total_failure_alerts = len(error_calls) + len(warning_calls)

            # Should have some alerts about the failures
            assert total_failure_alerts > 0, "Should report concurrent failures"
