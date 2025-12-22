# tasty-agent: A TastyTrade MCP Server
[![Trust Score](https://archestra.ai/mcp-catalog/api/badge/quality/ferdousbhai/tasty-agent)](https://archestra.ai/mcp-catalog/ferdousbhai__tasty-agent)

A Model Context Protocol server for TastyTrade brokerage accounts. Enables LLMs to monitor portfolios, analyze positions, and execute trades. Features automated IV analysis prompts and built-in rate limiting (2 requests/second) to prevent API errors.

## Authentication

**OAuth Setup**:
1. Create an OAuth app at https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications
2. Check all scopes, save your client ID and client secret  
3. Create a "New Personal OAuth Grant" in your OAuth app settings (check all scopes)
4. Copy the generated refresh token
5. Configure the MCP server with your credentials (see Usage section below)

## MCP Tools

### Account & Portfolio
- **`get_balances()`** - Account balances and buying power
- **`get_positions()`** - All open positions with current values
- **`get_net_liquidating_value_history(time_back='1y')`** - Portfolio value history ('1d', '1m', '3m', '6m', '1y', 'all')
- **`get_transaction_history(days=90, underlying_symbol=None, transaction_type=None)`** - All transactions: trades + cash flows (default: last 90 days, transaction_type: 'Trade' or 'Money Movement')
- **`get_order_history(days=7, underlying_symbol=None)`** - Order history including filled, canceled, and rejected orders (default: last 7 days)

### Market Data & Research
- **`get_quotes(instruments, timeout=10.0)`** - Real-time quotes for multiple stocks and/or options via DXLink streaming
- **`get_greeks(options, timeout=10.0)`** - Greeks (delta, gamma, theta, vega, rho) for multiple options via DXLink streaming
- **`get_market_metrics(symbols)`** - IV rank, percentile, beta, liquidity for multiple symbols
- **`market_status(exchanges=['Equity'])`** - Market hours and status ('Equity', 'CME', 'CFE', 'Smalls')
- **`search_symbols(symbol)`** - Search for symbols by name/ticker
- **`get_current_time_nyc()`** - Current time in New York timezone (market time)

### Order Management
- **`get_live_orders()`** - Currently active orders
- **`place_order(legs, price=None, time_in_force='Day', dry_run=False)`** - Place multi-leg orders with automatic price discovery from market quotes
  - **Stock actions**: 'Buy', 'Sell'
  - **Option actions**: 'Buy to Open', 'Buy to Close', 'Sell to Open', 'Sell to Close'
- **`replace_order(order_id, price)`** - Modify existing order price (for complex changes, cancel and place new order)
- **`delete_order(order_id)`** - Cancel orders by ID

### Watchlist Management
- **`get_watchlists(watchlist_type='private', name=None)`** - Get watchlists ('public'/'private', all if name=None)
- **`manage_private_watchlist(action, symbols, name='main')`** - Add/remove multiple symbols from private watchlists
- **`delete_private_watchlist(name)`** - Delete private watchlist

### MCP Prompts
- **IV Rank Analysis** - Automated prompt to analyze IV rank extremes across positions and watchlists for entry/exit opportunities

## Key Features

### Smart Order Placement
- Automatic price calculation from real-time market quotes when no price specified
- Multi-leg options strategies (spreads, strangles, etc.) with single function call
- Dry-run mode for testing orders without execution

### Rate Limiting & Reliability
- Built-in rate limiting (2 requests/second) prevents API throttling
- Comprehensive error handling and logging

## HTTP Server Mode (For Multiple Kiosks)

The HTTP server mode allows multiple kiosks/clients to connect to a single TastyTrade API server via REST endpoints. This is ideal for deploying a centralized server that multiple applications can access.

### Starting the HTTP Server

```bash
# Set required environment variables
export TASTYTRADE_CLIENT_SECRET=your_client_secret
export TASTYTRADE_REFRESH_TOKEN=your_refresh_token
export TASTYTRADE_ACCOUNT_ID=your_account_id  # optional, defaults to first account

# Set API key for authentication (REQUIRED for production)
export API_KEY=your-secure-api-key-here

# Optional: Configure CORS origins (comma-separated, default: "*")
export CORS_ORIGINS=http://localhost:3000,https://yourdomain.com

# Optional: Configure host and port (default: 0.0.0.0:8000)
export HOST=0.0.0.0
export PORT=8000

# Run the HTTP server
uv run tasty-agent-http

# Or with uvicorn directly
uvicorn tasty_agent.http_server:app --host 0.0.0.0 --port 8000
```

### API Authentication

All endpoints (except `/health`) require an API key in the `X-API-Key` header:

```bash
curl -H "X-API-Key: your-secure-api-key-here" http://localhost:8000/api/v1/balances
```

### API Endpoints

All MCP tools are available as REST endpoints under `/api/v1/`:

- **Account & Portfolio**: `GET /api/v1/balances`, `GET /api/v1/positions`, `GET /api/v1/net-liquidating-value-history`
- **Market Data**: `POST /api/v1/quotes`, `POST /api/v1/greeks`, `POST /api/v1/market-metrics`, `GET /api/v1/market-status`, `GET /api/v1/search-symbols`
- **History**: `GET /api/v1/transaction-history`, `GET /api/v1/order-history`
- **Trading**: `GET /api/v1/live-orders`, `POST /api/v1/place-order`, `POST /api/v1/replace-order/{order_id}`, `DELETE /api/v1/orders/{order_id}`
- **Watchlists**: `GET /api/v1/watchlists`, `POST /api/v1/watchlists/private/manage`, `DELETE /api/v1/watchlists/private/{name}`
- **Utility**: `GET /api/v1/current-time`, `GET /health`

### Example API Requests

```bash
# Get account balances
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/balances

# Get quotes for multiple instruments
curl -X POST -H "X-API-Key: your-api-key" -H "Content-Type: application/json" \
  -d '[{"symbol": "AAPL"}, {"symbol": "SPY", "option_type": "C", "strike_price": 450, "expiration_date": "2024-12-20"}]' \
  http://localhost:8000/api/v1/quotes

# Place an order
curl -X POST -H "X-API-Key: your-api-key" -H "Content-Type: application/json" \
  -d '{"legs": [{"symbol": "AAPL", "action": "Buy", "quantity": 100}], "dry_run": true}' \
  http://localhost:8000/api/v1/place-order

# Get market metrics
curl -X POST -H "X-API-Key: your-api-key" -H "Content-Type: application/json" \
  -d '["AAPL", "SPY", "TQQQ"]' \
  http://localhost:8000/api/v1/market-metrics
```

### Interactive API Documentation

Once the server is running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### MCP Client Configuration

Add to your MCP client configuration (e.g., `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "tastytrade": {
      "command": "uvx",
      "args": ["tasty-agent"],
      "env": {
        "TASTYTRADE_CLIENT_SECRET": "your_client_secret",
        "TASTYTRADE_REFRESH_TOKEN": "your_refresh_token",
        "TASTYTRADE_ACCOUNT_ID": "your_account_id"
      }
    }
  }
}
```

## Examples

```
"Get my account balances and current positions"
"Get real-time quotes for SPY and AAPL"
"Get quotes for TQQQ C option with strike 100 expiring 2026-01-16"
"Get Greeks for AAPL P option with strike 150 expiring 2024-12-20"
"Buy 100 AAPL shares" (auto-pricing)
"Buy 100 AAPL at $150"
"Buy to open 17 TQQQ calls, strike 100, exp 2026-01-16"
"Place a call spread: buy to open AAPL 150C and sell to open AAPL 155C, both exp 2024-12-20"
"Close my AAPL position: sell to close 10 AAPL calls"
"Modify order 12345 to price $10.05"
"Cancel order 12345"
"Get my trading history from January"
"Get my private watchlists"
"Add TSLA and NVDA to my tech watchlist"
"Remove AAPL from my tech watchlist"
```

## Background Trading Bot

Run automated trading strategies:

```bash
# Run once with instructions
uv run background.py "Check my portfolio and rebalance if needed"

# Run every hour
uv run background.py "Monitor SPY and alert on significant moves" --hourly

# Run every day
uv run background.py "Generate daily portfolio summary" --daily

# Custom period (seconds)
uv run background.py "Scan for covered call opportunities" --period 1800  # every 30 minutes

# Schedule start time (NYC timezone)
uv run background.py "Execute morning trading strategy" --schedule "9:30am" --hourly

# Market open shorthand (9:30am)
uv run background.py "Buy the dip strategy" --market-open --hourly
```

## Development

### Testing with chat.py

For interactive testing during development:
```bash
# Set up environment variables in .env file:
# TASTYTRADE_CLIENT_SECRET=your_secret
# TASTYTRADE_REFRESH_TOKEN=your_token
# TASTYTRADE_ACCOUNT_ID=your_account_id (defaults to the first account)
# OPENAI_API_KEY=your_openai_key (you can provide alternative provider of your choice as supported by pydantic-ai)
# MODEL_IDENTIFIER=model_provider:model_name (defaults to openai:gpt-5-mini)


# Run the interactive client
uv run chat.py
```

The client provides a chat interface to test MCP tools directly. Example commands:
- "Get my account balances"
- "Get quote for SPY" 
- "Place dry-run order: buy 100 AAPL at $150"

### Debug with MCP inspector

```bash
npx @modelcontextprotocol/inspector uvx tasty-agent
```

## License

MIT
