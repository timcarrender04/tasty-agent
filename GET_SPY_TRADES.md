++ b/backend_server/tasty-agent/GET_SPY_TRADES.md
# Get Today's SPY Trades with P&L

This guide shows you how to get a list of your live SPY trades from today with P&L.

## Option 1: Using the Python Script (Recommended)

### Prerequisites
- Python 3.12+
- Install dependencies: `pip install requests tabulate`

### Run the script:

```bash
# Method 1: Pass API key as argument
python3 get_spy_trades_api.py YOUR_API_KEY

# Method 2: Set API key as environment variable
export API_KEY=YOUR_API_KEY
python3 get_spy_trades_api.py
```

## Option 2: Using curl

```bash
# Replace YOUR_API_KEY with your actual API key
curl -H "X-API-Key: YOUR_API_KEY" \
  "http://localhost:8033/api/v1/recent-trades?days=1&underlying_symbol=SPY" \
  | python3 -m json.tool
```

## Option 3: Using Python Script with Direct TastyTrade API

If you have TastyTrade credentials in your environment:

```bash
# Make sure you have the tastytrade library installed
# The script will use TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN from environment
python3 get_spy_trades_today.py
```

## Output Format

The output will show:
- **Closed Trades**: Trades that have been closed with their P&L
- **Open Positions**: Positions that are still open (no P&L yet)
- **Summary**: Total P&L, win rate, fees, etc.

Example output:
```
📊 Found 3 SPY trade(s) today:

========================================================================================================
CLOSED TRADES (with P&L):
========================================================================================================
| Time              | Symbol  | Action      | Qty | Price    | Net Value | Fees    | P&L      |
|-------------------|---------|-------------|-----|----------|-----------|---------|----------|
| 09:35:12          | SPY     | Buy to Open | 100 | $684.50  | $68,450   | $1.00   | —        |
| 10:15:23          | SPY     | Sell to Close| 100| $685.25  | $68,525   | $1.00   | +$74.00  |

📈 Summary for Closed Trades:
   Total Closed Trades: 1
   Winning Trades: 1
   Losing Trades: 0
   Total Fees: $2.00
   Total P&L: +$74.00
   Win Rate: 100.0%
```

## Finding Your API Key

If you're using the kiosk/admin interface, the API key should be configured there.
You can also check:
- Environment variables: `echo $API_KEY`
- Configuration files in the project

## Notes

- Make sure the HTTP API server is running on `localhost:8033`
- The API will return trades from the last 1 day, filtered to show only today's trades
- P&L is calculated using FIFO (First In, First Out) matching for closed positions
- Open positions won't show P&L until they're closed


