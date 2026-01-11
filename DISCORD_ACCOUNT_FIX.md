# Discord Account Selection Fix

## Issue
Discord bot was showing account `5WI20677` instead of `5WI12958` (from `TASTYTRADE_ACCOUNT_ID` env var).

## Root Cause
The Claude agent was cached with `account_id=None`, which caused:
1. MCP server to default to first account (`5WI20677`)
2. Cached agent to persist the wrong account across requests

## Fixes Applied

### 1. Balance Endpoint (`/api/v1/balances`) ✅
- Updated `get_session_and_account()` to check `TASTYTRADE_ACCOUNT_ID` env var
- Now correctly uses `5WI12958` when account_id not specified

### 2. Chat Endpoint (`/api/v1/chat`) ✅
- Updated `get_claude_agent()` to resolve `account_id=None` to actual account
- Uses `get_account_context()` which respects `TASTYTRADE_ACCOUNT_ID` env var
- Cache key now uses resolved account_id: `(api_key, "5WI12958")`
- MCP server now receives `TASTYTRADE_ACCOUNT_ID=5WI12958` in environment

## Verification
After restart, logs show:
```
Using account 5WI12958 from TASTYTRADE_ACCOUNT_ID environment variable
```

## Next Steps
1. ✅ Container restarted - all caches cleared
2. Next Discord chat request will create new agent with correct account
3. Test by asking for balance/details in Discord - should show `5WI12958`

## If Still Showing Wrong Account
If Discord still shows `5WI20677` after restart:
1. Wait 1-2 minutes for any in-flight requests to complete
2. Send a new `/chat` command in Discord
3. The new agent will be created with correct account_id
