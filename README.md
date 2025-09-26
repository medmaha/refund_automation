# Refund Automation System

Automatically processes Shopify refunds when returned packages are delivered.

## Setup

1. Install dependencies:
   ```bash
   uv sync
   ```

2. Create `.env` file:
   ```env
   TRACKING_API_KEY=your_17track_api_key
   SHOPIFY_STORE_URL=your-store-name
   SHOPIFY_ACCESS_TOKEN=your_shopify_access_token
   ```

3. Run:
   ```bash
   uv run main.py
   ```

## How it works

1. **Finds orders** - Gets Shopify orders with returns in progress
2. **Tracks packages** - Monitors return shipments via 17TRACK API  
3. **Processes refunds** - Creates refunds when packages are delivered
4. **Runs automatically** - GitHub Actions runs every 4 hours

## Features

- Automatic refund processing
- Package tracking integration
- Comprehensive logging
- Error handling and retries
- Dry-run mode for testing
