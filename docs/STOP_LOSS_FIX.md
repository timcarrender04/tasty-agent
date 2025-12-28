# Stop Loss Order Creation Fix

## Issues Fixed

### 1. ✅ Improved Stop Action Logic
**Problem:** The code didn't properly handle "to close" positions when determining stop-loss actions.

**Fix:**
- Added check to skip stop-loss for "Buy to Close" and "Sell to Close" orders (they're already closing positions)
- Only place stop-loss for "Buy to Open" and "Sell to Open" orders (opening new positions)

**Code Changes:**
- Lines 1164-1166: Skip stop-loss if entry action is "Buy to Close"
- Lines 1179-1181: Skip stop-loss if entry action is "Sell to Close"
- Better logging to explain why stop-loss is skipped

### 2. ✅ Enhanced Error Handling
**Problem:** Errors during stop-loss placement were not logged with sufficient detail.

**Fix:**
- Added full exception traceback logging
- Validate stop price before placing order (must be > 0)
- Check order result status for rejections
- Log entry price and stop price together for debugging

**Code Changes:**
- Lines 1215-1218: Validate stop price
- Lines 1220-1224: Check order result status
- Lines 1227-1230: Enhanced error logging with traceback

### 3. ✅ Better Logging
**Problem:** Stop-loss placement logs didn't include enough context.

**Fix:**
- Log both entry action and stop action together
- Include entry price and stop price in log messages
- Added warning when skipping stop-loss for closing trades

## Testing

After deploying this fix:

1. **Test Buy to Open → Stop Loss:**
   ```bash
   # Place a "Buy to Open" order - should create stop-loss
   # Entry: "Buy to Open" → Stop: "Sell to Close"
   ```

2. **Test Sell to Open → Stop Loss:**
   ```bash
   # Place a "Sell to Open" order - should create stop-loss
   # Entry: "Sell to Open" → Stop: "Buy to Close"
   ```

3. **Test Closing Trades:**
   ```bash
   # Place "Buy to Close" or "Sell to Close" - should skip stop-loss
   # Should log: "Entry action 'X' is already a closing trade, skipping stop-loss"
   ```

## Deployment

1. **Restart the tasty-agent container:**
   ```bash
   cd /home/ert/projects/infrastructure/tasty-agent
   docker compose restart tasty-agent-http
   ```

2. **Check logs:**
   ```bash
   docker logs -f tasty-agent-http | grep -i "stop"
   ```

## Common Issues

### Stop Price Invalid
If you see: `Invalid stop price calculated`
- Check `max_loss_per_contract` setting (default: $50)
- Check entry fill price is correct
- Check quantity is > 0

### Order Rejected
If stop-loss orders are being rejected by TastyTrade:
- Check account has required permissions
- Verify stop price is within reasonable range
- Check if position already exists (can't place stop-loss on non-existent position)






