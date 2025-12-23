# TastyTrade HTTP API - External Application Integration Guide

Complete guide for connecting external applications to the TastyTrade HTTP API server.

## Table of Contents

1. [Authentication](#authentication)
2. [Base URL & Setup](#base-url--setup)
3. [Account & Portfolio Endpoints](#account--portfolio-endpoints)
4. [Market Data Endpoints](#market-data-endpoints)
5. [Trading Endpoints](#trading-endpoints)
6. [Watchlist Endpoints](#watchlist-endpoints)
7. [Chat/AI Endpoints](#chatai-endpoints)
8. [Utility Endpoints](#utility-endpoints)
9. [Error Handling](#error-handling)
10. [Multi-Tenant Usage](#multi-tenant-usage)
11. [Common Workflows](#common-workflows)

---

## Authentication

All API requests require an **API Key** in the `X-API-Key` header.

### Getting Your API Key

1. **Contact the server administrator** to get your API key
2. **Or add your own credentials** using the credentials endpoint (see below)

### Using API Key in Requests

```bash
# Include in every request header
-H "X-API-Key: YOUR_API_KEY"
```

### Adding Your Own Credentials

If you have TastyTrade OAuth credentials, you can register them:

```bash
curl -X POST https://tasty.gammabox.app/api/v1/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "your_custom_key",
    "client_secret": "your_tastytrade_client_secret",
    "refresh_token": "your_tastytrade_refresh_token"
  }'
```

**Response:**
```json
{
  "success": true,
  "api_key": "your_custom_key",
  "message": "Credentials saved successfully. Existing sessions and agents cleared."
}
```

### Getting TastyTrade OAuth Credentials (One-Time Setup)

**Refresh tokens are long-lived** - you should only need to set them up once. However, if you get "Grant revoked" errors, you'll need to get a new refresh token.

**Steps to Get Refresh Token:**

1. **Create OAuth App** (if you don't have one):
   - Visit: https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications
   - Click "Create OAuth Application"
   - Check all scopes/permissions
   - Save your **Client ID** and **Client Secret**

2. **Create Personal OAuth Grant**:
   - In your OAuth app settings, click "New Personal OAuth Grant"
   - Check all scopes
   - Click "Create Grant"
   - Copy the generated **Refresh Token** (this is what you'll use)

3. **Register with API**:
   ```bash
   curl -X POST https://tasty.gammabox.app/api/v1/credentials \
     -H "Content-Type: application/json" \
     -d '{
       "api_key": "your_api_key",
       "client_secret": "your_client_secret_from_step_1",
       "refresh_token": "your_refresh_token_from_step_2"
     }'
   ```

**Important Notes:**
- **Refresh tokens are long-lived** - you should only need to set them up once
- **Access tokens expire every 15 minutes** - the server automatically refreshes them using your refresh token (per [TastyTrade FAQ](https://developer.tastytrade.com/faq/))
- **You don't need to manually refresh tokens** - the server handles this automatically via `session.refresh()`
- If you see "Grant revoked" errors, your refresh token was revoked (check TastyTrade OAuth settings)
- You can update credentials anytime using the same endpoint

**How Token Refresh Works (Automatic):**
According to the [TastyTrade API FAQ](https://developer.tastytrade.com/faq/):
- Access tokens last **15 minutes** and must be sent with every request
- The server automatically calls `session.refresh()` before tokens expire
- Your refresh token is used behind the scenes to get new access tokens
- **You never need to manually refresh** - it's all handled automatically

**Why You Might See "Grant Revoked" Errors:**
- You manually revoked the OAuth grant in TastyTrade settings
- The OAuth app was deleted or modified  
- TastyTrade revoked it for security reasons
- The refresh token was copied incorrectly
- Too many failed authentication attempts (IP may be blocked for 8 hours - see [FAQ](https://developer.tastytrade.com/faq/))

**Solution:** Get a new refresh token (see steps above) and update your credentials. Once set correctly, refresh tokens should work indefinitely. The server will automatically refresh access tokens every 15 minutes.

---

## Base URL & Setup

### Production Server
```
https://tasty.gammabox.app
```

### Local Development
```
http://localhost:8000
```

### Base Path
All endpoints are under `/api/v1/`

### Required Headers
```bash
Content-Type: application/json
X-API-Key: YOUR_API_KEY
```

### Interactive API Documentation

Once connected, visit:
- **Swagger UI**: `https://tasty.gammabox.app/docs`
- **ReDoc**: `https://tasty.gammabox.app/redoc`

---

## Account & Portfolio Endpoints

### Get Account Balances

Get cash balance, buying power, net liquidating value, and account equity.

**Endpoint:** `GET /api/v1/balances`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=YOUR_ACCOUNT_ID" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
{
  "cash": "14000.00",
  "net_liquidating_value": "14500.00",
  "buying_power": "14000.00",
  "equity": "14500.00",
  "day_trading_buying_power": "0.00"
}
```

---

### Get Open Positions

Get all open positions with current values, unrealized P&L, and Greeks.

**Endpoint:** `GET /api/v1/positions`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/positions?account_id=YOUR_ACCOUNT_ID" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
{
  "positions": [
    {
      "symbol": "SPY   251219P00685000",
      "quantity": 1,
      "average_open_price": "4.50",
      "current_price": "5.25",
      "unrealized_pnl": "75.00",
      "delta": "-0.68",
      "gamma": "0.02",
      "theta": "-0.15",
      "vega": "0.08"
    }
  ],
  "table": "formatted table string"
}
```

---

### Get Portfolio Value History

Get portfolio value over time (1d, 1m, 3m, 6m, 1y, all).

**Endpoint:** `GET /api/v1/net-liquidating-value-history`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID
- `time_back` (optional) - Time period: `1d`, `1m`, `3m`, `6m`, `1y`, `all` (default: `1y`)

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/net-liquidating-value-history?account_id=YOUR_ACCOUNT_ID&time_back=1m" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### Get Transaction History

Get all transactions including trades and money movements.

**Endpoint:** `GET /api/v1/transaction-history`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID
- `days` (optional) - Number of days to look back (default: 90)
- `underlying_symbol` (optional) - Filter by symbol (e.g., "SPY")
- `transaction_type` (optional) - Filter by type: `Trade` or `Money Movement`

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/transaction-history?account_id=YOUR_ACCOUNT_ID&days=30&transaction_type=Trade" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### Get Order History

Get all order history including filled, canceled, and rejected orders.

**Endpoint:** `GET /api/v1/order-history`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID
- `days` (optional) - Number of days to look back (default: 7)
- `underlying_symbol` (optional) - Filter by symbol

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/order-history?account_id=YOUR_ACCOUNT_ID&days=7" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## Market Data Endpoints

### Get Real-Time Quotes

Get live quotes for stocks and/or options via DXLink streaming.

**Endpoint:** `POST /api/v1/quotes`

**Request Body:**
```json
[
  {
    "symbol": "AAPL"
  },
  {
    "symbol": "SPY",
    "option_type": "C",
    "strike_price": 690.0,
    "expiration_date": "2025-12-19"
  }
]
```

**Query Parameters:**
- `timeout` (optional) - Timeout in seconds (default: 10.0)

**Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/quotes?timeout=10.0" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '[
    {"symbol": "SPY"},
    {"symbol": "AAPL", "option_type": "C", "strike_price": 150.0, "expiration_date": "2025-12-20"}
  ]'
```

**Response:**
```json
{
  "quotes": [
    {
      "event_symbol": "SPY",
      "bid_price": "687.50",
      "ask_price": "687.51",
      "last_price": "687.50",
      "bid_size": 100,
      "ask_size": 200
    }
  ],
  "table": "formatted table",
  "claude_analysis": "AI analysis of quotes"
}
```

---

### Get Option Greeks

Get Greeks (delta, gamma, theta, vega, rho) for options.

**Endpoint:** `POST /api/v1/greeks`

**Request Body:**
```json
[
  {
    "symbol": "SPY",
    "option_type": "P",
    "strike_price": 685.0,
    "expiration_date": "2025-12-19"
  }
]
```

**Query Parameters:**
- `timeout` (optional) - Timeout in seconds (default: 10.0)

**Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/greeks?timeout=10.0" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '[
    {"symbol": "SPY", "option_type": "P", "strike_price": 685.0, "expiration_date": "2025-12-19"}
  ]'
```

**Response:**
```json
{
  "greeks": [
    {
      "event_symbol": "SPY   251219P00685000",
      "delta": "-0.68",
      "gamma": "0.02",
      "theta": "-0.15",
      "vega": "0.08",
      "rho": "0.01"
    }
  ],
  "table": "formatted table",
  "claude_analysis": "AI analysis"
}
```

---

### Get Market Metrics

Get IV rank, percentile, beta, liquidity, and other market metrics.

**Endpoint:** `POST /api/v1/market-metrics`

**Request Body:**
```json
["SPY", "AAPL", "QQQ"]
```

**Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/market-metrics" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '["SPY", "AAPL", "QQQ"]'
```

**Response:**
```json
{
  "metrics": [
    {
      "symbol": "SPY",
      "iv_rank": 0.45,
      "iv_percentile": 0.52,
      "beta": 1.0,
      "liquidity_rating": "High"
    }
  ],
  "table": "formatted table",
  "claude_analysis": "AI analysis"
}
```

---

### Get Market Status

Get market hours and status for exchanges.

**Endpoint:** `GET /api/v1/market-status`

**Query Parameters:**
- `exchanges` (optional) - Comma-separated list: `Equity`, `CME`, `CFE`, `Smalls` (default: `Equity`)

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/market-status?exchanges=Equity" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
[
  {
    "exchange": "Equity",
    "status": "open",
    "close_at": "2025-12-15T16:00:00-05:00"
  }
]
```

---

### Search Symbols

Search for symbols by name or ticker.

**Endpoint:** `GET /api/v1/search-symbols`

**Query Parameters:**
- `symbol` (required) - Search query

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/search-symbols?symbol=apple" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### Get Option Chain

Get complete option chain for a symbol, including all available expiration dates, strikes, and option contracts.

**Endpoint:** `GET /api/v1/option-chain`

**Query Parameters:**
- `symbol` (required) - Stock symbol (e.g., "TSLA", "AAPL", "SPY")

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/option-chain?symbol=TSLA" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
{
  "symbol": "TSLA",
  "total_expirations": 15,
  "total_options": 450,
  "expiration_dates": [
    "2024-12-27",
    "2025-01-03",
    "2025-01-10"
  ],
  "chain": {
    "2024-12-27": {
      "calls": [
        {
          "strike_price": 240.0,
          "option_type": "C",
          "streamer_symbol": "TSLA   241227C00240000",
          "expiration_date": "2024-12-27",
          "symbol": "TSLA   241227C00240000"
        }
      ],
      "puts": [
        {
          "strike_price": 240.0,
          "option_type": "P",
          "streamer_symbol": "TSLA   241227P00240000",
          "expiration_date": "2024-12-27",
          "symbol": "TSLA   241227P00240000"
        }
      ],
      "strikes": [240.0, 245.0, 250.0],
      "total_options": 30
    }
  },
  "all_options": [
    {
      "strike_price": 240.0,
      "option_type": "C",
      "streamer_symbol": "TSLA   241227C00240000",
      "expiration_date": "2024-12-27",
      "symbol": "TSLA   241227C00240000"
    }
  ],
  "table": "Complete Option Chain for TSLA\nExpiration   DTE    Calls    Puts     Strikes   Total   \n------------------------------------------------------------\n2024-12-27   4      15       15       10        30      \n..."
}
```

**Response Fields:**
- `symbol` - The underlying symbol
- `total_expirations` - Total number of expiration dates available
- `total_options` - Total number of option contracts across all expirations
- `expiration_dates` - Array of all available expiration dates (YYYY-MM-DD format)
- `chain` - Object organized by expiration date, containing:
  - `calls` - Array of all call options for that expiration
  - `puts` - Array of all put options for that expiration
  - `strikes` - Array of all available strike prices
  - `total_options` - Total options for that expiration
- `all_options` - Flat array of all options sorted by expiration, strike, and type
- `table` - Formatted summary table with DTE (days to expiration) calculations

**Use Cases:**
- Find all available expiration dates for a symbol
- Get all strikes for a specific expiration
- Filter for specific DTE (days to expiration) options
- Get complete option chain data for analysis

**Example: Filter for 4DTE Options**
```bash
# Get option chain
CHAIN=$(curl -s -X GET "https://tasty.gammabox.app/api/v1/option-chain?symbol=TSLA" \
  -H "X-API-Key: YOUR_API_KEY")

# Extract 4DTE expiration (4 days to expiration)
# The table includes DTE calculations for easy filtering
```

---

## Trading Endpoints

### Get Live Orders

Get currently active/pending orders.

**Endpoint:** `GET /api/v1/live-orders`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/live-orders?account_id=YOUR_ACCOUNT_ID" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### Place Order

Place multi-leg orders (stocks or options). Auto-prices from quotes if `price` is not provided.

**Endpoint:** `POST /api/v1/place-order`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID

**Request Body:**
```json
{
  "legs": [
    {
      "symbol": "SPY",
      "option_type": "P",
      "action": "Buy to Open",
      "quantity": 1,
      "strike_price": 685.0,
      "expiration_date": "2025-12-19"
    }
  ],
  "price": -4.50,
  "time_in_force": "Day",
  "dry_run": false
}
```

**Field Descriptions:**
- `legs` - Array of order legs (supports multi-leg strategies)
- `price` - Limit price (optional, auto-calculated if null)
  - **Negative values** for debit orders (buying)
  - **Positive values** for credit orders (selling)
- `time_in_force` - `Day`, `GTC`, or `IOC`
- `dry_run` - If `true`, validates without placing

**Stock Order Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=YOUR_ACCOUNT_ID" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "legs": [
      {"symbol": "AAPL", "action": "Buy", "quantity": 100}
    ],
    "price": null,
    "time_in_force": "Day",
    "dry_run": false
  }'
```

**Option Order Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=YOUR_ACCOUNT_ID" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "legs": [
      {
        "symbol": "SPY",
        "option_type": "P",
        "action": "Buy to Open",
        "quantity": 1,
        "strike_price": 685.0,
        "expiration_date": "2025-12-19"
      }
    ],
    "price": -4.50,
    "time_in_force": "Day",
    "dry_run": false
  }'
```

**Spread Example (Call Spread):**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=YOUR_ACCOUNT_ID" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "legs": [
      {
        "symbol": "SPY",
        "option_type": "C",
        "action": "Buy to Open",
        "quantity": 1,
        "strike_price": 690.0,
        "expiration_date": "2025-12-19"
      },
      {
        "symbol": "SPY",
        "option_type": "C",
        "action": "Sell to Open",
        "quantity": 1,
        "strike_price": 695.0,
        "expiration_date": "2025-12-19"
      }
    ],
    "price": null,
    "time_in_force": "Day",
    "dry_run": false
  }'
```

**Response:**
```json
{
  "id": "846283",
  "status": "Filled",
  "fills": [
    {
      "price": "4.50",
      "quantity": 1
    }
  ]
}
```

---

### Replace Order

Modify an existing order's price.

**Endpoint:** `POST /api/v1/replace-order/{order_id}`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID

**Request Body:**
```json
{
  "price": -4.75
}
```

**Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/replace-order/846283?account_id=YOUR_ACCOUNT_ID" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"price": -4.75}'
```

---

### Cancel Order

Cancel a pending order.

**Endpoint:** `DELETE /api/v1/orders/{order_id}`

**Query Parameters:**
- `account_id` (required) - Your TastyTrade account ID

**Example:**
```bash
curl -X DELETE "https://tasty.gammabox.app/api/v1/orders/846283?account_id=YOUR_ACCOUNT_ID" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
{
  "success": true,
  "order_id": "846283"
}
```

---

## Watchlist Endpoints

### Get Watchlists

Get public or private watchlists.

**Endpoint:** `GET /api/v1/watchlists`

**Query Parameters:**
- `watchlist_type` (optional) - `public` or `private` (default: `private`)
- `name` (optional) - Specific watchlist name (returns all if omitted)

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/watchlists?watchlist_type=private" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### Manage Private Watchlist

Add or remove symbols from a private watchlist.

**Endpoint:** `POST /api/v1/watchlists/private/manage`

**Request Body:**
```json
{
  "action": "add",
  "symbols": [
    {"symbol": "AAPL", "instrument_type": "Equity"},
    {"symbol": "TSLA", "instrument_type": "Equity"}
  ],
  "name": "tech-stocks"
}
```

**Example - Add Symbols:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/watchlists/private/manage" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "action": "add",
    "symbols": [
      {"symbol": "AAPL", "instrument_type": "Equity"},
      {"symbol": "TSLA", "instrument_type": "Equity"}
    ],
    "name": "tech-stocks"
  }'
```

**Example - Remove Symbols:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/watchlists/private/manage" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "action": "remove",
    "symbols": [
      {"symbol": "AAPL", "instrument_type": "Equity"}
    ],
    "name": "tech-stocks"
  }'
```

---

### Delete Private Watchlist

Delete a private watchlist.

**Endpoint:** `DELETE /api/v1/watchlists/private/{name}`

**Example:**
```bash
curl -X DELETE "https://tasty.gammabox.app/api/v1/watchlists/private/tech-stocks" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## Chat/AI Endpoints

### Chat with AI Agent

Chat with the Claude AI agent that has access to all TastyTrade MCP tools. The agent uses the live trading instructions and can execute trades, analyze positions, and provide market insights.

**Endpoint:** `POST /api/v1/chat`

**Request Body:**
```json
{
  "message": "What are my current positions?",
  "message_history": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi! How can I help you?"}
  ]
}
```

**Example:**
```bash
curl -X POST "https://tasty.gammabox.app/api/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "message": "What are my current positions?",
    "message_history": null
  }'
```

**Response:**
```json
{
  "response": "You currently have 1 open position:\n\nSPY 685P 12/19 - 1 contract\nEntry: $4.50\nCurrent: $5.25\nUnrealized P&L: +$75.00\n\nDelta: -0.68\nTheta: -0.15/day",
  "message_history": [
    {"role": "user", "content": "What are my current positions?"},
    {"role": "assistant", "content": "..."}
  ]
}
```

**Example Queries:**
- "Get my account balances"
- "What are my current positions?"
- "Get a quote for SPY"
- "Analyze my portfolio"
- "What's the IV rank for SPY?"
- "Place an order to buy 1 SPY 685P expiring 12/19"

---

## Utility Endpoints

### Get Current Time (NYC)

Get current time in New York timezone (market time).

**Endpoint:** `GET /api/v1/current-time`

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/api/v1/current-time" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
{
  "current_time_nyc": "2025-12-15T14:30:00-05:00"
}
```

---

### Health Check

Check if the server is running (no authentication required).

**Endpoint:** `GET /health`

**Example:**
```bash
curl -X GET "https://tasty.gammabox.app/health"
```

**Response:**
```json
{
  "status": "healthy",
  "service": "tasty-agent-http"
}
```

---

## Error Handling

### HTTP Status Codes

- `200` - Success
- `400` - Bad Request (invalid parameters)
- `401` - Unauthorized (missing or invalid API key)
- `404` - Not Found (account or resource not found)
- `500` - Internal Server Error
- `504` - Gateway Timeout (request took too long)

### Error Response Format

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common Errors

**Missing API Key:**
```json
{
  "detail": "Missing API key. Provide X-API-Key header."
}
```

**Invalid API Key:**
```json
{
  "detail": "Invalid API key. No credentials configured for this key."
}
```

**Missing Account ID:**
```json
{
  "detail": "account_id is required. Provide it as a query parameter."
}
```

**Account Not Found:**
```json
{
  "detail": "Account '123456789' not found. Available accounts: ['987654321']"
}
```

**TastyTrade Credentials Invalid/Revoked:**
```json
{
  "detail": "TastyTrade refresh token is invalid or revoked. Please update your credentials using POST /api/v1/credentials. To get a new refresh token, visit https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications and create a new Personal OAuth Grant. Error: Grant revoked"
}
```

**How to Fix:**
1. Visit https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications
2. Check if your OAuth app still exists and is active
3. Create a new "Personal OAuth Grant" to get a new refresh token
4. Update credentials via `POST /api/v1/credentials` with the new refresh token

---

## Multi-Tenant Usage

The API supports multiple tenants, each with their own API key and TastyTrade credentials.

### Adding Multiple Tenants

```bash
# Tenant 1
curl -X POST https://tasty.gammabox.app/api/v1/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "tenant1_key",
    "client_secret": "tenant1_secret",
    "refresh_token": "tenant1_token"
  }'

# Tenant 2
curl -X POST https://tasty.gammabox.app/api/v1/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "tenant2_key",
    "client_secret": "tenant2_secret",
    "refresh_token": "tenant2_token"
  }'
```

### Using Different Tenants

Each tenant uses their own API key in the `X-API-Key` header:

```bash
# Tenant 1 request
curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=TENANT1_ACCOUNT" \
  -H "X-API-Key: tenant1_key"

# Tenant 2 request
curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=TENANT2_ACCOUNT" \
  -H "X-API-Key: tenant2_key"
```

### Listing All Tenants

```bash
curl -X GET "https://tasty.gammabox.app/api/v1/credentials" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## Common Workflows

### Workflow 1: Check Account & Place Order

```bash
#!/bin/bash

API_KEY="YOUR_API_KEY"
ACCOUNT_ID="YOUR_ACCOUNT_ID"
BASE_URL="https://tasty.gammabox.app"

# 1. Check balances
echo "Checking balances..."
curl -X GET "${BASE_URL}/api/v1/balances?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}" | jq '.'

# 2. Get current positions
echo "Getting positions..."
curl -X GET "${BASE_URL}/api/v1/positions?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}" | jq '.'

# 3. Get quote for SPY
echo "Getting SPY quote..."
curl -X POST "${BASE_URL}/api/v1/quotes" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '[{"symbol": "SPY"}]' | jq '.'

# 4. Place order (dry run first)
echo "Placing order (dry run)..."
curl -X POST "${BASE_URL}/api/v1/place-order?account_id=${ACCOUNT_ID}" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "legs": [{
      "symbol": "SPY",
      "option_type": "P",
      "action": "Buy to Open",
      "quantity": 1,
      "strike_price": 685.0,
      "expiration_date": "2025-12-19"
    }],
    "price": null,
    "time_in_force": "Day",
    "dry_run": true
  }' | jq '.'
```

### Workflow 2: Monitor Positions & Close

```bash
#!/bin/bash

API_KEY="YOUR_API_KEY"
ACCOUNT_ID="YOUR_ACCOUNT_ID"
BASE_URL="https://tasty.gammabox.app"

# 1. Get positions
POSITIONS=$(curl -s -X GET "${BASE_URL}/api/v1/positions?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}")

# 2. Extract position symbols
SYMBOLS=$(echo $POSITIONS | jq -r '.positions[].symbol')

# 3. For each position, get current quote
for SYMBOL in $SYMBOLS; do
  echo "Getting quote for $SYMBOL..."
  curl -X POST "${BASE_URL}/api/v1/quotes" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d "[{\"symbol\": \"$SYMBOL\"}]" | jq '.'
done

# 4. Close a position (example)
curl -X POST "${BASE_URL}/api/v1/place-order?account_id=${ACCOUNT_ID}" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "legs": [{
      "symbol": "SPY",
      "option_type": "P",
      "action": "Sell to Close",
      "quantity": 1,
      "strike_price": 685.0,
      "expiration_date": "2025-12-19"
    }],
    "price": null,
    "time_in_force": "Day",
    "dry_run": false
  }' | jq '.'
```

### Workflow 3: AI-Powered Trading Analysis

```bash
#!/bin/bash

API_KEY="YOUR_API_KEY"
BASE_URL="https://tasty.gammabox.app"

# Ask AI to analyze portfolio
curl -X POST "${BASE_URL}/api/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "message": "Analyze my portfolio and suggest any adjustments based on current market conditions",
    "message_history": null
  }' | jq -r '.response'
```

---

## Rate Limiting

The API has built-in rate limiting:
- **2 requests per second** per API key
- Requests exceeding this limit will be queued
- No hard limit on total requests per day

---

## Best Practices

1. **Always use HTTPS** in production
2. **Store API keys securely** - never commit to version control
3. **Use dry_run=true** when testing orders
4. **Handle errors gracefully** - check HTTP status codes
5. **Cache market data** when possible to reduce API calls
6. **Use message_history** in chat endpoint for context
7. **Monitor your account** - check balances before placing orders
8. **Validate order parameters** before submission

---

## SDK Examples

### Python Example

```python
import requests

class TastyTradeAPI:
    def __init__(self, base_url, api_key, account_id):
        self.base_url = base_url
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
        self.account_id = account_id
    
    def get_balances(self):
        response = requests.get(
            f"{self.base_url}/api/v1/balances",
            params={"account_id": self.account_id},
            headers=self.headers
        )
        return response.json()
    
    def get_positions(self):
        response = requests.get(
            f"{self.base_url}/api/v1/positions",
            params={"account_id": self.account_id},
            headers=self.headers
        )
        return response.json()
    
    def get_quotes(self, symbols):
        response = requests.post(
            f"{self.base_url}/api/v1/quotes",
            headers=self.headers,
            json=[{"symbol": s} for s in symbols]
        )
        return response.json()
    
    def get_option_chain(self, symbol):
        response = requests.get(
            f"{self.base_url}/api/v1/option-chain",
            params={"symbol": symbol},
            headers=self.headers
        )
        return response.json()
    
    def place_order(self, legs, price=None, time_in_force="Day", dry_run=False):
        response = requests.post(
            f"{self.base_url}/api/v1/place-order",
            params={"account_id": self.account_id},
            headers=self.headers,
            json={
                "legs": legs,
                "price": price,
                "time_in_force": time_in_force,
                "dry_run": dry_run
            }
        )
        return response.json()

# Usage
api = TastyTradeAPI(
    base_url="https://tasty.gammabox.app",
    api_key="YOUR_API_KEY",
    account_id="YOUR_ACCOUNT_ID"
)

# Get balances
balances = api.get_balances()
print(balances)

# Get quotes
quotes = api.get_quotes(["SPY", "AAPL"])
print(quotes)

# Get option chain
chain = api.get_option_chain("TSLA")
print(f"Total expirations: {chain['total_expirations']}")
print(f"Total options: {chain['total_options']}")

# Filter for 4DTE options
from datetime import datetime, date
today = date.today()
for exp_date_str in chain['expiration_dates']:
    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
    dte = (exp_date - today).days
    if dte == 4:
        print(f"4DTE expiration: {exp_date_str}")
        print(f"Available strikes: {chain['chain'][exp_date_str]['strikes']}")

# Place order
order = api.place_order(
    legs=[{
        "symbol": "SPY",
        "option_type": "P",
        "action": "Buy to Open",
        "quantity": 1,
        "strike_price": 685.0,
        "expiration_date": "2025-12-19"
    }],
    price=-4.50,
    dry_run=True
)
print(order)
```

### JavaScript/Node.js Example

```javascript
const axios = require('axios');

class TastyTradeAPI {
  constructor(baseUrl, apiKey, accountId) {
    this.baseUrl = baseUrl;
    this.headers = {
      'X-API-Key': apiKey,
      'Content-Type': 'application/json'
    };
    this.accountId = accountId;
  }

  async getBalances() {
    const response = await axios.get(
      `${this.baseUrl}/api/v1/balances`,
      {
        params: { account_id: this.accountId },
        headers: this.headers
      }
    );
    return response.data;
  }

  async getPositions() {
    const response = await axios.get(
      `${this.baseUrl}/api/v1/positions`,
      {
        params: { account_id: this.accountId },
        headers: this.headers
      }
    );
    return response.data;
  }

  async getQuotes(symbols) {
    const response = await axios.post(
      `${this.baseUrl}/api/v1/quotes`,
      symbols.map(s => ({ symbol: s })),
      { headers: this.headers }
    );
    return response.data;
  }

  async getOptionChain(symbol) {
    const response = await axios.get(
      `${this.baseUrl}/api/v1/option-chain`,
      {
        params: { symbol },
        headers: this.headers
      }
    );
    return response.data;
  }

  async placeOrder(legs, price = null, timeInForce = 'Day', dryRun = false) {
    const response = await axios.post(
      `${this.baseUrl}/api/v1/place-order`,
      {
        legs,
        price,
        time_in_force: timeInForce,
        dry_run: dryRun
      },
      {
        params: { account_id: this.accountId },
        headers: this.headers
      }
    );
    return response.data;
  }
}

// Usage
const api = new TastyTradeAPI(
  'https://tasty.gammabox.app',
  'YOUR_API_KEY',
  'YOUR_ACCOUNT_ID'
);

(async () => {
  const balances = await api.getBalances();
  console.log(balances);

  const quotes = await api.getQuotes(['SPY', 'AAPL']);
  console.log(quotes);

  // Get option chain
  const chain = await api.getOptionChain('TSLA');
  console.log(`Total expirations: ${chain.total_expirations}`);
  console.log(`Total options: ${chain.total_options}`);

  // Filter for 4DTE options
  const today = new Date();
  for (const expDateStr of chain.expiration_dates) {
    const expDate = new Date(expDateStr);
    const dte = Math.ceil((expDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
    if (dte === 4) {
      console.log(`4DTE expiration: ${expDateStr}`);
      console.log(`Available strikes: ${chain.chain[expDateStr].strikes}`);
    }
  }
})();
```

---

## Troubleshooting

### "Grant Revoked" Errors

If you're getting `invalid_grant` or `Grant revoked` errors:

1. **Check your TastyTrade OAuth settings:**
   - Visit: https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications
   - Verify your OAuth app is still active
   - Check if any Personal OAuth Grants are still valid

2. **Get a new refresh token:**
   - Create a new "Personal OAuth Grant" in your OAuth app
   - Copy the new refresh token

3. **Update your credentials:**
   ```bash
   curl -X POST https://tasty.gammabox.app/api/v1/credentials \
     -H "Content-Type: application/json" \
     -d '{
       "api_key": "YOUR_API_KEY",
       "client_secret": "YOUR_CLIENT_SECRET",
       "refresh_token": "YOUR_NEW_REFRESH_TOKEN"
     }'
   ```

4. **Verify it works:**
   ```bash
   curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=YOUR_ACCOUNT_ID" \
     -H "X-API-Key: YOUR_API_KEY"
   ```

### Token Refresh Explained

**You should NOT need to manually refresh tokens.** Here's how it works (per [TastyTrade FAQ](https://developer.tastytrade.com/faq/)):

- **Refresh Token** (what you provide once): Long-lived, used to get access tokens
- **Access Token** (what the server uses): Expires every 15 minutes, automatically refreshed by the server
- **Session Refresh**: The server automatically calls `session.refresh()` before access tokens expire
- **Background Task**: A keep-alive task runs every 5 minutes to refresh sessions proactively

**According to TastyTrade:**
> "Access tokens last 15 minutes and must be sent with every request in the `Authorization` header."

The server handles this automatically - you provide the refresh token once, and it's used to get new access tokens as needed.

**You only need to update your refresh token if:**
- It was revoked in TastyTrade settings
- You're getting "Grant revoked" errors  
- You want to use a different TastyTrade account
- Your IP was blocked (8-hour block after too many failed attempts - contact api.support@tastytrade.com)

**Common Issues (from [TastyTrade FAQ](https://developer.tastytrade.com/faq/)):**
- `unauthorized` errors: Access token expired (server should auto-refresh)
- `invalid_credentials`: Wrong username/password or wrong environment (sandbox vs production)
- Timeouts: IP blocked due to too many failed login attempts (8-hour block)

---

## Support

For issues or questions:
1. Check the interactive API docs at `/docs`
2. Review error messages in responses
3. Check the troubleshooting section above
4. Contact the server administrator

---

**Last Updated:** December 2025

