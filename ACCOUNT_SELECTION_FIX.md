# Account Selection Fix for Chat Endpoint

## Problem
The Discord chat was showing account `5WI20677` instead of `5WI12958` (from `TASTYTRADE_ACCOUNT_ID` env var).

## Root Cause
1. **Balance endpoint** (`/api/v1/balances`) - Fixed ✅
   - `get_session_and_account()` now checks `TASTYTRADE_ACCOUNT_ID` env var

2. **Chat endpoint** (`/api/v1/chat`) - Fixed ✅
   - `get_claude_agent()` was using `account_id=None` as cache key
   - When `account_id=None`, it removed `TASTYTRADE_ACCOUNT_ID` from env passed to MCP server
   - MCP server defaulted to first account (`5WI20677`)

## Solution
Updated `get_claude_agent()` to:
1. When `account_id=None`, resolve to actual account using `get_account_context()` 
   - This respects `TASTYTRADE_ACCOUNT_ID` env var
2. Use resolved `account_id` for cache key: `(api_key, resolved_account_id)`
3. Pass resolved `account_id` to MCP server in environment

## Files Modified
- `tasty_agent/http_server.py`:
  - `get_claude_agent()` - Now resolves default account from env var
  - `get_session_and_account()` - Already fixed to check env var
  - `get_account_context()` - Already fixed to check env var

## Testing
After restart, chat endpoint should now use account `5WI12958` from environment variable.

## Cache Behavior
- Agent cache key is now `(api_key, resolved_account_id)`
- Old cached agents with `(api_key, None)` will be ignored
- New agents will be created with correct account_id
- Container restart clears in-memory cache
