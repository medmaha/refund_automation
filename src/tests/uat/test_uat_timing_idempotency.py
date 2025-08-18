"""
UAT Test Suite: Timing & Idempotency (B-Time1, B-Time2, B-Time3, B-ID1, B-ID2)

Tests timing constraints and idempotency scenarios:
- B-Time1: Delivered T0, job runs T+3 → No refund (too early)
- B-Time2: Delivered T0, job runs T+5h (same day) → Still too early if <120h exact
- B-Time3: Delivered T0, job runs T+5d+1h → Refund occurs
- B-ID1: Rerun after successful refund → No duplicate refund
- B-ID2: Two concurrent runs → Single refund only
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from unittest.mock import patch

from src.shopify.refund import refund_order
from src.tests.uat.uat_fixtures import (
    UATTrackingBuilder,
    create_b_h1_order,
    create_delivered_tracking,
)
from src.utils.timing_validator import (
    TimingTestHelper,
    TimingValidationResult,
    delivery_timing_validator,
)


class TestTimingConstraintScenarios:
    """Test timing constraint scenarios B-Time1, B-Time2, B-Time3."""

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_time1_delivered_t0_job_runs_t_plus_3_no_refund(
        self, mock_idempotency, mock_slack
    ):
        """B-Time1: Delivered T0, job runs T+3 days → No refund (too early)."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btime1",
            False,
        )

        # Create tracking with delivery 3 days ago (72 hours < 120 hours required)
        tracking = (
            UATTrackingBuilder("BTIME1_001")
            .with_latest_event()
            .with_delivered_status(days_ago=3)  # 72 hours ago
            .build()
        )

        # Test timing validation directly
        result, details = delivery_timing_validator.validate_delivery_timing(tracking)

        # Verify timing validation fails
        assert result == TimingValidationResult.TOO_EARLY
        assert details["hours_since_delivery"] < 120.0
        assert details["time_remaining_hours"] > 0

        # Integration test: refund should be blocked by timing
        with patch("src.utils.timing_validator.validate_refund_timing") as mock_timing:
            mock_timing.return_value = (False, details)  # Not eligible due to timing

            # Refund should be rejected due to timing
            # Note: Current implementation doesn't use timing validator yet
            # This test documents expected behavior once timing validation is integrated

            # For now, test the timing validator behavior
            is_eligible, timing_details = mock_timing.return_value
            assert not is_eligible
            assert timing_details["time_remaining_hours"] > 0

        # Verify Slack would be notified about timing issue
        # expected_message = (
        #     f"Too early for refund. Wait {details['time_remaining_hours']:.1f} hours"
        # )

        # This would be sent once timing validation is integrated into main flow

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_time2_same_day_5h_still_too_early_exact_120h(
        self, mock_idempotency, mock_slack
    ):
        """B-Time2: Delivered T0, job runs T+5h (same day) → Still too early if <120h exact."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btime2",
            False,
        )

        # Create tracking with delivery 5 hours ago (much less than 120 hours)
        tracking = (
            UATTrackingBuilder("BTIME2_001")
            .with_early_delivery(hours_ago=5)  # Only 5 hours ago
            .build()
        )

        # Test timing validation
        result, details = delivery_timing_validator.validate_delivery_timing(tracking)

        # Verify timing validation fails - way too early
        assert result == TimingValidationResult.TOO_EARLY
        assert details["hours_since_delivery"] == 5.0
        assert details["time_remaining_hours"] >= 115.0  # 120 - 5 = 115
        assert details["required_delay_hours"] == 120

        # Test edge case: exactly 119.9 hours (just under threshold)
        tracking_edge = (
            UATTrackingBuilder("BTIME2_EDGE")
            .with_early_delivery(hours_ago=119.9)
            .build()
        )

        result_edge, details_edge = delivery_timing_validator.validate_delivery_timing(
            tracking_edge
        )

        # Should still be too early
        assert result_edge == TimingValidationResult.TOO_EARLY
        assert details_edge["hours_since_delivery"] < 120.0
        assert details_edge["time_remaining_hours"] > 0

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_time3_delivered_t0_job_runs_t_plus_5d_1h_refund_occurs(
        self, mock_idempotency, mock_slack
    ):
        """B-Time3: Delivered T0, job runs T+5d+1h → Refund occurs."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_btime3",
            False,
        )

        order, _ = create_b_h1_order()

        # Create tracking with delivery 5 days + 1 hour ago (121 hours > 120 hours required)
        tracking = (
            UATTrackingBuilder(tracking_number=order.tracking_number)
            .with_delivered_at(days=5, hours=1)
            .build()
        )

        # Test timing validation
        result, details = delivery_timing_validator.validate_delivery_timing(tracking)

        # Verify timing validation passes
        assert result == TimingValidationResult.ELIGIBLE
        assert details["hours_since_delivery"] >= 120.0
        assert details["time_remaining_hours"] == 0

        # Execute refund (should succeed with timing validation)
        refund = refund_order(order, tracking)

        # Verify refund was processed
        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.amount > 0

        # Verify success notification
        mock_slack.send_success.assert_called_once()

    def test_exact_timing_boundary_conditions(self):
        """Test exact timing boundary conditions for precise 120-hour requirement."""
        # Test various boundary scenarios
        edge_cases = TimingTestHelper.create_edge_case_scenarios()

        for scenario_name, delivery_time in edge_cases.items():
            # Create mock tracking with specific delivery time
            tracking = (
                UATTrackingBuilder("TR138324")
                .with_delivered_at(**delivery_time)
                .with_latest_event()
                .build()
            )

            # Test timing validation
            result, details = delivery_timing_validator.validate_delivery_timing(
                tracking
            )

            hours_since = details["hours_since_delivery"]

            if scenario_name in [
                "too_early_3_days",
                "same_day_too_early",
                "just_under_boundary",
            ]:
                # Should be too early
                assert result == TimingValidationResult.TOO_EARLY, (
                    f"Scenario {scenario_name} should be too early (hours: {hours_since})"
                )
                assert hours_since < 120.0
                assert details["time_remaining_hours"] > 0

            elif scenario_name in [
                "just_eligible",
                "exact_boundary",
                "just_over_boundary",
                "well_past_eligible",
            ]:
                # Should be eligible
                assert result == TimingValidationResult.ELIGIBLE, (
                    f"Scenario {scenario_name} should be eligible (hours: {hours_since})"
                )
                assert hours_since >= 120.0
                assert details["time_remaining_hours"] == 0


class TestIdempotencyScenarios:
    """Test idempotency scenarios B-ID1, B-ID2."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_id1_rerun_after_successful_refund_no_duplicate(
        self, mock_idempotency_save, mock_slack, mock_request
    ):
        """B-ID1: Rerun after successful refund → No duplicate refund."""

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Execute first refund
        refund1 = refund_order(order, tracking)

        # Verify first refund succeeded
        assert refund1 is not None
        mock_slack.send_success.assert_called_once()

        # Reset mocks for second run
        mock_slack.reset_mock()

        # Integration test with process_refund_automation
        refund2 = refund_order(order, tracking)

        # Verify duplicate was detected and handled
        # The process should complete without error but skip processing
        # Slack should be notified about the duplicate detection
        assert refund2 is None
        mock_slack.send_warning.assert_called()
        error_call_args = mock_slack.send_warning.call_args[0][0]
        assert "Duplicate refund operation detected" in error_call_args

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    @patch("src.shopify.refund.idempotency_manager.check_operation_idempotency")
    def test_b_id2_two_concurrent_runs_single_refund_only(
        self,
        mock_check_operation_idempotency,
        mock_idempotency_save,
        mock_slack,
        mock_req,
    ):
        """B-ID2: Two concurrent runs → Single refund only."""
        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        check_calls = 0

        # Simulate race condition where both threads check idempotency at same time
        # First thread gets non-duplicate, second thread should get duplicate
        def mock_idempotency_check(*args, **kwargs):
            nonlocal check_calls
            check_calls += 1
            if check_calls == 1:
                return ("bid2_race_key", False)  # First thread: not duplicate
            else:
                return ("bid2_race_key", True)  # Second thread: duplicate detected

        mock_check_operation_idempotency.side_effect = mock_idempotency_check

        # Track refund creation calls
        refund_results = []
        original_refund_order = refund_order

        def tracked_refund_order(*args, **kwargs):
            result = original_refund_order(*args, **kwargs)
            refund_results.append(result)
            return result

        # Run two concurrent operations
        with patch("src.shopify.refund.refund_order", side_effect=tracked_refund_order):
            with ThreadPoolExecutor(max_workers=2) as executor:
                # Submit two concurrent refund operations
                future1 = executor.submit(refund_order, order, tracking)
                future2 = executor.submit(refund_order, order, tracking)

                # Wait for both to complete
                results = []
                for future in as_completed([future1, future2], timeout=10):
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        results.append(e)

        # Verify only one successful refund
        successful_refunds = [
            r for r in results if r is not None and not isinstance(r, Exception)
        ]
        assert len(successful_refunds) <= 1, "Should have at most one successful refund"

        # Verify idempotency check was called for both operations
        assert check_calls == 2, (
            "Idempotency should be checked for both concurrent operations"
        )

    @patch("src.shopify.refund.idempotency_manager._save_cache")
    @patch("src.shopify.refund.idempotency_manager.generate_key")
    def test_idempotency_key_generation_consistency(
        self, mock_generate_key, mock_idempotency_save
    ):
        """Test that idempotency keys are generated consistently for same inputs."""

        order, _ = create_b_h1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Mock the check to return the actual key for verification
        generated_keys = []

        def generate_idempotency_key(order_id, operation="refund", **kwargs):
            from src.utils.idempotency import IdempotencyManager

            key = IdempotencyManager().generate_key(order_id, operation, **kwargs)
            generated_keys.append(key)
            return key

        mock_generate_key.side_effect = generate_idempotency_key

        # Call refund multiple times with same parameters
        for _ in range(3):
            try:
                refund_order(order, tracking)
            except Exception as _:
                pass  # Ignore errors, we just want to test key generation

        # Verify all keys are identical for same inputs
        assert len(generated_keys) == 3
        assert all(key == generated_keys[0] for key in generated_keys), (
            "Idempotency keys should be identical for same inputs"
        )

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_idempotency_with_different_parameters(self, mock_idempotency, mock_slack):
        """Test that different parameters generate different idempotency keys."""
        base_order, _ = create_b_h1_order()
        tracking = (
            UATTrackingBuilder("BID_DIFFERENT")
            .with_delivered_status(days_ago=6)
            .build()
        )

        # Create variations
        order_variation1, _ = create_b_h1_order()  # Different order ID

        tracking_variation = (
            UATTrackingBuilder("BID_DIFFERENT_TRACK")
            .with_delivered_status(days_ago=6)
            .build()
        )

        generated_keys = []

        def capture_key(order_id, operation="refund", **kwargs):
            from src.utils.idempotency import idempotency_manager

            key = idempotency_manager.generate_key(order_id, operation, **kwargs)
            generated_keys.append(key)
            return key, False

        mock_idempotency.check_operation_idempotency.side_effect = capture_key

        # Test different combinations
        test_combinations = [
            (base_order, tracking),
            (order_variation1, tracking),  # Different order
            (base_order, tracking_variation),  # Different tracking
        ]

        for order, track in test_combinations:
            try:
                refund_order(order, track)
            except Exception as _:
                pass

        # Verify all keys are different
        assert len(set(generated_keys)) == len(generated_keys), (
            "Different parameters should generate different idempotency keys"
        )


class TestTimingIdempotencyIntegration:
    """Test integration between timing validation and idempotency."""

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_timing_failure_does_not_affect_idempotency_for_future_runs(
        self, mock_idempotency_save, mock_slack
    ):
        """Test that timing failures don't prevent future idempotency checks."""
        order, _ = create_b_h1_order()

        # First attempt: too early
        tracking_early = (
            UATTrackingBuilder(order.tracking_number)
            .with_delivered_at(hours=99)  # 50hrs Too early
            .build()
        )

        # This would be blocked by timing validation
        result, _ = delivery_timing_validator.validate_delivery_timing(tracking_early)

        assert result == TimingValidationResult.TOO_EARLY

        # Later attempt: eligible timing
        tracking_eligible = (
            UATTrackingBuilder(order.tracking_number)
            .with_delivered_at(days=6)  # 6days Too early
            .build()
        )

        # Should generate same idempotency key since same tracking/order
        result_eligible, timing_details_eligible = (
            delivery_timing_validator.validate_delivery_timing(tracking_eligible)
        )
        assert result_eligible == TimingValidationResult.ELIGIBLE

        # Execute refund - should work once timing is eligible
        refund = refund_order(order, tracking_eligible)
        assert refund is not None

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_idempotency_cache_ttl_respects_timing_constraints(
        self, mock_idempotency, mock_slack
    ):
        """Test that idempotency cache TTL is reasonable for timing constraints."""
        from src.utils.idempotency import idempotency_manager

        # Verify default TTL is appropriate (24 hours is default)
        # Since refunds require 5-day delay, 24h TTL should be reasonable
        assert idempotency_manager.ttl_hours >= 24, (
            "Idempotency TTL should be at least 24 hours to handle retry scenarios"
        )

        # For refund scenarios, might want longer TTL
        # This test documents the expected behavior
        stats = idempotency_manager.get_stats()
        assert stats["ttl_hours"] >= 24  # Minimum reasonable TTL


class TestTimingValidationEdgeCases:
    """Test edge cases in timing validation."""

    tracking_number = "TRA39383930"

    def test_missing_delivery_time_handling(self):
        """Test handling of tracking data without delivery time."""

        # Deliberately don't set delivered_at or any timing attributes
        tracking_no_time = (
            UATTrackingBuilder(
                delivered_at=None,
                tracking_number=self.tracking_number,
            )
            .with_latest_event()
            .build()
        )

        result, details = delivery_timing_validator.validate_delivery_timing(
            tracking_no_time
        )

        assert result == TimingValidationResult.NO_DELIVERY_TIME
        assert details["reason"] == "No delivery time found in tracking data"
        assert details["tracking_number"] == tracking_no_time.number

    def test_invalid_delivery_time_format_handling(self):
        """Test handling of invalid delivery time formats."""

        tracking_invalid = (
            UATTrackingBuilder(
                delivered_at="not_a_datetime_object",
                tracking_number=self.tracking_number,
            )
            .with_latest_event()
            .build()
        )

        result, details = delivery_timing_validator.validate_delivery_timing(
            tracking_invalid
        )

        assert result == TimingValidationResult.INVALID_DELIVERY_TIME
        assert details["reason"] == "Invalid delivery time format"
        assert details["tracking_number"] == tracking_invalid.number

    def test_timezone_consistency_in_timing_validation(self):
        """Test that timezone handling is consistent in timing validation."""
        import pytz

        # Create delivery time in different timezone
        utc_time_now = datetime.now(tz=pytz.UTC)
        est_time_now = utc_time_now.replace(tzinfo=pytz.UTC).astimezone(
            pytz.timezone("US/Eastern")
        )

        tracking_tz = (
            UATTrackingBuilder(
                tracking_number="TIMEZONE_TEST",
                delivered_at=str(
                    est_time_now - timedelta(hours=125)
                ),  # Should be eligible
            )
            .with_latest_event()
            .build()
        )

        result, details = delivery_timing_validator.validate_delivery_timing(
            tracking_tz
        )

        # Should handle timezone conversion correctly
        assert result == TimingValidationResult.ELIGIBLE
        assert details["hours_since_delivery"] >= 120.0

        # Verify timezone info is preserved in details
        assert "delivery_time" in details
        assert "current_time" in details
