# Speed Optimization Guide

## Current Bottlenecks

### 1. **Large Instruction File (1,956 lines)**
- Every chat request processes ~2,000 lines of instructions
- Adds significant latency to Claude model processing
- Each request sends full prompt to API

### 2. **90 Second Timeout**
- Chat endpoint allows up to 90 seconds for responses
- Complex queries can take 30-60+ seconds
- Multiple sequential API calls add up

### 3. **Sequential Tool Calls**
- Agent makes multiple MCP tool calls one after another
- Each call has network latency (Alpaca + TastyTrade APIs)
- No parallelization of independent calls

### 4. **MCP Server Overhead**
- Each tool call goes through stdio MCP server
- Adds process communication overhead
- Multiple round-trips for complex queries

## Speed Optimization Strategies

### ⚡ **FASTEST: Use Direct Order Endpoint (Bypass Chat)**

For immediate order execution, use the direct REST API endpoint instead of chat:

```bash
# Direct order placement - ~1-2 seconds
curl -X POST "https://tasty.gammabox.app/api/v1/place-order?account_id=YOUR_ACCOUNT_ID" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "legs": [
      {"symbol": "SPY", "option_type": "C", "action": "Buy to Open", "quantity": 1, "strike_price": 685.0, "expiration_date": "2025-12-19"},
      {"symbol": "SPY", "option_type": "P", "action": "Buy to Open", "quantity": 1, "strike_price": 690.0, "expiration_date": "2025-12-19"}
    ],
    "order_type": "Market",
    "time_in_force": "Day"
  }'
```

**Speed**: ~1-2 seconds (vs 30-90 seconds via chat)

### 🚀 **Option 2: Use Streaming Chat**

The streaming endpoint (`/api/v1/chat/stream`) provides faster feedback:

```typescript
// Frontend: Use streaming instead of regular chat
for await (const chunk of apiClient.streamChatMessage(message)) {
  // Display chunks as they arrive
  if (chunk.type === "text") {
    console.log(chunk.content); // Immediate feedback
  }
}
```

**Speed**: First chunk arrives in ~5-10 seconds (vs 30-60 seconds full response)

### 📝 **Option 3: Simplify Instructions for Fast Mode**

Create a "fast execution mode" with shorter instructions:

1. **Create `instruction_fast_prompt.md`** (200-300 lines max):
   - Core rules only
   - Skip verbose examples
   - Focus on execution logic

2. **Add environment variable**:
   ```bash
   FAST_MODE=true  # Use shorter instructions
   ```

3. **Update `get_claude_agent()` to use fast mode when flag is set**

**Expected speed improvement**: 30-50% faster responses

### ⚙️ **Option 4: Optimize Agent Behavior**

#### A. Use Parallel API Calls
Currently the agent may make sequential calls. Optimize instructions to:
- Make parallel calls for independent data (quotes, positions, balances)
- Batch requests where possible
- Cache frequently accessed data

#### B. Use Faster Model for Simple Tasks
```python
# In get_claude_agent(), use Haiku for simple requests
if is_simple_request(user_message):
    model = "anthropic:claude-3-5-haiku-20241022"  # Faster, cheaper
else:
    model = "anthropic:claude-sonnet-4-20250514"  # More capable
```

#### C. Reduce Instruction File Size
- Move verbose examples to separate documentation
- Keep only essential rules in main prompt
- Use references: "See docs/X.md for examples"

**Expected speed improvement**: 20-40% faster

### 🔧 **Option 5: Cache Frequently Used Data**

Add caching for:
- Account balances (cache 5-10 seconds)
- Option chains (cache 30-60 seconds)
- Market status (cache 1-2 minutes)

### 📊 **Option 6: Optimize MCP Tool Calls**

1. **Batch multiple requests** where API supports it
2. **Reduce timeout values** for faster failures
3. **Use connection pooling** for TastyTrade session

## Recommended Approach

### For **Order Placement** (Fastest):
✅ **Use direct `/api/v1/place-order` endpoint**
- Frontend: `apiClient.placeOrder(legs, options)`
- Bypasses chat/agent entirely
- ~1-2 second execution

### For **Analysis + Order Placement**:
✅ **Use streaming chat + parallel data fetching**
- Stream analysis results
- Pre-fetch required data while agent thinks
- Execute order via direct endpoint after confirmation

### For **Complex Analysis**:
✅ **Keep current setup but optimize**
- Reduce instruction file size
- Use faster model for simple queries
- Add caching layer

## Implementation Priority

1. **High Impact, Low Effort**: Use direct order endpoint for executions
2. **High Impact, Medium Effort**: Implement streaming chat
3. **Medium Impact, Medium Effort**: Create fast mode instructions
4. **Medium Impact, High Effort**: Optimize instruction file structure
5. **Low Impact, High Effort**: Add caching layer

## Expected Speed Improvements

| Optimization | Current | Optimized | Improvement |
|-------------|---------|-----------|-------------|
| Direct order endpoint | 30-90s | 1-2s | **95% faster** |
| Streaming chat | 30-60s | 5-10s (first chunk) | **80% faster** |
| Fast mode instructions | 30-60s | 15-35s | **40% faster** |
| Faster model (Haiku) | 30-60s | 15-30s | **50% faster** |
| Parallel API calls | 30-60s | 20-40s | **30% faster** |

## Quick Wins

1. **Immediate**: Frontend uses `apiClient.placeOrder()` directly for confirmed trades
2. **This week**: Implement streaming chat for analysis
3. **Next sprint**: Create fast mode instruction file
4. **Future**: Optimize instruction structure and caching

