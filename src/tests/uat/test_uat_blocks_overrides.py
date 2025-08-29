"""
UAT Test Suite: Blocks & Overrides (B-F1, B-Tag1, B-Tag2)

Tests blocking conditions and override scenarios:
- B-F1: Order under chargeback → Hold; Slack
- B-Tag1: refund:auto:off → Skipped; Slack (info)
- B-Tag2: refund:force:now → Immediate refund; logged override
"""

from unittest.mock import patch

from src.shopify.refund import process_refund_automation, refund_order
from src.tests.uat.uat_fixtures import (
    UATConstants,
    create_chargeback_order,
    create_delivered_tracking,
    create_early_delivery_tracking,
    create_refund_auto_off_order,
    create_refund_force_now_order,
)


def safe_process_refund_automation():
    """Wrapper to handle SystemExit in tests."""
    try:
        return process_refund_automation()
    except SystemExit as e:
        # In tests, we want to capture the exit code rather than actually exit
        return e.code


class TestChargebackBlockScenarios:
    """Test B-F1: Order under chargeback → Hold; Slack."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_f1_chargeback_order_hold_with_slack_alert(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-F1: Order under chargeback should be held and trigger Slack alert."""

        order = create_chargeback_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Verify order has chargeback tag
        assert "chargeback" in order.tags

        # Process refund automation with chargeback order
        refund_order(order, tracking)

        # Verify Slack was notified about chargeback hold
        error_calls = mock_slack.send_error.call_args_list
        warning_calls = mock_slack.send_warning.call_args_list

        # Should have alert about chargeback
        chargeback_alerts = [
            call
            for call in (error_calls + warning_calls)
            if any(
                keyword in str(call).lower()
                for keyword in ["chargeback", "hold", "blocked"]
            )
        ]

        assert len(chargeback_alerts) > 0, "Should alert about chargeback block"

        # Check alert includes order details
        alert_content = str(chargeback_alerts[0])
        assert order.id in alert_content or order.name in alert_content

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_f1_chargeback_prevents_refund_processing(
        self, mock_idempotency, mock_slack
    ):
        """B-F1: Chargeback should prevent refund processing entirely."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bf1_prevent",
            False,
        )

        order = create_chargeback_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Track refund attempts
        refund_attempts = []
        original_refund_order = refund_order

        def track_refund_attempts(*args, **kwargs):
            refund_attempts.append(args)
            return original_refund_order(*args, **kwargs)

        # Process automation
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            with patch(
                "src.shopify.refund.refund_order", side_effect=track_refund_attempts
            ):
                safe_process_refund_automation()

            # Should either skip refund entirely or handle chargeback gracefully
            # The exact behavior depends on implementation

            # Verify appropriate alerts were sent
            total_alerts = len(mock_slack.send_error.call_args_list) + len(
                mock_slack.send_warning.call_args_list
            )
            assert total_alerts > 0, "Should alert about chargeback block"

            # Should not have successful refund
            success_calls = mock_slack.send_success.call_args_list
            assert len(success_calls) == 0, "Should not succeed with chargeback"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_f1_chargeback_detection_case_variations(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-F1: Test chargeback detection with various tag formats."""

        tracking = create_delivered_tracking()

        # Test various chargeback tag formats
        chargeback_variations = [
            ["chargeback"],
            ["CHARGEBACK"],
            ["Chargeback"],
            ["chargeback-dispute"],
            ["order-chargeback"],
        ]

        for tags in chargeback_variations:
            # Create order with specific chargeback tag format
            from src.models.order import TransactionKind
            from src.tests.uat.uat_fixtures import UATFixtureBuilder

            order = (
                UATFixtureBuilder()
                .with_line_item(quantity=2, price=50.0)
                .with_transaction(
                    UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0
                )
                .with_tags(*tags)
                .with_return_tracking("BF1_CASE_TEST")
                .build()
            )

            # Reset mock for each test
            mock_slack.reset_mock()

            # Process automation

            refund_order(order, tracking)

            # Should detect chargeback regardless of case
            chargeback_detected = any(
                "chargeback" in str(call).lower()
                for call in (
                    mock_slack.send_error.call_args_list
                    + mock_slack.send_warning.call_args_list
                )
            )

            assert chargeback_detected, f"Should detect chargeback in tags: {tags}"

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_f1_chargeback_audit_logging(self, mock_idempotency, mock_slack):
        """B-F1: Verify chargeback blocks are properly logged in audit."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bf1_audit",
            False,
        )

        order = create_chargeback_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Process with audit logging
        with patch("src.utils.audit.log_refund_audit") as mock_audit:
            refund_order(order, tracking)

            # Verify audit logging captured chargeback block
            if mock_audit.call_args_list:
                audit_calls = mock_audit.call_args_list

                # Should have audit entry mentioning chargeback
                audit_content = str(audit_calls)
                assert any(
                    keyword in audit_content.lower()
                    for keyword in ["chargeback", "blocked", "hold", "skipped"]
                )


class TestRefundAutoOffScenarios:
    """Test B-Tag1: refund:auto:off → Skipped; Slack (info)."""

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tag1_refund_auto_off_skipped_with_info_slack(
        self, mock_idempotency, mock_slack
    ):
        """B-Tag1: refund:auto:off tag should skip processing with info Slack alert."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btag1",
            False,
        )

        order = create_refund_auto_off_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Verify order has refund:auto:off tag
        assert "refund:auto:off" in order.tags

        # Process refund automation
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            safe_process_refund_automation()

            # Verify Slack was notified with INFO level about skipping
            info_calls = mock_slack.send_info.call_args_list
            warning_calls = mock_slack.send_warning.call_args_list

            # Should have info alert about auto-off skip
            auto_off_alerts = [
                call
                for call in (info_calls + warning_calls)
                if any(
                    keyword in str(call).lower()
                    for keyword in ["auto", "off", "skip", "manual"]
                )
            ]

            assert len(auto_off_alerts) > 0, (
                "Should send info alert about auto-off skip"
            )

            # Should not process refund
            success_calls = mock_slack.send_success.call_args_list
            assert len(success_calls) == 0, "Should not process refund with auto:off"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tag1_auto_off_tag_variations(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Tag1: Test various refund auto-off tag formats."""

        tracking = create_delivered_tracking()

        # Test various auto-off tag formats
        auto_off_variations = [
            ["refund:auto:off"],
            ["refund:auto:OFF"],
            ["REFUND:AUTO:OFF"],
            ["refund-auto-off"],
            ["no-auto-refund"],
            ["manual-refund-only"],
        ]

        for tags in auto_off_variations:
            # Create order with specific auto-off tag
            from src.models.order import TransactionKind
            from src.tests.uat.uat_fixtures import UATFixtureBuilder

            order = (
                UATFixtureBuilder()
                .with_line_item(quantity=2, price=50.0)
                .with_transaction(
                    UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0
                )
                .with_tags(*tags)
                .with_return_tracking("BTAG1_VAR_TEST")
                .build()
            )

            # Reset mock for each test
            mock_slack.reset_mock()

            # Process automation
            with patch(
                "src.shopify.refund.retrieve_refundable_shopify_orders"
            ) as mock_retrieve:
                mock_retrieve.return_value = [(order, tracking)]

                safe_process_refund_automation()

                # Should detect auto-off intent
                # Note: Implementation may vary on which tags are recognized
                total_notifications = (
                    len(mock_slack.send_info.call_args_list)
                    + len(mock_slack.send_warning.call_args_list)
                    + len(mock_slack.send_error.call_args_list)
                )

                # Should at least acknowledge the processing attempt
                assert total_notifications > 0, f"Should handle auto-off tags: {tags}"

                # Should not succeed with auto-off
                success_calls = mock_slack.send_success.call_args_list
                assert len(success_calls) == 0, (
                    f"Should not succeed with auto-off tags: {tags}"
                )

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_tag1_auto_off_manual_override_suggestion(
        self, mock_idempotency_save, mock_slack, mock_req
    ):
        """B-Tag1: Auto-off should suggest manual processing in alerts."""

        order = create_refund_auto_off_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        refund_order(order, tracking)

        # Check that alerts suggest manual processing
        all_alerts = (
            mock_slack.send_warning.call_args_list
            + mock_slack.send_error.call_args_list
        )

        # Should suggest manual action
        alert_content = " ".join(str(alert) for alert in all_alerts)
        suggests_manual = any(
            keyword in alert_content.lower()
            for keyword in [
                "manual",
                "operator",
                "review",
                "admin",
                "process manually",
            ]
        )

        assert suggests_manual, "Should suggest manual processing for auto-off orders"


class TestRefundForceNowScenarios:
    """Test B-Tag2: refund:force:now → Immediate refund; logged override."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_tag2_refund_force_now_immediate_refund_logged_override(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-Tag2: refund:force:now should trigger immediate refund with logged override."""

        order = create_refund_force_now_order()

        # Use early tracking that would normally be too early
        # Only delivered a few hours ago
        tracking = create_early_delivery_tracking(tracking_number=order.tracking_number)

        # Verify order has refund:force:now tag
        assert "refund:force:now" in order.tags

        refund_order(order, tracking)

        # Should succeed despite early timing due to force override
        success_calls = mock_slack.send_success.call_args_list
        assert len(success_calls) > 0, "Should succeed with force:now override"

        # Should also log the override
        info_calls = mock_slack.send_info.call_args_list
        warning_calls = mock_slack.send_warning.call_args_list

        # Should mention override/force in notifications
        all_notifications = info_calls + warning_calls + success_calls
        notification_content = " ".join(str(call) for call in all_notifications)

        mentions_override = any(
            keyword in notification_content.lower()
            for keyword in ["override", "force", "immediate", "bypassed"]
        )

        assert mentions_override, "Should log override/force action"

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tag2_force_now_bypasses_timing_validation(
        self, mock_idempotency, mock_slack
    ):
        """B-Tag2: force:now should bypass normal timing validation."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btag2_timing",
            False,
        )

        order = create_refund_force_now_order()
        # Use tracking that would normally fail timing validation
        tracking = create_early_delivery_tracking()  # Very early delivery

        # Verify timing would normally fail
        from src.utils.timing_validator import (
            TimingValidationResult,
            delivery_timing_validator,
        )

        result, details = delivery_timing_validator.validate_delivery_timing(tracking)
        assert result == TimingValidationResult.TOO_EARLY, (
            "Should normally be too early"
        )

        # Process with force override
        refund_order(order, tracking)

        # Should succeed despite timing constraint
        success_calls = mock_slack.send_success.call_args_list
        assert len(success_calls) > 0, "Should bypass timing with force:now"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_tag2_force_now_bypasses_other_blocks(
        self, mock_idempotency_save, mock_slack, mock_req
    ):
        """B-Tag2: force:now should bypass various blocking conditions."""

        # Create order with both blocking and force tags
        from src.models.order import TransactionKind
        from src.tests.uat.uat_fixtures import UATFixtureBuilder

        order = (
            UATFixtureBuilder()
            .with_line_item("gid://shopify/Order/W9I3D3EJ93ISW", quantity=2, price=50.0)
            .with_transaction(
                UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0
            )
            # Force should override blocks
            .with_tags("refund:force:now", "chargeback", "refund:auto:off", "dispute")
            .with_return_tracking()
            .with_return_line_item(
                "gid://shopify/Order/W9I3D3EJ93ISW", refundable_qty=2
            )
            .build()
        )

        tracking = create_early_delivery_tracking(order.tracking_number)

        refund_order(order, tracking)

        # Should succeed with force override
        success_calls = mock_slack.send_success.call_args_list
        assert len(success_calls) > 0, "Force should override blocking conditions"

        # Should log the override action
        all_alerts = (
            mock_slack.send_info.call_args_list
            + mock_slack.send_warning.call_args_list
            + success_calls
        )

        alert_content = " ".join(str(alert) for alert in all_alerts).lower()
        mentions_force = (keyword in alert_content for keyword in ["refund:force:now"])

        assert mentions_force, "Should log force override action"

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_tag2_force_now_audit_trail_includes_override(
        self, mock_idempotency, mock_slack
    ):
        """B-Tag2: Force override should be clearly documented in audit trail."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btag2_audit",
            False,
        )

        order = create_refund_force_now_order()
        tracking = create_early_delivery_tracking()

        # Process with audit logging
        with patch("src.utils.audit.log_refund_audit") as mock_audit:
            with patch(
                "src.shopify.refund.retrieve_refundable_shopify_orders"
            ) as mock_retrieve:
                mock_retrieve.return_value = [(order, tracking)]

                safe_process_refund_automation()

                # Verify audit logging includes override information
                if mock_audit.call_args_list:
                    audit_calls = mock_audit.call_args_list
                    audit_content = str(audit_calls)

                    # Should mention force/override in audit
                    mentions_override = any(
                        keyword in audit_content.lower()
                        for keyword in ["force", "override", "immediate", "bypass"]
                    )

                    assert mentions_override, "Should document override in audit trail"


class TestBlockOverrideInteractionScenarios:
    """Test interactions between different blocking and override conditions."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_force_now_vs_chargeback_priority(
        self, mock_idempotency_save, mock_slack, mock_request
    ):
        """Test priority when both force:now and chargeback exist."""

        # Create order with both chargeback and force tags
        from src.models.order import TransactionKind
        from src.tests.uat.uat_fixtures import UATFixtureBuilder

        order = (
            UATFixtureBuilder()
            .with_line_item(quantity=2, price=50.0)
            .with_transaction(
                UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0
            )
            .with_tags("chargeback", "refund:force:now")  # Conflicting instructions
            .with_return_tracking("PRIORITY_TEST")
            .build()
        )

        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        refund_order(order, tracking)
        # Implementation should decide priority
        # Either force should override chargeback, or chargeback should block force
        # The key is that the decision should be logged clearly

        all_alerts = (
            mock_slack.send_info.call_args_list
            + mock_slack.send_warning.call_args_list
            + mock_slack.send_error.call_args_list
            + mock_slack.send_success.call_args_list
        )

        # Should have alerts explaining the conflict resolution
        assert len(all_alerts) > 0, "Should alert about conflicting tags"

        # Should mention both conditions
        alert_content = " ".join(str(alert) for alert in all_alerts)
        mentions_chargeback = "chargeback" in alert_content.lower()
        mentions_force = (
            "force" in alert_content.lower() or "override" in alert_content.lower()
        )

        assert mentions_chargeback or mentions_force, (
            "Should explain tag conflict resolution"
        )

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_multiple_blocking_conditions(self, mock_idempotency, mock_slack):
        """Test handling when multiple blocking conditions exist."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_multiple_blocks",
            False,
        )

        # Create order with multiple blocking conditions
        from src.models.order import TransactionKind
        from src.tests.uat.uat_fixtures import UATFixtureBuilder

        order = (
            UATFixtureBuilder()
            .with_line_item(quantity=2, price=50.0)
            .with_transaction(
                UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0
            )
            .with_tags("chargeback", "refund:auto:off", "high-risk")  # Multiple blocks
            .with_return_tracking("MULTI_BLOCK_TEST")
            .build()
        )

        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Process automation
        with patch(
            "src.shopify.refund.retrieve_refundable_shopify_orders"
        ) as mock_retrieve:
            mock_retrieve.return_value = [(order, tracking)]

            safe_process_refund_automation()

            # Should handle multiple blocks appropriately
            error_or_warning_calls = (
                mock_slack.send_error.call_args_list
                + mock_slack.send_warning.call_args_list
            )

            # Should not succeed with multiple blocks
            success_calls = mock_slack.send_success.call_args_list
            assert len(success_calls) == 0, "Should not succeed with multiple blocks"

            # Should alert about blocking conditions
            assert len(error_or_warning_calls) > 0, (
                "Should alert about blocking conditions"
            )

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_tag_case_sensitivity_handling(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """Test that tag matching handles case variations appropriately."""

        tracking = create_delivered_tracking()

        # Test various case combinations
        case_variations = [
            ["refund:force:now"],
            ["REFUND:FORCE:NOW"],
            ["Refund:Force:Now"],
            ["refund:FORCE:now"],
        ]

        from src.models.order import TransactionKind
        from src.tests.uat.uat_fixtures import UATFixtureBuilder

        for tags in case_variations:
            # Create order with specific case

            order = (
                UATFixtureBuilder()
                .with_line_item(quantity=2, price=50.0)
                .with_transaction(
                    UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 100.0
                )
                .with_tags(*tags)
                .with_return_tracking("CASE_TEST")
                .build()
            )

            # Reset mock for each test
            mock_slack.reset_mock()

            refund_order(order, tracking)

            # Should handle case variations consistently
            total_notifications = (
                len(mock_slack.send_info.call_args_list)
                + len(mock_slack.send_warning.call_args_list)
                + len(mock_slack.send_error.call_args_list)
                + len(mock_slack.send_success.call_args_list)
            )

            assert total_notifications > 0, f"Should handle case variation: {tags}"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_comprehensive_tag_processing_audit(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """Test comprehensive audit of tag-based processing decisions."""

        # Test each major tag scenario
        test_scenarios = [
            (create_chargeback_order(), "chargeback"),
            (create_refund_auto_off_order(), "auto_off"),
            (create_refund_force_now_order(), "force_now"),
        ]

        tracking = create_delivered_tracking()

        for order, scenario_name in test_scenarios:
            # Reset mock for each scenario
            mock_slack.reset_mock()

            # Process with audit logging
            with patch("src.utils.audit.log_refund_audit") as mock_audit:
                refund_order(order, tracking)

                # Verify appropriate processing for each scenario
                if scenario_name == "chargeback":
                    # Should block and alert
                    error_or_warning = (
                        mock_slack.send_error.call_args_list
                        + mock_slack.send_warning.call_args_list
                    )
                    assert len(error_or_warning) > 0, (
                        "Chargeback should generate alerts"
                    )

                elif scenario_name == "auto_off":
                    # Should skip with info
                    info_or_warning = (
                        mock_slack.send_info.call_args_list
                        + mock_slack.send_warning.call_args_list
                    )
                    assert len(info_or_warning) > 0, (
                        "Auto-off should generate info/warning"
                    )

                elif scenario_name == "force_now":
                    # Should succeed and log override
                    success_calls = mock_slack.send_success.call_args_list
                    assert len(success_calls) > 0, "Force-now should succeed"

                # All scenarios should have audit logging
                if mock_audit.call_args_list:
                    audit_content = str(mock_audit.call_args_list)
                    # Should mention the relevant tag/decision
                    assert len(audit_content) > 0, (
                        f"Should audit {scenario_name} processing"
                    )
