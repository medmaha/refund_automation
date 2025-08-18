class UATConstants:
    """Constants for UAT testing."""

    # Currencies
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CHF = "CHF"

    # Payment Gateways
    SHOPIFY_PAYMENTS = "shopify_payments"
    GIFT_CARD = "gift_card"
    STORE_CREDIT = "store_credit"
    PAYPAL = "paypal"

    # Discount Types
    ORDER_LEVEL_PERCENTAGE = "order_percentage"
    LINE_LEVEL_FIXED = "line_fixed"

    # Shipping Policies
    SHIPPING_REFUNDABLE = True
    SHIPPING_NON_REFUNDABLE = False

    # Tax Rates
    VAT_RATE = 0.15  # 15% VAT
    SALES_TAX_RATE = 0.08  # 8% sales tax

    # Timing
    DELIVERY_DELAY_DAYS = 5
    HOURS_IN_DAY = 24

    # Carrier
    CARRIER_CODE = 4971

    # Tracking
    TRACKING_NUMBER = "UAT1373893300203"
