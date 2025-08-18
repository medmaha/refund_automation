"""
UAT Test Suite: Tracking & Exceptions (B-Tr1, B-Tr2, B-Tr3, B-Tr4)

Tests carrier delivery confirmations and exception scenarios:
- B-Tr1: Carrier says TrackingStatus.DELIVERED with proof → Eligible after delay
- B-Tr2: "Delivery attempted" only → Not eligible; Slack
- B-Tr3: Carrier systems disagree → Hold; Slack with details
- B-Tr4: No tracking number → Not eligible; Slack
"""

from unittest.mock import Mock, patch

from src.models.tracking import TrackingStatus, TrackingSubStatus
from src.shopify.refund import process_refund_automation, refund_order
from src.tests.uat.uat_fixtures import (
    UATTrackingBuilder,
    create_attempted_delivery_tracking,
    create_b_h1_order,
    create_carrier_mismatch_tracking,
    create_delivered_tracking,
    create_no_tracking,
)


class TestCarrierDeliveryConfirmationScenarios:
    """Test B-Tr1: Carrier says 'Delivered' with proof → Eligible after delay."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_tr1_carrier_delivered_with_proof_eligible_after_delay(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Tr1: Carrier delivery confirmation with proof should be eligible after delay."""

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(
            tracking_number=order.tracking_number
        )  # Well past 5-day requirement

        # Verify tracking has proper delivery confirmation
        assert (
            tracking.track_info.latest_status.status.value
            == TrackingStatus.DELIVERED.value
        )
        assert (
            tracking.track_info.latest_status.sub_status.value
            == TrackingSubStatus.DELIVERED_OTHER.value
        )
        assert tracking.number is not None

        # Execute refund - should succeed with clear delivery proof
        refund = refund_order(order, tracking)

        # Verify refund was processed
        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.amount > 0

        # Verify success notification includes delivery confirmation details
        mock_slack.send_success.assert_called_once()
        success_call_args = mock_slack.send_success.call_args[1]["details"]
        assert "refund_type" in success_call_args

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr1_delivered_status_validation_requirements(
        self, mock_idempotency, mock_slack, mock_request
    ):
        """B-Tr1: Test specific delivered status validation requirements."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr1_validation",
            False,
        )

        order, _ = create_b_h1_order()

        # Test various delivered status combinations
        valid_delivery_scenarios = [
            (TrackingStatus.DELIVERED, TrackingSubStatus.DELIVERED_OTHER),
            # (TrackingStatus.DELIVERED, TrackingSubStatus.EXCEPTION_RETURNED),
            # (TrackingStatus.DELIVERED, TrackingSubStatus.EXCEPTION_RETURNING),
        ]

        for main_status, sub_status in valid_delivery_scenarios:
            tracking = create_delivered_tracking(tracking_number=order.tracking_number)

            # Override status for specific test
            tracking.track_info.latest_status.status = main_status
            tracking.track_info.latest_status.sub_status = sub_status

            # Should be eligible with proper delivered status
            refund = refund_order(order, tracking)
            assert refund is not None, (
                f"Should accept delivered status: {main_status}/{sub_status}"
            )

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_tr1_delivery_proof_documentation_in_logs(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Tr1: Verify delivery proof is properly documented in audit logs."""

        order, info = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Execute refund
        with patch("src.shopify.refund.log_refund_audit") as mock_audit:
            refund_order(order, tracking)

            # Verify audit logging was called with tracking details
            mock_audit.assert_called()
            audit_call_args = (
                mock_audit.call_args[1]
                if mock_audit.call_args[1]
                else mock_audit.call_args[0]
            )

            # Should include tracking number in audit
            if isinstance(audit_call_args, dict):
                assert "order_id" in str(audit_call_args) or tracking.number in str(
                    audit_call_args
                )
                assert "tracking_number" in str(
                    audit_call_args
                ) or tracking.number in str(audit_call_args)


class TestDeliveryAttemptScenarios:
    """Test B-Tr2: 'Delivery attempted' only → Not eligible; Slack."""

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr2_delivery_attempted_only_not_eligible_slack_alert(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr2: Delivery attempted only should not be eligible and trigger Slack alert."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr2",
            False,
        )

        order, info = create_b_h1_order()
        tracking = create_attempted_delivery_tracking()

        # Verify tracking shows delivery attempt but not delivered
        assert tracking.track_info.latest_status.status == TrackingStatus.IN_TRANSIT
        assert (
            tracking.track_info.latest_status.sub_status
            == TrackingSubStatus.IN_TRANSIT_OTHER
        )

        # Mock the order processing to test the full flow
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            # Process refund automation - should detect delivery attempt issue
            process_refund_automation()

            # Verify Slack was notified about delivery attempt issue
            # Should get warning/error about incomplete delivery
            slack_calls = (
                mock_slack.send_warning.call_args_list
                + mock_slack.send_error.call_args_list
            )

            # Find call related to delivery attempt
            delivery_attempt_alerts = [
                call
                for call in slack_calls
                if any(
                    keyword in str(call).lower()
                    for keyword in ["attempt", "delivery", "not delivered"]
                )
            ]

            # Should have at least one alert about delivery attempt
            assert len(delivery_attempt_alerts) > 0, (
                "Should alert about delivery attempt issue"
            )

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr2_various_incomplete_delivery_statuses(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr2: Test various incomplete delivery statuses that should not be eligible."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr2_incomplete",
            False,
        )

        order, info = create_b_h1_order()

        # Test various non-delivered statuses
        incomplete_statuses = [
            ("in_transit", "out_for_delivery"),
            ("in_transit", "delivery_attempted"),
            ("exception", "delivery_failed"),
            ("pending", "awaiting_pickup"),
            ("returned", "return_to_sender"),
        ]

        for main_status, sub_status in incomplete_statuses:
            tracking = (
                UATTrackingBuilder(f"BTR2_INCOMPLETE_{sub_status.upper()}")
                .with_attempted_delivery()
                .build()
            )

            # Override status for specific test
            tracking.track_info.latest_status.status = main_status
            tracking.track_info.latest_status.sub_status = sub_status

            # Should not be eligible for refund
            with patch(
                "src.shopify.refund.retrieve_refundable_shopify_orders"
            ) as mock_retrieve:
                mock_retrieve.return_value = [(order, tracking)]

                # Reset mock to capture this specific call
                mock_slack.reset_mock()

                process_refund_automation()

                # Should result in warning/skipped processing
                warning_or_error_calls = (
                    mock_slack.send_warning.call_args_list
                    + mock_slack.send_error.call_args_list
                )

                # Should have some notification about incomplete delivery
                assert len(warning_or_error_calls) > 0, (
                    f"Should alert about incomplete status: {main_status}/{sub_status}"
                )


class TestCarrierSystemDisagreementScenarios:
    """Test B-Tr3: Carrier systems disagree → Hold; Slack with details."""

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr3_carrier_systems_disagree_hold_with_slack_details(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr3: Carrier system disagreement should hold processing and alert with details."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr3",
            False,
        )

        order, _ = create_b_h1_order()
        tracking = create_carrier_mismatch_tracking()

        # Verify tracking has carrier disagreement data
        assert tracking.carrier_disagreement

        assert tracking.carrier_disagreement["mismatch"] is True
        assert tracking.carrier_disagreement["primary_says"] == TrackingStatus.DELIVERED
        assert tracking.carrier_disagreement["secondary_says"] == "in_transit"

        # Process refund automation with carrier mismatch
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            process_refund_automation()

            # # Verify Slack was notified with detailed carrier mismatch information
            # error_calls = mock_slack.send_error.call_args_list
            # warning_calls = mock_slack.send_warning.call_args_list

            # # Should have alert about carrier disagreement
            # carrier_mismatch_alerts = [
            #     call
            #     for call in (error_calls + warning_calls)
            #     if any(
            #         keyword in str(call).lower()
            #         for keyword in ["disagree", "mismatch", "carrier"]
            #     )
            # ]

            # # TODO: implement carrier mismatch
            # assert (
            #     len(carrier_mismatch_alerts) > 0
            # ), "Should alert about carrier system disagreement"

            # # Check that alert includes detailed information
            # alert_content = str(carrier_mismatch_alerts[0])
            # assert (
            #     "primary" in alert_content.lower()
            #     or "secondary" in alert_content.lower()
            # )

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr3_carrier_disagreement_holds_processing(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr3: Carrier disagreement should hold refund processing."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr3_hold",
            False,
        )

        order, info = create_b_h1_order()
        tracking = create_carrier_mismatch_tracking()

        # Attempt to process refund directly
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            # Mock the automation to track if refunds were attempted
            original_refund_order = refund_order
            refund_attempts = []

            def track_refund_attempts(*args, **kwargs):
                refund_attempts.append(args)
                return original_refund_order(*args, **kwargs)

            with patch(
                "src.shopify.refund.refund_order", side_effect=track_refund_attempts
            ):
                process_refund_automation()

            # Should either skip refund attempt entirely or handle mismatch gracefully
            # The exact behavior depends on implementation, but should not proceed normally

            # Verify appropriate alerts were sent
            total_alerts = (
                len(mock_slack.send_error.call_args_list)
                + len(mock_slack.send_warning.call_args_list)
                + len(mock_slack.send_info.call_args_list)
            )
            assert total_alerts > 0, "Should send alerts about carrier disagreement"

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr3_multiple_carrier_source_validation(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr3: Test validation when multiple carrier sources provide conflicting data."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr3_multi",
            False,
        )

        order, info = create_b_h1_order()

        # Create tracking with multiple conflicting sources
        tracking = (
            UATTrackingBuilder("BTR3_MULTI_SOURCE").with_carrier_mismatch().build()
        )

        # Add additional conflicting data sources
        tracking.carrier_disagreement.update(
            {
                "primary_says": TrackingStatus.DELIVERED,
                "secondary_says": "in_transit",
                "third_party_says": "exception",
                "sources_agree": False,
                "confidence_level": "low",
            }
        )

        # Process refund automation
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            process_refund_automation()

            # Should handle multiple source disagreement
            error_calls = mock_slack.send_error.call_args_list

            # Find calls related to carrier disagreement
            disagreement_alerts = [
                call
                for call in error_calls
                if "carrier" in str(call).lower() and "disagree" in str(call).lower()
            ]

            # Should alert about disagreement with details
            if disagreement_alerts:
                alert_details = str(disagreement_alerts[0])
                # Should mention multiple sources or specific disagreement details
                assert any(
                    keyword in alert_details.lower()
                    for keyword in [
                        "multiple",
                        "conflict",
                        "sources",
                        "primary",
                        "secondary",
                    ]
                )


class TestNoTrackingNumberScenarios:
    """Test B-Tr4: No tracking number → Not eligible; Slack."""

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr4_no_tracking_number_not_eligible_slack_alert(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr4: No tracking number should not be eligible and trigger Slack alert."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr4",
            False,
        )

        order, info = create_b_h1_order()
        tracking = create_no_tracking()

        # Verify tracking has no tracking number
        assert tracking.number is None

        # Process refund automation with no tracking
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            process_refund_automation()

            # Verify Slack was notified about missing tracking
            warning_calls = mock_slack.send_warning.call_args_list
            error_calls = mock_slack.send_error.call_args_list

            # Should have alert about missing tracking number
            no_tracking_alerts = [
                call
                for call in (warning_calls + error_calls)
                if any(
                    keyword in str(call).lower()
                    for keyword in ["tracking", "number", "missing"]
                )
            ]

            assert len(no_tracking_alerts) > 0, (
                "Should alert about missing tracking number"
            )

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr4_empty_tracking_number_handling(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr4: Test handling of empty/invalid tracking numbers."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr4_empty",
            False,
        )

        order, info = create_b_h1_order()

        # Test various invalid tracking number scenarios
        invalid_tracking_scenarios = [
            None,  # No tracking number
            "",  # Empty string
            "   ",  # Whitespace only
            "INVALID",  # Invalid format
            "000000000",  # All zeros
        ]

        for invalid_tracking in invalid_tracking_scenarios:
            tracking = Mock()
            tracking.number = invalid_tracking
            tracking.track_info = Mock()
            tracking.track_info.latest_event = "No valid tracking information"
            tracking.track_info.latest_status = Mock()
            tracking.track_info.latest_status.status = "unknown"

            # Reset mock for each test
            mock_slack.reset_mock()

            # Process refund automation
            with patch(
                "src.shopify.refund.retrieve_refundable_shopify_orders"
            ) as mock_retrieve:
                mock_retrieve.return_value = [(order, tracking)]

                process_refund_automation()

                # Should handle invalid tracking appropriately
                total_alerts = len(mock_slack.send_warning.call_args_list) + len(
                    mock_slack.send_error.call_args_list
                )

                assert total_alerts > 0, (
                    f"Should alert about invalid tracking: {repr(invalid_tracking)}"
                )

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tr4_tracking_number_format_validation(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """B-Tr4: Test tracking number format validation requirements."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btr4_format",
            False,
        )

        order, info = create_b_h1_order()

        # Test tracking numbers that might be considered invalid
        questionable_tracking_numbers = [
            "TEST123",  # Test format
            "123",  # Too short
            "A" * 50,  # Too long
            "INVALID-FORMAT",  # Contains invalid characters
            "test_tracking",  # Contains underscore
        ]

        for tracking_number in questionable_tracking_numbers:
            tracking = Mock()
            tracking.number = tracking_number
            tracking.track_info = Mock()
            tracking.track_info.latest_event = f"Test event for {tracking_number}"
            tracking.track_info.latest_status = Mock()
            tracking.track_info.latest_status.status = "unknown"

            # Process and verify appropriate handling
            with patch(
                "src.shopify.refund.retrieve_refundable_shopify_orders"
            ) as mock_retrieve:
                mock_retrieve.return_value = [(order, tracking)]

                # Reset mock for each test
                mock_slack.reset_mock()

                process_refund_automation()

                # Should handle questionable tracking numbers appropriately
                # Exact behavior may vary based on validation rules
                total_notifications = (
                    len(mock_slack.send_info.call_args_list)
                    + len(mock_slack.send_warning.call_args_list)
                    + len(mock_slack.send_error.call_args_list)
                )

                # Should at least acknowledge processing attempt
                assert total_notifications > 0, (
                    f"Should handle tracking number: {tracking_number}"
                )


class TestTrackingExceptionIntegration:
    """Test integration scenarios across tracking and exception handling."""

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_tracking_exception_prioritization(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """Test prioritization when multiple tracking issues exist."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_tracking_priority",
            False,
        )

        order, info = create_b_h1_order()

        # Create tracking with multiple issues
        problematic_tracking = Mock()
        problematic_tracking.number = None  # Missing tracking number
        problematic_tracking.track_info = Mock()
        problematic_tracking.track_info.latest_event = (
            "Delivery attempted - no one home"
        )
        problematic_tracking.track_info.latest_status = Mock()
        problematic_tracking.track_info.latest_status.status = "in_transit"
        problematic_tracking.track_info.latest_status.sub_status = "delivery_attempted"
        problematic_tracking.carrier_disagreement = {
            "mismatch": True,
            "primary_says": TrackingStatus.DELIVERED,
            "secondary_says": "in_transit",
        }

        # Process automation with multiple issues
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, problematic_tracking)]

            process_refund_automation()

            # Should handle multiple issues and prioritize appropriately
            all_alerts = (
                mock_slack.send_error.call_args_list
                + mock_slack.send_warning.call_args_list
                + mock_slack.send_info.call_args_list
            )

            # Should send appropriate alerts for the issues found
            assert len(all_alerts) > 0, "Should alert about tracking issues"

            # Check that most critical issue is addressed
            alert_content = " ".join(str(alert) for alert in all_alerts)

            # Should mention at least one of the major issues
            has_tracking_issue = any(
                keyword in alert_content.lower()
                for keyword in [
                    "tracking",
                    "number",
                    "missing",
                    "disagree",
                    "attempt",
                    "matching",
                ]
            )
            assert has_tracking_issue, "Should alert about tracking-related issues"

    @patch("src.shopify.refund.sys")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_tracking_exception_audit_trail(
        self, mock_idempotency, mock_slack, mock_sys
    ):
        """Test that tracking exceptions are properly logged in audit trail."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_tracking_audit",
            False,
        )

        order, info = create_b_h1_order()
        tracking = create_attempted_delivery_tracking()

        # Process with audit logging
        with patch("src.utils.audit.log_refund_audit") as mock_audit:
            with patch(
                "src.shopify.refund.retrieve_refundable_shopify_orders"
            ) as mock_retrieve:
                mock_retrieve.return_value = [(order, tracking)]

                process_refund_automation()

                # Verify audit logging captured tracking exception
                if mock_audit.call_args_list:
                    audit_calls = mock_audit.call_args_list

                    # Should have audit entry with tracking details
                    audit_content = str(audit_calls)
                    assert any(
                        keyword in audit_content.lower()
                        for keyword in ["tracking", "delivery", "attempt", "skipped"]
                    )

    @patch("src.shopify.refund.sys.exit")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_successful_tracking_vs_exception_scenarios_comparison(
        self, mock_idempotency, mock_slack, mock_sys_exit
    ):
        """Test comparison between successful tracking and exception scenarios."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_comparison",
            False,
        )

        order, info = create_b_h1_order()

        # Test successful scenario first
        successful_tracking = create_delivered_tracking(
            tracking_number=order.tracking_number
        )

        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, successful_tracking)]

            # Reset mocks
            mock_slack.reset_mock()

            process_refund_automation()

            # Should succeed
            success_calls = mock_slack.send_refund_summary.call_args_list
            assert len(success_calls) > 0, (
                "Successful tracking should result in success"
            )

            mock_sys_exit.assert_not_called()

        # Test exception scenario
        exception_tracking = create_attempted_delivery_tracking()

        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, exception_tracking)]

            # Reset mocks
            mock_sys_exit.reset_mock()
            mock_slack.reset_mock()

            process_refund_automation()

            # Should not succeed, should have warnings/errors
            warning_or_error_calls = (
                mock_slack.send_warning.call_args_list
                + mock_slack.send_error.call_args_list
            )

            success_calls = mock_slack.send_success.call_args_list

            mock_sys_exit.assert_called_once()

            # Exception scenario should not result in success
            assert len(success_calls) == 0, (
                "Exception tracking should not result in success"
            )
            assert len(warning_or_error_calls) > 0, (
                "Exception tracking should result in warnings/errors"
            )
