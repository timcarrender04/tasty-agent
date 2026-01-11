# Paper Trading Validation

## Overview

This document explains how to validate that paper trading mode is working correctly by testing market-closed error handling.

## How It Works

When you attempt to place an order while the market is closed, the TastyTrade API should return a "market is closed" error. This validation ensures:

1. ✅ Paper trading mode is correctly configured
2. ✅ TastyTrade API correctly enforces market hours (even in sandbox)
3. ✅ Error handling properly catches and returns market-closed errors
4. ✅ Your application correctly displays these errors to users

## Validation Script

A validation script is provided to test this:

```bash
# Run with default settings (SPY symbol, first available API key)
python scripts/validate_paper_trading.py

# Or specify symbol and API key
python scripts/validate_paper_trading.py --symbol SPY --api-key YOUR_API_KEY
```

### What the Script Does

1. **Checks Mode**: Verifies paper trading mode is enabled
2. **Checks Market Status**: Shows current market status (Open/Closed)
3. **Attempts Order**: Places a MARKET order for the specified symbol
4. **Validates Error**: Confirms "market is closed" error is returned when appropriate

### Expected Behavior

#### When Market is CLOSED:
```
📋 Error received from TastyTrade API:
   TastyApiError: Market is closed

✅ VALIDATION PASSED!
   ✓ Paper trading mode is working correctly
   ✓ TastyTrade API correctly returned 'market is closed' error
   ✓ Error handling is functioning as expected
```

#### When Market is OPEN:
```
⚠️  Order was ACCEPTED by TastyTrade API!
   This means the market appears to be OPEN.
```

## Testing via API

You can also test this through the HTTP API:

```bash
# Make sure paper trading is enabled
export TASTYTRADE_PAPER_MODE=true

# Attempt to place an order when market is closed
curl -X POST "http://localhost:8000/place_order" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "legs": [
      {
        "symbol": "SPY",
        "action": "Buy",
        "quantity": 1
      }
    ],
    "order_type": "Market"
  }'
```

### Expected Response (Market Closed):

```json
{
  "detail": "Market is closed"
}
```

**Important**: The error message comes **directly from TastyTrade API** without modification. This allows you to validate that:
- The API integration is working correctly
- The error is actually from TastyTrade (not from our code)
- Paper trading mode is functioning as expected

## Error Handling

The `place_order` endpoint passes through TastyTrade API errors **directly without modification**:

1. **Catches TastyTrade API Exceptions**: All exceptions from `account.a_place_order()` are caught
2. **Extracts Error Message**: Uses the exact error message from TastyTrade API
3. **Direct Passthrough**: Returns the API's error message in the `detail` field
4. **Status Code Mapping**: Maps API errors to appropriate HTTP status codes (400 for client errors, 500 for server errors)

**Why this approach?**
- ✅ Frontend can validate the error came from TastyTrade API
- ✅ No message modification means you see exactly what the API returns
- ✅ Easier debugging - the error is the actual API error
- ✅ Paper trading mode is safe - errors behave the same as live mode for validation

## Stop-Loss Orders

Stop-loss orders also handle market-closed errors:
- If market is closed when attempting to place a stop-loss, it logs a warning and returns `None`
- The main order placement will continue, but stop-loss placement will be skipped
- This prevents the entire order from failing just because stop-loss can't be placed

## Configuration

Paper trading mode is enabled by setting environment variables:

```bash
# Option 1: Set paper mode
export TASTYTRADE_PAPER_MODE=true

# Option 2: Set sandbox mode (same effect)
export TASTYTRADE_SANDBOX=true
```

To disable (use live trading):
```bash
unset TASTYTRADE_PAPER_MODE
unset TASTYTRADE_SANDBOX
```

## Best Practices

1. **Always Test in Paper Mode First**: Use paper trading to validate your logic before going live
2. **Handle Market-Closed Errors**: Ensure your UI properly displays market-closed errors to users
3. **Check Market Status**: Use the `/market_status` endpoint to check market hours before allowing orders
4. **Test Outside Market Hours**: Run validation script when market is closed to ensure error handling works

## Troubleshooting

### Order Accepted When Market Should Be Closed

If orders are accepted when you expect them to be rejected:
- Check market status: Market might actually be open (e.g., extended hours)
- Paper trading might allow orders outside regular hours (check TastyTrade documentation)
- Market status check might be incorrect (verify with TastyTrade API directly)

### Error Not Caught

If you don't see the "market is closed" error:
- Verify paper trading mode is enabled: `echo $TASTYTRADE_PAPER_MODE`
- Check TastyTrade API error format (they might use different wording)
- Check server logs for full error details

### Authentication Issues

If you get authentication errors:
- Verify API key is correct
- Check credentials are properly configured
- Ensure refresh token is valid (not expired/revoked)
