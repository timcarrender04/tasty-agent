# Multi-Tenant Usage Guide

The TastyTrade HTTP API supports multiple tenants, where each tenant uses a different API key that maps to different TastyTrade credentials.

## How Multi-Tenant Works

1. **Each tenant has a unique API key** - This is the identifier for the tenant
2. **Each API key maps to different TastyTrade credentials** - Different `client_secret` and `refresh_token`
3. **Each tenant can have different account IDs** - Different TastyTrade accounts
4. **Complete isolation** - Each tenant's sessions and data are separate

## Adding Multiple Tenants

### Step 1: Add Credentials for Tenant 1

```bash
curl -X POST https://tasty.gammabox.app/api/v1/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "tenant1_key",
    "client_secret": "tenant1_client_secret",
    "refresh_token": "tenant1_refresh_token"
  }'
```

### Step 2: Add Credentials for Tenant 2

```bash
curl -X POST https://tasty.gammabox.app/api/v1/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "tenant2_key",
    "client_secret": "tenant2_client_secret",
    "refresh_token": "tenant2_refresh_token"
  }'
```

### Step 3: List All Tenants

```bash
curl https://tasty.gammabox.app/api/v1/credentials
```

Response:
```json
{
  "api_keys": [
    {"api_key": "tenant1_key", "configured": true},
    {"api_key": "tenant2_key", "configured": true}
  ],
  "count": 2
}
```

## Using Different Tenants with curl

### Tenant 1 Requests

```bash
# Get balances for Tenant 1
curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=TENANT1_ACCOUNT_ID" \
  -H "X-API-Key: tenant1_key"

# Get positions for Tenant 1
curl -X GET "https://tasty.gammabox.app/api/v1/positions?account_id=TENANT1_ACCOUNT_ID" \
  -H "X-API-Key: tenant1_key"

# Get live orders for Tenant 1
curl -X GET "https://tasty.gammabox.app/api/v1/live-orders?account_id=TENANT1_ACCOUNT_ID" \
  -H "X-API-Key: tenant1_key"
```

### Tenant 2 Requests

```bash
# Get balances for Tenant 2
curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=TENANT2_ACCOUNT_ID" \
  -H "X-API-Key: tenant2_key"

# Get positions for Tenant 2
curl -X GET "https://tasty.gammabox.app/api/v1/positions?account_id=TENANT2_ACCOUNT_ID" \
  -H "X-API-Key: tenant2_key"

# Get live orders for Tenant 2
curl -X GET "https://tasty.gammabox.app/api/v1/live-orders?account_id=TENANT2_ACCOUNT_ID" \
  -H "X-API-Key: tenant2_key"
```

## Important Notes

1. **API Key is Required**: Every request must include the `X-API-Key` header
2. **Account ID Must Match**: The `account_id` must be a valid account for that tenant's TastyTrade credentials
3. **Isolation**: Each tenant's data is completely isolated - Tenant 1 cannot access Tenant 2's data
4. **Sessions**: Each tenant maintains separate authentication sessions

## Example: Multiple Tenants in One Script

```bash
#!/bin/bash

BASE_URL="https://tasty.gammabox.app"

# Tenant 1
TENANT1_KEY="tenant1_key"
TENANT1_ACCOUNT="ACCOUNT1"

echo "=== Tenant 1 ==="
curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${TENANT1_ACCOUNT}" \
  -H "X-API-Key: ${TENANT1_KEY}" | python3 -m json.tool

# Tenant 2
TENANT2_KEY="tenant2_key"
TENANT2_ACCOUNT="ACCOUNT2"

echo "=== Tenant 2 ==="
curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${TENANT2_ACCOUNT}" \
  -H "X-API-Key: ${TENANT2_KEY}" | python3 -m json.tool
```

## Credentials Storage

All credentials are stored in `/home/ert/projects/infrastructure/tasty-agent/credentials.json`:

```json
{
  "tenant1_key": {
    "client_secret": "tenant1_client_secret",
    "refresh_token": "tenant1_refresh_token"
  },
  "tenant2_key": {
    "client_secret": "tenant2_client_secret",
    "refresh_token": "tenant2_refresh_token"
  }
}
```

## Query Operations (Market Data)

### Get Live Quotes (Stocks and Options)

**Tenant 1:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/quotes" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '{
    "instruments": [
      {"symbol": "SPY"},
      {"symbol": "AAPL"},
      {
        "symbol": "SPY",
        "option_type": "C",
        "strike_price": 500,
        "expiration_date": "2025-12-26"
      }
    ],
    "timeout": 10.0
  }'
```

**Tenant 2:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/quotes" \
  -H "X-API-Key: tenant2_key" \
  -H "Content-Type: application/json" \
  -d '{
    "instruments": [
      {"symbol": "QQQ"},
      {"symbol": "TSLA"}
    ]
  }'
```

### Get Greeks for Options

**Tenant 1:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/greeks" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '{
    "options": [
      {
        "symbol": "SPY",
        "option_type": "C",
        "strike_price": 500,
        "expiration_date": "2025-12-26"
      },
      {
        "symbol": "SPY",
        "option_type": "P",
        "strike_price": 495,
        "expiration_date": "2025-12-26"
      }
    ],
    "timeout": 10.0
  }'
```

### Get Market Metrics

**Tenant 1:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/market-metrics" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '["SPY", "AAPL", "QQQ"]'
```

### Get Market Status

**Tenant 1:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/market-status?exchanges=Equity" \
  -H "X-API-Key: tenant1_key"
```

**Tenant 2:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/market-status" \
  -H "X-API-Key: tenant2_key"
```

### Search Symbols

**Tenant 1:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/search-symbols?symbol=SPY" \
  -H "X-API-Key: tenant1_key"
```

## Trading Operations

### Place Order (Multi-Leg Options/Equity)

**Tenant 1 - Single Leg Stock Order:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=TENANT1_ACCOUNT_ID&time_in_force=Day&dry_run=false" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '{
    "legs": [
      {
        "symbol": "SPY",
        "action": "Buy",
        "quantity": 10
      }
    ],
    "price": 500.00
  }'
```

**Tenant 1 - Single Leg Option Order:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=TENANT1_ACCOUNT_ID&time_in_force=Day&dry_run=false" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '{
    "legs": [
      {
        "symbol": "SPY",
        "action": "Buy to Open",
        "quantity": 1,
        "option_type": "C",
        "strike_price": 500,
        "expiration_date": "2025-12-26"
      }
    ],
    "price": 5.50
  }'
```

**Tenant 1 - Multi-Leg Spread (Auto-calculated price):**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=TENANT1_ACCOUNT_ID&time_in_force=Day&dry_run=false" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '{
    "legs": [
      {
        "symbol": "SPY",
        "action": "Buy to Open",
        "quantity": 1,
        "option_type": "C",
        "strike_price": 500,
        "expiration_date": "2025-12-26"
      },
      {
        "symbol": "SPY",
        "action": "Sell to Open",
        "quantity": 1,
        "option_type": "C",
        "strike_price": 505,
        "expiration_date": "2025-12-26"
      }
    ]
  }'
```

**Tenant 2 - Different Account:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=TENANT2_ACCOUNT_ID&time_in_force=GTC&dry_run=true" \
  -H "X-API-Key: tenant2_key" \
  -H "Content-Type: application/json" \
  -d '{
    "legs": [
      {
        "symbol": "QQQ",
        "action": "Buy",
        "quantity": 5
      }
    ],
    "price": 400.00
  }'
```

### Replace Order (Modify Price)

**Tenant 1:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/replace-order/428400056?account_id=TENANT1_ACCOUNT_ID" \
  -H "X-API-Key: tenant1_key" \
  -H "Content-Type: application/json" \
  -d '{
    "price": 5.75
  }'
```

**Tenant 2:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/replace-order/ORDER_ID?account_id=TENANT2_ACCOUNT_ID" \
  -H "X-API-Key: tenant2_key" \
  -H "Content-Type: application/json" \
  -d '{
    "price": 6.00
  }'
```

### Cancel Order

**Tenant 1:**
```bash
curl -X DELETE "https://tasty.gammabox.app/api/v1/orders/428400056?account_id=TENANT1_ACCOUNT_ID" \
  -H "X-API-Key: tenant1_key"
```

**Tenant 2:**
```bash
curl -X DELETE "https://tasty.gammabox.app/api/v1/orders/ORDER_ID?account_id=TENANT2_ACCOUNT_ID" \
  -H "X-API-Key: tenant2_key"
```

### Get Live Orders

**Tenant 1:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/live-orders?account_id=TENANT1_ACCOUNT_ID" \
  -H "X-API-Key: tenant1_key"
```

**Tenant 2:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/live-orders?account_id=TENANT2_ACCOUNT_ID" \
  -H "X-API-Key: tenant2_key"
```

### Get Order History

**Tenant 1:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/order-history?account_id=TENANT1_ACCOUNT_ID&days=7&underlying_symbol=SPY" \
  -H "X-API-Key: tenant1_key"
```

**Tenant 2:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/order-history?account_id=TENANT2_ACCOUNT_ID&days=30" \
  -H "X-API-Key: tenant2_key"
```

## Complete Multi-Tenant Trading Example

```bash
#!/bin/bash

BASE_URL="https://tasty.gammabox.app"

# Tenant 1 Configuration
TENANT1_KEY="tenant1_key"
TENANT1_ACCOUNT="ACCOUNT1"

# Tenant 2 Configuration
TENANT2_KEY="tenant2_key"
TENANT2_ACCOUNT="ACCOUNT2"

echo "=== Tenant 1: Get Quotes ==="
curl -s -X POST "${BASE_URL}/api/v1/quotes" \
  -H "X-API-Key: ${TENANT1_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"instruments": [{"symbol": "SPY"}]}' | python3 -m json.tool

echo ""
echo "=== Tenant 2: Get Quotes ==="
curl -s -X POST "${BASE_URL}/api/v1/quotes" \
  -H "X-API-Key: ${TENANT2_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"instruments": [{"symbol": "QQQ"}]}' | python3 -m json.tool

echo ""
echo "=== Tenant 1: Place Order (Dry Run) ==="
curl -s -X POST "${BASE_URL}/api/v1/place-order?account_id=${TENANT1_ACCOUNT}&dry_run=true" \
  -H "X-API-Key: ${TENANT1_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "legs": [{
      "symbol": "SPY",
      "action": "Buy",
      "quantity": 1
    }],
    "price": 500.00
  }' | python3 -m json.tool

echo ""
echo "=== Tenant 2: Get Balances ==="
curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${TENANT2_ACCOUNT}" \
  -H "X-API-Key: ${TENANT2_KEY}" | python3 -m json.tool
```

## Security Considerations

- Each tenant's credentials are stored securely in the credentials.json file
- API keys act as the authentication mechanism
- Each tenant can only access their own account data
- Sessions are automatically managed and refreshed per tenant
- All trading operations require both API key authentication and valid account_id
- Use `dry_run=true` for testing orders before placing real trades

