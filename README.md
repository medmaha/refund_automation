# Refund Automation System

A robust, production-ready refund automation system for Shopify with comprehensive safety features, audit logging, and dual-mode operation.

## 🎯 Requirements Implementation

This implementation fulfills all the specified requirements:

### ✅ DRY-RUN Toggle
- **`DRY_RUN=true`**: Performs all reads and Slack alerts but makes no Shopify mutations
- **`DRY_RUN=false`**: Makes actual Shopify API calls and processes real refunds  
- Every test passes in both modes with identical logic and safety checks

### ✅ Idempotency
- Prevents double marking and double refunds using SHA-256 hashed keys
- Persistent cache with configurable TTL (24h default)
- Rerunning with same inputs produces identical outcomes
- Works consistently in both DRY-RUN and LIVE modes

### ✅ Time & Timezone Management  
- All time comparisons use configurable store timezone
- ISO8601 timestamps with timezone info in all logs
- Proper timezone conversion and handling throughout

### ✅ Rate Limiting & Retries
- Exponential backoff with jitter for API calls
- Configurable retry limits (3 default) and delays
- Failed requests escalate to Slack with unique request IDs
- Comprehensive error handling and recovery

### ✅ Comprehensive Auditability
Every decision is logged with:
- Order ID, amounts & currency
- External references (tracking numbers, etc.)
- API statuses and response times
- Decision branch (matched/unmatched/skipped/processed/failed)
- Idempotency keys for tracking
- Request IDs for debugging
- Timestamps in store timezone

## 🎯 Key Features

- **📦 Automated Return Tracking** - Monitors return shipments via 17TRACK API
- **✅ Delivery Verification** - Confirms when returned items reach the merchant
- **💰 Intelligent Refund Processing** - Automatically processes refunds for verified returns
- **🔍 GraphQL Integration** - Efficient data retrieval from Shopify using GraphQL
- **📋 Comprehensive Logging** - Detailed audit trail of all operations
- **🚀 GitHub Actions Automation** - Scheduled execution every 4 hours
- **🛡️ Robust Error Handling** - Graceful handling of API failures and edge cases

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Shopify API   │◄──►│  Refund Engine   │◄──►│   17TRACK API   │
│   (GraphQL)     │    │                  │    │  (REST API)     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ Order & Return  │    │   Processing     │    │   Tracking      │
│   Management    │    │     Logic        │    │   Validation    │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- Shopify Store with Admin API access
- 17TRACK API account
- UV package manager (recommended)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/refund-automation.git
   cd refund-automation
   ```

2. **Install dependencies:**
   ```bash
   # Using UV (recommended)
   uv sync
   
   # Or using pip
   pip install -r requirements.txt
   ```

3. **Environment Configuration:**
   Create a `.env` file in the project root:
   ```env
   # 17TRACK Configuration
   TRACKING_API_KEY=your_17track_api_key
   TRACKING_API_URL=https://api.17track.net/track/v2.2
   
   # Shopify Configuration
   SHOPIFY_STORE_URL=your-store-name
   SHOPIFY_ACCESS_TOKEN=your_shopify_access_token
   
   # Optional: Logging Configuration
   LOG_LEVEL=INFO
   ```

4. **Run the automation:**
   ```bash
   # Using UV
   uv run main.py
   
   # Or using Python directly
   python main.py
   ```

## 📁 Project Structure

```
refund-automation/
├── src/
│   ├── config.py              # Configuration management
│   ├── logger.py              # Logging utilities
│   ├── models/                # Data models
│   │   ├── event.py          # Webhook event models
│   │   ├── order.py          # Shopify order models
│   │   └── tracking.py       # 17TRACK tracking models
│   ├── monitor/               # Monitoring & webhooks
│   │   └── webhook.py        # 17TRACK webhook handler
│   └── shopify/              # Shopify integration
│       ├── graph_ql_queries.py  # GraphQL query definitions
│       ├── orders.py            # Order retrieval & processing
│       └── refund.py            # Refund processing logic
├── src/tests/                # Test suite
├── .github/workflows/        # GitHub Actions automation
├── .logs/                    # Application logs
├── main.py                   # Application entry point
├── pyproject.toml           # Project configuration
└── README.md                # This file
```

## 🔧 How It Works

### 1. Order Discovery
- Queries Shopify using GraphQL for orders with `return_status:IN_PROGRESS` and `financial_status:PAID`
- Filters orders that have valid return shipments with tracking information
- Processes orders in batches with pagination support

### 2. Tracking Validation
- Extracts return tracking numbers from Shopify order data
- Registers tracking numbers with 17TRACK API
- Monitors delivery status and confirms when packages reach merchant

### 3. Refund Processing
- Validates that returned items have `status: DELIVERED` and `sub_status: DELIVERED_OTHER`
- Automatically calculates refund amounts based on original transactions
- Creates refunds via Shopify GraphQL API with comprehensive error handling

### 4. Automation & Scheduling
- GitHub Actions workflow runs every 4 hours (`0 0 */4 * *`)
- Manual execution available via workflow dispatch
- Comprehensive logging for audit and debugging

## 🛠️ Configuration

### Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|----------|
| `TRACKING_API_KEY` | ✅ | 17TRACK API authentication key | `your-17track-key` |
| `TRACKING_API_URL` | ✅ | 17TRACK API base URL | `https://api.17track.net/track/v2.2` |
| `SHOPIFY_STORE_URL` | ✅ | Your Shopify store name | `my-store` |
| `SHOPIFY_ACCESS_TOKEN` | ✅ | Shopify access token with admin permissions | `shpat_xxxx` |
| `LOG_LEVEL` | ❌ | Logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |

### Shopify Permissions Required

Your Shopify app needs these permissions:
- `read_orders` - To fetch order information
- `write_orders` - To create refunds
- `read_fulfillments` - To access fulfillment data
- `read_returns` - To access return information

## 🧪 Testing

Run the comprehensive test suite:

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest src/tests/shopify/test_refund.py

# Run with coverage
uv run pytest --cov=src
```

### Test Coverage
- ✅ **Order Processing**: 9 tests covering order retrieval and parsing
- ✅ **Refund Logic**: 8 tests for refund creation and validation
- ✅ **Data Parsing**: 3 focused tests for GraphQL data transformation
- ✅ **Error Handling**: Comprehensive edge case coverage

## 📊 Monitoring & Logging

The system provides detailed logging at multiple levels:

- **INFO**: Normal operation flow, successful refunds
- **WARNING**: Non-critical issues, orders without tracking data
- **ERROR**: API failures, refund creation errors
- **DEBUG**: Detailed execution flow (set `LOG_LEVEL=DEBUG`)

Logs are stored in `.logs/log_YYYY-MM-DD.log` files with automatic daily rotation.

## 🔄 GitHub Actions Automation

The included workflow (`.github/workflows/automate_refund.yml`) provides:

- **Scheduled Execution**: Runs every 4 hours automatically
- **Manual Triggers**: Run on-demand via GitHub UI
- **Secure Configuration**: Uses GitHub Secrets for API keys
- **Error Notifications**: Workflow failures are visible in GitHub

### Setting Up GitHub Secrets

Add these secrets to your GitHub repository:

1. Go to `Settings` → `Secrets and variables` → `Actions`
2. Add the following secrets:
   - `TRACKING_API_KEY`
   - `TRACKING_API_URL`
   - `SHOPIFY_STORE_URL`
   - `SHOPIFY_ACCESS_TOKEN`

## 🚨 Error Handling

The system handles various error scenarios:

- **API Rate Limits**: Implements proper timeout and retry logic
- **Invalid Tracking Data**: Gracefully skips malformed tracking information
- **Network Issues**: Robust error handling with detailed logging
- **Shopify API Errors**: Parses and logs specific error messages
- **Data Validation**: Pydantic models ensure data integrity

## 🔮 Webhook Support (Future)

The system includes webhook infrastructure for real-time processing:

```python
# Example webhook handler (implemented but not yet integrated)
from src.monitor.webhook import handle_17track_webhook

# Handles real-time tracking updates from 17TRACK
# Allows for immediate refund processing when packages are delivered
```


## Development Guidelines

- Follow Python PEP 8 style guide
- Add tests for new functionality
- Update documentation for significant changes
- Use type hints for better code clarity

## 📋 Dependencies

### Core Dependencies
- **FastAPI** - Web framework (for future webhook endpoints)
- **Requests** - HTTP client for API calls
- **Pydantic** - Data validation and serialization
- **python-dotenv** - Environment variable management
- **SQLAlchemy** - Database ORM (for future data persistence)

## 🙋‍♂️ Support

If you encounter any issues or have questions:

1. Check the [logs](#-monitoring--logging) for error details
2. Review the [troubleshooting section](#-error-handling)
3. Open an issue on GitHub with detailed error information
4. Include relevant log entries and configuration (without sensitive data)

---

**Made with ❤️ by Primeforge West Ltd**
