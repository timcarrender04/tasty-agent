# Safe Testing Guide for TastyTrade API

## ⚠️ Important Safety Notice

**NEVER test API functionality with LIVE trading accounts.** Always use PAPER TRADING mode for testing.

## Verifying Paper Mode

Before running any tests, verify you're in paper trading mode:

```bash
# Check container environment
docker exec tasty-agent-paper-http env | grep TASTYTRADE

# Should show:
# TASTYTRADE_SANDBOX=TRUE
# TASTYTRADE_PAPER_MODE=true
```

## Testing API Error Handling

### Test 1: Market Closed Error (Safe - Paper Mode)

This test validates that:
1. Paper trading mode is active
2. TastyTrade API errors are passed through correctly
3. Your frontend receives the exact API error message

```bash
# Run the test script (requires API_KEY env var)
cd /home/ert/projects/infrastructure/tasty-agent
export API_KEY="your-api-key-here"
./scripts/test_api_error.sh
```

**Expected Behavior:**
- HTTP 400 or 500 status code
- Error message like `"Market is closed"` (direct from TastyTrade API)
- This confirms the API is working and errors are passed through

### Test 2: Manual API Test

```bash
curl -X POST "http://localhost:8034/place_order" \
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

**When Market is Closed:**
```json
{
  "detail": "Market is closed"
}
```

**When Market is Open:**
- Order will be placed (in paper account - SAFE)
- Response will contain order details

## Validating Error Messages

The error message in the `detail` field is the **exact error from TastyTrade API**. This allows you to:

1. **Confirm API Integration**: The error proves the API was called
2. **Validate Error Handling**: Frontend can parse and display API errors
3. **Debug Issues**: See exactly what TastyTrade API returns

## Frontend Validation

In your frontend component (`prompt-input.tsx`), you can validate:

```typescript
// After API call
if (response.error) {
  const errorMessage = response.error.detail;
  
  // This is the EXACT error from TastyTrade API
  console.log('TastyTrade API Error:', errorMessage);
  
  // Validate it's a real API error (not our code)
  if (errorMessage.includes('Market is closed')) {
    // Handle market closed error
  }
}
```

## What Makes This Safe?

1. **Paper Mode**: `TASTYTRADE_SANDBOX=TRUE` ensures paper trading
2. **No Real Money**: Paper accounts use virtual funds
3. **Same API**: Paper mode uses the same API endpoints as live
4. **Real Errors**: Even in paper mode, you get real API error messages

## Troubleshooting

### Error: "Not in paper mode"
- Check environment variables: `TASTYTRADE_PAPER_MODE=true` or `TASTYTRADE_SANDBOX=true`
- Restart the container if needed

### Error: "Authentication failed"
- Verify API key is correct
- Check credentials are loaded in the container

### No Error, Order Placed
- Market might be open (check market hours)
- In paper mode, this is SAFE - order goes to paper account
- Use `dry_run=true` parameter to validate orders without placing

## Best Practices

1. ✅ Always verify paper mode before testing
2. ✅ Test error handling when market is closed (safe, predictable)
3. ✅ Use `dry_run=true` for order validation
4. ✅ Never test with live trading accounts
5. ✅ Validate error messages come from API (not our code)
