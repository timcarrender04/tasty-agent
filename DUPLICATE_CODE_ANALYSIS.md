++ b/backend_server/tasty-agent/DUPLICATE_CODE_ANALYSIS.md
# Duplicate Code Analysis

This document identifies duplicate code patterns found in the `tasty-agent` codebase.

## Summary of Duplicates

### 1. **Session Creation Pattern** (HIGH PRIORITY)
**Files:** `tasty_agent/server.py`, `tasty_agent/http_server.py`, `background.py`, `get_today_trades.py`

**Issue:** The pattern for creating TastyTrade sessions is duplicated across multiple files:

```python
# Pattern repeated in 4+ locations:
paper_mode = os.getenv("TASTYTRADE_PAPER_MODE", "false").lower() in ("true", "1", "yes")
sandbox_mode = os.getenv("TASTYTRADE_SANDBOX", "false").lower() in ("true", "1", "yes")
is_sandbox = paper_mode or sandbox_mode
session = Session(client_secret, refresh_token, is_test=is_sandbox)
```

**Locations:**
- `tasty_agent/server.py:140-153` (in `lifespan` function)
- `tasty_agent/http_server.py:635-636` (in `get_api_key_context`)
- `tasty_agent/http_server.py:703-704` (in `get_api_key_context`)
- `tasty_agent/http_server.py:1037-1038` (in credential validation)
- `background.py:32-35` (in `check_market_open`)
- `get_today_trades.py:32-47` (in `get_todays_trades`)

**Note:** `http_server.py` has a function `is_sandbox_mode()` (line 83) that encapsulates this logic, but other files duplicate it.

**Recommendation:** 
- Extract to a shared utility function: `create_session(client_secret: str, refresh_token: str, is_test: bool | None = None) -> Session`
- Update all call sites to use this function

---

### 2. **Credential Unpacking Logic** (MEDIUM PRIORITY)
**Files:** `tasty_agent/http_server.py`

**Issue:** The logic for unpacking credentials from tuples/dicts is duplicated in multiple places within the same file:

```python
# Pattern repeated 3+ times in http_server.py:
if isinstance(creds, dict):
    client_secret = creds.get('client_secret')
    refresh_token = creds.get('refresh_token')
elif isinstance(creds, (tuple, list)) and len(creds) >= 2:
    client_secret, refresh_token = creds[0], creds[1]
else:
    client_secret, refresh_token = creds
```

**Locations:**
- `tasty_agent/http_server.py:677-687` (in `get_api_key_context`)
- `tasty_agent/http_server.py:1020-1026` (in credential validation endpoint)

**Recommendation:**
- Create helper function: `unpack_credentials(creds: dict | tuple | list) -> tuple[str, str]`
- This will make the code more maintainable and reduce error risk

---

### 3. **get_valid_session Functions** (LOW PRIORITY - Different Implementations)
**Files:** `tasty_agent/server.py`, `tasty_agent/http_server.py`

**Issue:** Two different implementations of `get_valid_session` exist:
- `server.py:89` - Takes `Context` parameter (for MCP server)
- `http_server.py:776` - Takes `api_key` parameter (for HTTP server)

**Note:** These serve different purposes (different architectures), so this may be intentional, but the naming could be more specific.

**Recommendation:**
- Consider renaming to `get_valid_session_from_context(ctx: Context)` and `get_valid_session_from_api_key(api_key: str)` for clarity

---

### 4. **Error Handling for Invalid Grants** (MEDIUM PRIORITY)
**Files:** `tasty_agent/http_server.py`

**Issue:** Similar error handling patterns for TastyTrade authentication errors are repeated:

```python
# Pattern repeated multiple times:
if "invalid_grant" in error_msg or "Grant revoked" in error_msg:
    raise HTTPException(
        status_code=401,
        detail=f"TastyTrade refresh token is invalid or revoked..."
    )
else:
    raise HTTPException(
        status_code=500,
        detail=f"Failed to authenticate with Tastytrade: {error_msg}"
    )
```

**Locations:**
- `tasty_agent/http_server.py:656-665` (in `get_api_key_context` for kiosk)
- `tasty_agent/http_server.py:715-726` (in `get_api_key_context` for local)
- `tasty_agent/http_server.py:1042-1054` (in credential validation)

**Recommendation:**
- Create helper function: `handle_tastytrade_auth_error(e: Exception, api_key: str) -> HTTPException`
- Standardize error messages across all locations

---

### 5. **Pagination Helper** (LOW PRIORITY - Already Extracted)
**Files:** `tasty_agent/server.py`, `get_today_trades.py`

**Issue:** A pagination helper function exists in both files:
- `server.py:55-69` - `_paginate` function (uses rate limiter)
- `get_today_trades.py:11-21` - `_paginate` function (no rate limiter)

**Note:** The implementations differ slightly (one uses rate limiter), so they serve different needs.

**Recommendation:**
- Keep as is, but document the difference in comments
- Or make rate_limiter optional parameter in a shared version

---

### 6. **Account Selection Logic** (LOW PRIORITY)
**Files:** `tasty_agent/server.py`, `get_today_trades.py`, `tasty_agent/http_server.py`

**Issue:** Logic for selecting an account by `account_id` is repeated:

```python
if account_id:
    account = next((acc for acc in accounts if acc.account_number == account_id), None)
    if not account:
        # Error handling...
else:
    account = accounts[0]
```

**Locations:**
- `tasty_agent/server.py:171-181`
- `get_today_trades.py:50-57`
- `tasty_agent/http_server.py:640-647` (slightly different pattern)

**Recommendation:**
- Create helper: `select_account(accounts: list[Account], account_id: str | None) -> Account`

---

## Priority Recommendations

1. **HIGH:** Extract session creation logic to shared utility
2. **MEDIUM:** Extract credential unpacking logic
3. **MEDIUM:** Extract error handling for TastyTrade auth errors
4. **LOW:** Consider renaming `get_valid_session` functions for clarity
5. **LOW:** Extract account selection logic (if accounts are used frequently)

## Next Steps

1. Create a new file `tasty_agent/utils/session.py` for session-related utilities
2. Create a new file `tasty_agent/utils/credentials.py` for credential-related utilities
3. Create a new file `tasty_agent/utils/errors.py` for error handling utilities
4. Refactor existing code to use these utilities
5. Update tests to ensure functionality is preserved

