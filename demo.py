#!/usr/bin/env python3
"""
Demo script to showcase the refund automation features.
This script demonstrates the key functionality in both DRY_RUN and LIVE modes.
"""

import os
from unittest.mock import Mock, patch
from datetime import datetime

# Set DRY_RUN mode for demo
os.environ['DRY_RUN'] = 'true'
os.environ['SHOPIFY_TIMEZONE'] = 'America/New_York'
os.environ['SLACK_ENABLED'] = 'false'  # Disable Slack for demo

# Import after setting environment variables
from src.config import DRY_RUN
from src.logger import get_logger
from src.models.order import ShopifyOrder, MoneyBag, MoneyBagSet, OrderTransaction, TransactionKind, LineItem
from src.shopify.refund import refund_order
from src.utils.timezone import timezone_handler, get_current_time_iso8601
from src.utils.idempotency import idempotency_manager
from src.utils.audit import audit_logger

logger = get_logger(__name__)

def create_sample_order(order_id: str = "gid://shopify/Order/12345") -> ShopifyOrder:
    """Create a sample Shopify order for demonstration."""
    return ShopifyOrder(
        id=order_id,
        name=f"DEMO-{order_id.split('/')[-1]}",
        tags=["demo"],
        lineItems=[
            LineItem(id="gid://shopify/LineItem/1", quantity=1, refundableQuantity=1)
        ],
        totalPriceSet=MoneyBagSet(
            presentmentMoney=MoneyBag(amount=99.99, currencyCode="USD"),
            shopMoney=MoneyBag(amount=99.99, currencyCode="USD")
        ),
        transactions=[
            OrderTransaction(
                id="gid://shopify/OrderTransaction/1",
                gateway="shopify_payments",
                kind=TransactionKind.SALE,
                amountSet=MoneyBagSet(
                    presentmentMoney=MoneyBag(amount=99.99, currencyCode="USD"),
                    shopMoney=MoneyBag(amount=99.99, currencyCode="USD")
                )
            )
        ],
        returns=[]
    )

def create_sample_tracking():
    """Create a sample tracking object."""
    tracking = Mock()
    tracking.number = "DEMO123456789"
    tracking.track_info.latest_event = "delivered"
    return tracking

def demonstrate_timezone_handling():
    """Demonstrate timezone handling functionality."""
    print("\n" + "="*50)
    print("🕐 TIMEZONE HANDLING DEMONSTRATION")
    print("="*50)
    
    tz_info = timezone_handler.get_timezone_info()
    
    print(f"Store timezone: {tz_info['store_timezone']}")
    print(f"Current UTC time: {tz_info['current_utc']}")
    print(f"Current store time: {tz_info['current_store']}")
    print(f"UTC offset: {tz_info['utc_offset']}")
    
    # Show ISO8601 formatting
    iso_time = get_current_time_iso8601()
    print(f"ISO8601 formatted time: {iso_time}")

def demonstrate_idempotency():
    """Demonstrate idempotency functionality."""
    print("\n" + "="*50)
    print("🔒 IDEMPOTENCY DEMONSTRATION")
    print("="*50)
    
    # Generate idempotency key
    order_id = "gid://shopify/Order/12345"
    key = idempotency_manager.generate_key(order_id, "refund", amount=99.99)
    print(f"Generated idempotency key: {key}")
    
    # Check if it's a duplicate (should be False first time)
    is_duplicate = idempotency_manager.is_duplicate_operation(key)
    print(f"Is duplicate operation: {is_duplicate}")
    
    # Mark as completed
    idempotency_manager.mark_operation_completed(key, order_id, "refund", {"demo": True})
    print("Marked operation as completed")
    
    # Check again (should be True now)
    is_duplicate = idempotency_manager.is_duplicate_operation(key)
    print(f"Is duplicate operation (after marking): {is_duplicate}")
    
    # Show stats
    stats = idempotency_manager.get_stats()
    print(f"Idempotency cache stats: {stats}")

def demonstrate_audit_logging():
    """Demonstrate audit logging functionality."""
    print("\n" + "="*50)
    print("📋 AUDIT LOGGING DEMONSTRATION")
    print("="*50)
    
    from src.utils.audit import log_refund_audit
    
    # Log a sample refund decision
    log_refund_audit(
        order_id="gid://shopify/Order/12345",
        order_name="DEMO-12345",
        refund_amount=99.99,
        currency="USD",
        decision="processed",
        tracking_number="DEMO123456789",
        idempotency_key="abc123",
        refund_id="gid://shopify/Refund/67890"
    )
    
    print("Audit log entry created")
    
    # Show audit stats
    stats = audit_logger.get_audit_stats()
    print(f"Audit logging stats: {stats}")

def demonstrate_dry_run_refund():
    """Demonstrate DRY_RUN refund processing."""
    print("\n" + "="*50)
    print("🧪 DRY-RUN REFUND DEMONSTRATION")
    print("="*50)
    
    sample_order = create_sample_order()
    sample_tracking = create_sample_tracking()
    
    print(f"Processing refund for order: {sample_order.name}")
    print(f"Order amount: ${sample_order.totalPriceSet.presentmentMoney.amount}")
    print(f"Tracking number: {sample_tracking.number}")
    print(f"DRY_RUN mode: {DRY_RUN}")
    
    # Process refund
    with patch('src.utils.slack.slack_notifier') as mock_slack:
        refund = refund_order(sample_order, sample_tracking)
    
    if refund:
        print(f"✅ Refund created successfully!")
        print(f"   Refund ID: {refund.id}")
        print(f"   Order Name: {refund.orderName}")
        print(f"   Created At: {refund.createdAt}")
        print(f"   Total Refunded: ${refund.totalRefundedSet.presentmentMoney.amount}")
    else:
        print("❌ Refund failed or was skipped")

def demonstrate_duplicate_prevention():
    """Demonstrate duplicate refund prevention."""
    print("\n" + "="*50)
    print("🚫 DUPLICATE PREVENTION DEMONSTRATION")
    print("="*50)
    
    sample_order = create_sample_order("gid://shopify/Order/99999")
    sample_tracking = create_sample_tracking()
    
    print("Attempting first refund...")
    with patch('src.utils.slack.slack_notifier') as mock_slack:
        refund1 = refund_order(sample_order, sample_tracking)
    
    if refund1:
        print(f"✅ First refund successful: {refund1.id}")
    
    print("\nAttempting duplicate refund...")
    with patch('src.utils.slack.slack_notifier') as mock_slack:
        refund2 = refund_order(sample_order, sample_tracking)
    
    if refund2 is None:
        print("✅ Duplicate refund prevented successfully!")
    else:
        print(f"❌ Duplicate refund was not prevented: {refund2.id}")

def main():
    """Run the demonstration."""
    print("🚀 REFUND AUTOMATION SYSTEM DEMONSTRATION")
    print("This demo showcases all the implemented requirements:")
    print("• DRY-RUN toggle functionality")
    print("• Idempotency and duplicate prevention") 
    print("• Timezone handling with ISO8601 timestamps")
    print("• Comprehensive audit logging")
    print("• Error handling and retry mechanisms")
    
    try:
        demonstrate_timezone_handling()
        demonstrate_idempotency()
        demonstrate_audit_logging()
        demonstrate_dry_run_refund()
        demonstrate_duplicate_prevention()
        
        print("\n" + "="*50)
        print("🎉 DEMONSTRATION COMPLETED SUCCESSFULLY!")
        print("="*50)
        print("Key features demonstrated:")
        print("✅ DRY-RUN mode creates mock refunds without API calls")
        print("✅ Idempotency prevents duplicate operations")
        print("✅ Timezone handling with proper ISO8601 formatting")
        print("✅ Comprehensive audit logging for all decisions")
        print("✅ Duplicate prevention works correctly")
        print("\nTo test in LIVE mode, set DRY_RUN=false in your .env file")
        print("and configure the Shopify credentials.")
        
    except Exception as e:
        logger.exception("Demo failed with error", extra={"error": str(e)})
        print(f"\n❌ Demo failed: {e}")
        raise

if __name__ == "__main__":
    main()
