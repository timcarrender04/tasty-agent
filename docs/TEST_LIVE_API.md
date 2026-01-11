# Testing Live Trading API Error Handling

## Overview

This guide shows how to test that the TastyTrade API integration is working correctly in **LIVE trading mode** by attempting to place an order when the market is closed.

## Why This Test is Safe

**When the market is closed:**
- ✅ TastyTrade API will **reject** the order with "Market is closed" error
- ✅ **No order will be placed** - the API rejects it before execution
- ✅ You'll receive the **exact error message from TastyTrade API**
- ✅ This validates your API integration is working correctly

## Testing via Chat Prompt

In your frontend chat component (`prompt-input.tsx`), when you try to execute an order and the market is closed:

1. **The order request is sent** to `/place_order` endpoint
2. **TastyTrade API rejects it** with "Market is closed" error  
3. **The error is passed through directly** to your frontend
4. **You see the exact API error** confirming integration works

## Expected Behavior

### When Market is CLOSED:

**API Response:**
```json
{
  "detail": "Market is closed"
}
```

**Status Code:** `400` (Bad Request)

**What This Means:**
- ✅ TastyTrade API is connected and responding
- ✅ Error handling is working correctly
- ✅ The error message is from TastyTrade (not our code)
- ✅ No order was placed (safe - rejected by API)

### When Market is OPEN:

**API Response:**
```json
{
  "entry_order": { ... },
  "stop_loss_orders": [ ... ],
  ...
}
```

**Status Code:** `200` (Success)

**⚠️ Warning:** If market is open, the order **will be placed** in your live account!

## Testing Steps

### 1. Check Market Status

```bash
# Check if market is currently closed
curl -X GET "http://localhost:8033/market_status" \
  -H "X-API-Key: YOUR_API_KEY"
```

### 2. Test Order Execution (Market Closed = Safe)

When market is closed, attempt to place an order via chat:

```bash
# This will be rejected by TastyTrade API (safe)
curl -X POST "http://localhost:8033/place_order" \
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

**Expected Result:**
- HTTP 400 status
- Error: `"Market is closed"`
- No order placed (safe)

### 3. Verify Error in Frontend

In your frontend component, check the error response:

```typescript
try {
  const response = await fetch('/place_order', { ... });
  if (!response.ok) {
    const error = await response.json();
    console.log('TastyTrade API Error:', error.detail);
    // This is the EXACT error from TastyTrade API
  }
} catch (error) {
  // Handle network errors
}
```

## Validating API Integration

The error message proves:

1. **API Connection Works**: TastyTrade API responded
2. **Authentication Works**: Your credentials are valid
3. **Error Handling Works**: Errors are passed through correctly
4. **Frontend Integration Works**: You receive the API error

## Important Notes

- **Market Closed = Safe**: Orders are rejected, nothing executes
- **Market Open = Real Orders**: Orders will execute in your live account
- **Use `dry_run=true`**: For extra safety, add `"dry_run": true` to test order validation without placing
- **Error Message**: The `detail` field contains the exact TastyTrade API error

## Using dry_run for Safety

If you want to test order validation without any risk:

```json
{
  "legs": [...],
  "order_type": "Market",
  "dry_run": true
}
```

This validates the order structure without placing it, even if market is open.
