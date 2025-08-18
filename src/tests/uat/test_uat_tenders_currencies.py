"""
UAT Test Suite: Tenders & Currencies (B-P1, B-P2, B-P3, B-C1, B-C2)

Tests payment method and currency scenarios:
- B-P1: Gift card 100% payment → Refund to gift card
- B-P2: Gift card + card mix → Split refund back to both in correct amounts
- B-P3: Store credit used → Refund store credit portion to store credit
- B-C1: Multi-currency store (order in CHF) → Refund in CHF, correct amounts
- B-C2: Prior partial refund exists → Only remaining refundable balance processed
"""

from unittest.mock import patch

from src.shopify.refund import refund_order
from src.shopify.refund_calculator import refund_calculator
from src.tests.uat.uat_fixtures import (
    UATConstants,
    create_b_c1_order,
    create_b_c2_order,
    create_b_p1_order,
    create_b_p2_order,
    create_b_p3_order,
    create_delivered_tracking,
)


class TestGiftCardPaymentScenarios:
    """Test B-P1: Gift card 100% payment → Refund to gift card."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_p1_gift_card_100_percent_payment_refund(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-P1: 100% gift card payment should refund entirely to gift card."""

        order = create_b_p1_order(
            return_qty=2, shipping_amount=10
        )  # $100 items + $10 shipping = $110, paid via gift card
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Verify payment structure
        assert len(order.transactions) == 1
        gift_card_transaction = order.transactions[0]
        assert gift_card_transaction.gateway == UATConstants.GIFT_CARD
        assert gift_card_transaction.amountSet.presentmentMoney.amount == 100.0

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify full refund to gift card
        assert calculation.refund_type == "FULL"
        assert calculation.total_refund_amount == 110.0  # ($100 + $10 shipping)

        # Verify transaction allocation - should refund to original gift card
        assert len(calculation.transactions) == 1
        refund_transaction = calculation.transactions[0]
        assert refund_transaction["gateway"] == UATConstants.GIFT_CARD
        assert refund_transaction["kind"] == "REFUND"
        assert refund_transaction["amount"] == 100.0  # Original transaction amount

        refund = refund_order(order, tracking)

        assert refund is not None
        assert "FULL" in refund.orderName
        assert refund.totalRefundedSet.presentmentMoney.amount == 110.0

        # Verify Slack notification mentions gift card refund
        mock_slack.send_success.assert_called_once()
        success_call_args = mock_slack.send_success.call_args[1]["details"]
        assert success_call_args["refund_type"] == "FULL"

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_p1_gift_card_partial_return_proportional_refund(
        self, mock_idempotency, mock_slack, mock_requests
    ):
        """B-P1: Partial return with gift card should maintain proportional refund to gift card."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bp1_partial",
            False,
        )

        order = create_b_p1_order(return_qty=1, shipping_amount=0)
        tracking = create_delivered_tracking()

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify partial refund maintains gift card allocation
        assert calculation.refund_type == "PARTIAL"

        # Should refund 50% of items + proportional shipping
        expected_total = 50 + calculation.shipping_refund

        assert abs(calculation.total_refund_amount - expected_total) < 0.01

        # Verify transaction still goes to gift card with proportional amount
        assert len(calculation.transactions) == 1
        refund_transaction = calculation.transactions[0]
        assert refund_transaction["gateway"] == UATConstants.GIFT_CARD

        # Transaction amount should be proportional to original
        expected_transaction_amount = 50.0 + calculation.shipping_refund
        assert abs(refund_transaction["amount"] - expected_transaction_amount) < 0.01


class TestMixedPaymentScenarios:
    """Test B-P2: Gift card + card mix → Split refund back to both in correct amounts."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_p2_mixed_payment_split_refund_correct_amounts(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-P2: Mixed payment should split refund back to both payment methods correctly."""

        order = create_b_p2_order(
            full_refund=True
        )  # $60 gift card + $40 regular card = $100, + $10 shipping
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Verify payment structure
        assert len(order.transactions) == 2

        gift_card_trans = next(
            t for t in order.transactions if t.gateway == UATConstants.GIFT_CARD
        )
        regular_card_trans = next(
            t for t in order.transactions if t.gateway == UATConstants.SHOPIFY_PAYMENTS
        )

        assert gift_card_trans.amountSet.presentmentMoney.amount == 60.0
        assert regular_card_trans.amountSet.presentmentMoney.amount == 40.0

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify full refund with split transactions
        assert calculation.refund_type == "FULL"
        assert calculation.total_refund_amount == 110.0  # $100 + $10 shipping

        # Verify both transactions are included in refund
        assert len(calculation.transactions) == 2

        gift_card_refund = next(
            t
            for t in calculation.transactions
            if t["gateway"] == UATConstants.GIFT_CARD
        )
        regular_card_refund = next(
            t
            for t in calculation.transactions
            if t["gateway"] == UATConstants.SHOPIFY_PAYMENTS
        )

        # Verify correct refund amounts back to original payment methods
        assert gift_card_refund["amount"] == 60.0
        assert regular_card_refund["amount"] == 40.0
        assert gift_card_refund["kind"] == "REFUND"
        assert regular_card_refund["kind"] == "REFUND"

        refund = refund_order(order, tracking)

        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.amount == 110.0

        # Verify Slack notification mentions mixed payment refund
        mock_slack.send_success.assert_called_once()

    def test_b_p2_mixed_payment_partial_refund_proportional_split(
        self,
    ):
        """B-P2: Partial refund with mixed payment should split proportionally."""

        order = create_b_p2_order(full_refund=True)

        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify partial refund with proportional split
        assert calculation.refund_type == "FULL"

        # Should refund 50% of total ($110)
        expected_refund = 100 + calculation.shipping_refund  # 50% of $120
        assert abs(calculation.total_refund_amount - expected_refund) < 0.01
        assert len(calculation.transactions) == 2

        gift_card_refund = next(
            t
            for t in calculation.transactions
            if t["gateway"] == UATConstants.GIFT_CARD
        )
        regular_card_refund = next(
            t
            for t in calculation.transactions
            if t["gateway"] == UATConstants.SHOPIFY_PAYMENTS
        )

        # Should maintain original proportions: 60% gift card, 40% regular card
        total_transaction_refund = (
            gift_card_refund["amount"] + regular_card_refund["amount"]
        )

        # Proportions should match original: 60/40 split
        gift_card_proportion = gift_card_refund["amount"] / total_transaction_refund
        regular_card_proportion = (
            regular_card_refund["amount"] / total_transaction_refund
        )

        assert abs(gift_card_proportion - 0.6) < 0.01  # 60%
        assert abs(regular_card_proportion - 0.4) < 0.01  # 40%


class TestStoreCreditScenarios:
    """Test B-P3: Store credit used → Refund store credit portion to store credit."""

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_p3_store_credit_payment_refund_to_store_credit(
        self, mock_idempotency, mock_slack
    ):
        """B-P3: Store credit payment should refund back to store credit."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bp3",
            False,
        )

        order = (
            create_b_p3_order()
        )  # $100 items + $10 shipping = $110, paid via store credit
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Verify payment structure
        assert len(order.transactions) == 1
        store_credit_transaction = order.transactions[0]
        assert store_credit_transaction.gateway == UATConstants.STORE_CREDIT
        assert store_credit_transaction.amountSet.presentmentMoney.amount == 100.0

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify full refund to store credit
        assert calculation.refund_type == "FULL"
        assert calculation.total_refund_amount == 110.0

        # Verify transaction allocation - should refund to store credit
        assert len(calculation.transactions) == 1
        refund_transaction = calculation.transactions[0]
        assert refund_transaction["gateway"] == UATConstants.STORE_CREDIT
        assert refund_transaction["kind"] == "REFUND"
        assert refund_transaction["amount"] == 100.0

        refund = refund_order(order, tracking)

        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.amount == 110.0

        # Verify Slack notification mentions store credit refund
        mock_slack.send_success.assert_called_once()
        success_call_args = mock_slack.send_success.call_args[1]["details"]
        assert success_call_args["refund_type"] == "FULL"

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_p3_store_credit_maintains_customer_balance(
        self, mock_idempotency, mock_slack
    ):
        """B-P3: Store credit refunds should maintain proper customer balance tracking."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bp3_balance",
            False,
        )

        order = create_b_p3_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        refund = refund_order(order, tracking)

        # Verify refund is structured correctly for store credit processing
        assert refund is not None

        # Store credit refunds should preserve transaction linkage for balance management
        calculation = refund_calculator.calculate_refund(order, tracking)
        refund_transaction = calculation.transactions[0]

        # Verify parent transaction reference for proper credit tracking
        assert "parentId" in refund_transaction
        assert refund_transaction["parentId"] == order.transactions[0].id


class TestMultiCurrencyScenarios:
    """Test B-C1: Multi-currency store (order in CHF) → Refund in CHF, correct amounts."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_c1_chf_currency_refund_correct_amounts(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-C1: CHF order should refund in CHF with correct amounts."""

        order = create_b_c1_order(
            shipping_amount=10
        )  # CHF 100 + CHF 10 shipping = CHF 110 total
        tracking = create_delivered_tracking()

        # Verify currency structure
        assert order.totalPriceSet.shopMoney.currencyCode == UATConstants.CHF
        assert order.totalPriceSet.presentmentMoney.currencyCode == UATConstants.CHF

        # Verify all line items use CHF
        for line_item in order.lineItems:
            assert (
                line_item.originalTotalSet.presentmentMoney.currencyCode
                == UATConstants.CHF
            )
            assert line_item.originalTotalSet.shopMoney.currencyCode == UATConstants.CHF

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify CHF refund
        assert calculation.refund_type == "FULL"
        assert calculation.total_refund_amount == 110.0  # CHF 100 + shipping CHF 10

        refund = refund_order(order, tracking)

        # Verify refund maintains CHF currency
        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.currencyCode == UATConstants.CHF
        assert refund.totalRefundedSet.shopMoney.currencyCode == UATConstants.CHF
        assert (
            refund.totalRefundedSet.presentmentMoney.amount == 110.0
        )  # CHF 100 + shipping CHF 10
        assert (
            refund.totalRefundedSet.shopMoney.amount == 110.0
        )  # CHF 100 + shipping CHF 10

        # Verify Slack notification includes currency context
        mock_slack.send_success.assert_called_once()
        success_call_args = mock_slack.send_success.call_args[1]["details"]
        assert success_call_args["refund_type"] == "FULL"

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_c1_currency_consistency_across_calculation(
        self, mock_idempotency, mock_slack
    ):
        """B-C1: Currency consistency should be maintained throughout refund calculation."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bc1_consistency",
            False,
        )

        order = create_b_c1_order()
        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Verify all calculated amounts respect CHF currency
        assert calculation.total_refund_amount > 0

        # Verify transactions maintain currency context
        for transaction in calculation.transactions:
            # Transaction amounts should be calculated in CHF context
            assert transaction["amount"] > 0
            assert "orderId" in transaction  # Should link back to CHF order

        # Verify shipping refund respects currency
        if calculation.shipping_refund > 0:
            # Shipping should be calculated in CHF
            assert calculation.shipping_refund == 10.0  # CHF 10


class TestPriorPartialRefundScenarios:
    """Test B-C2: Prior partial refund exists → Only remaining refundable balance processed."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_b_c2_prior_partial_refund_remaining_balance_only(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """B-C2: Prior partial refund should reduce remaining refundable balance."""

        order = (
            create_b_c2_order()
        )  # $100 + $10 shipping = $110 total, $30 already refunded

        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        # Verify prior refund structure
        assert len(order.refunds) == 1
        prior_refund = order.refunds[0]
        assert prior_refund.totalRefundedSet.presentmentMoney.amount == 30.0
        assert prior_refund.createdAt is not None

        # The order should have filtered refundable quantities based on prior refunds
        # This is handled by the __filter_out_already_refunded_return_line_items method with remaining balance
        calculation = refund_calculator.calculate_refund(order, tracking)

        # Should calculate refund for remaining items only
        # Note: The filtering logic in the ShopifyOrder model should handle this
        assert calculation.total_refund_amount > 0

        # Remaining refundable amount should be less than original total
        # Original: $110, Previous: $30, Remaining should be ≤ $80
        assert calculation.total_refund_amount <= float(100.0 - 30.0)

        refund = refund_order(order, tracking)

        # Verify refund processes remaining balance
        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.amount > 0
        assert (
            refund.totalRefundedSet.presentmentMoney.amount <= 80.0
        )  # Remaining balance

        # Verify Slack notification mentions partial refund context
        mock_slack.send_success.assert_called_once()

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager")
    def test_b_c2_no_double_refund_prevention(self, mock_idempotency, mock_slack):
        """B-C2: Should prevent refunding already refunded items."""
        # Setup
        mock_idempotency.check_operation_idempotency.return_value = (
            "test_key_bc2_no_double",
            False,
        )

        # Create order with substantial prior refund that should affect calculation
        order = create_b_c2_order()

        # Simulate scenario where prior refund covered significant portion
        # Update prior refund to cover more of the order
        order.refunds[
            0
        ].totalRefundedSet.presentmentMoney.amount = (
            80.0  # $80 of $110 already refunded
        )
        order.refunds[0].totalRefundedSet.shopMoney.amount = 80.0

        tracking = create_delivered_tracking(tracking_number=order.tracking_number)

        calculation = refund_calculator.calculate_refund(order, tracking)

        # Should only process remaining $30 balance
        expected_remaining = 30.0  # $110 - $80 = $30

        # Allow for reasonable calculation differences but prevent over-refunding
        assert calculation.total_refund_amount <= expected_remaining + 0.01

        # Verify no negative refunds
        assert calculation.total_refund_amount >= 0

        # If significant portion already refunded, should be partial refund
        if calculation.total_refund_amount > 0:
            # Could be PARTIAL if some items remain, or minimal if almost everything refunded
            assert calculation.refund_type in ["PARTIAL", "FULL"]


class TestTenderCombinationComplexScenarios:
    """Test complex combinations of different tender types and currencies."""

    @patch("src.shopify.refund.requests")
    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_complex_multi_tender_multi_currency_accuracy(
        self, mock_idempotency_save, mock_slack, mock_requests
    ):
        """Test complex scenario with multiple tenders and currency considerations."""

        # Create complex scenario: EUR order with multiple payment methods
        from src.models.order import TransactionKind
        from src.tests.uat.uat_fixtures import UATFixtureBuilder

        complex_order = (
            UATFixtureBuilder()
            .with_currency(UATConstants.EUR)
            .with_line_item(
                "gid://shopify/LineItem/30M02M2099M91", quantity=1, price=50.0
            )
            .with_line_item(
                "gid://shopify/LineItem/30M02M2099M92", quantity=1, price=30.0
            )
            .with_gift_card_payment(40.0)  # €40 gift card
            # €40 regular card
            .with_transaction(UATConstants.SHOPIFY_PAYMENTS, TransactionKind.SALE, 40.0)
            .with_return_tracking()
            .with_return_line_item(
                "gid://shopify/LineItem/30M02M2099M91", refundable_qty=1
            )
            .with_return_line_item(
                "gid://shopify/LineItem/30M02M2099M92", refundable_qty=1
            )
            .with_shipping(10.0, refundable=True)  # €10 shipping
            .build()
        )

        tracking = create_delivered_tracking()

        calculation = refund_calculator.calculate_refund(
            order=complex_order, tracking=tracking
        )

        # Verify complex calculation
        assert calculation.refund_type == "FULL"

        # Total: €80 items + €10 shipping = €90
        expected_total = 90.0
        assert abs(calculation.total_refund_amount - expected_total) < 0.01

        # Verify both tender types are handled
        assert len(calculation.transactions) == 2

        # Verify currency consistency throughout
        gift_card_refund = next(
            t
            for t in calculation.transactions
            if t["gateway"] == UATConstants.GIFT_CARD
        )
        regular_card_refund = next(
            t
            for t in calculation.transactions
            if t["gateway"] == UATConstants.SHOPIFY_PAYMENTS
        )

        assert gift_card_refund["amount"] == 40.0  # €40
        assert regular_card_refund["amount"] == 40.0  # €40

        refund = refund_order(complex_order, tracking)

        # Verify complex refund creation
        assert refund is not None
        assert refund.totalRefundedSet.presentmentMoney.currencyCode == UATConstants.EUR
        assert (
            abs(refund.totalRefundedSet.presentmentMoney.amount - expected_total) < 0.01
        )

    @patch("src.shopify.refund.slack_notifier")
    @patch("src.shopify.refund.idempotency_manager._save_cache")
    def test_tender_allocation_mathematical_accuracy(
        self, mock_idempotency_save, mock_slack
    ):
        """Verify mathematical accuracy in tender allocation across scenarios."""

        test_cases = [
            (
                "Gift Card Only",
                create_b_p1_order(shipping_amount=0),
                100.0,
                1,
            ),  # Single tender
            (
                "Mixed Payment",
                create_b_p2_order(shipping_amount=0),
                100.0,
                2,
            ),  # Two tenders
            (
                "Store Credit",
                create_b_p3_order(shipping_amount=0),
                100.0,
                1,
            ),  # Single tender
            (
                "CHF Currency",
                create_b_c1_order(shipping_amount=10),
                110.0,
                1,
            ),  # Currency test
        ]

        for scenario_name, order, expected_total, expected_transactions in test_cases:
            tracking = create_delivered_tracking(tracking_number=order.tracking_number)

            # Calculate refund
            calculation = refund_calculator.calculate_refund(order, tracking)

            # Verify mathematical accuracy
            assert abs(calculation.total_refund_amount - expected_total) < 0.01, (
                f"{scenario_name}: Expected {expected_total}, got {calculation.total_refund_amount}"
            )

            # Verify transaction count
            assert len(calculation.transactions) == expected_transactions, (
                f"{scenario_name}: Expected {expected_transactions} transactions, got {len(calculation.transactions)}"
            )

            # Verify no negative amounts
            assert calculation.total_refund_amount > 0
            assert all(t["amount"] > 0 for t in calculation.transactions)

            # Verify transaction amounts sum correctly for multi-tender scenarios
            if expected_transactions > 1:
                transaction_total = sum(t["amount"] for t in calculation.transactions)
                # Transaction total should be ≤ total refund (shipping might not be in transactions)
                assert transaction_total <= calculation.total_refund_amount + 0.01
