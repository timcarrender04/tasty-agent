# TastyTrade Token Management Guide

Based on the [TastyTrade API FAQ](https://developer.tastytrade.com/faq/) and our implementation.

## How Tokens Work

### Access Tokens (Automatic - You Don't Touch These)

**Per [TastyTrade FAQ](https://developer.tastytrade.com/faq/):**
> "Access tokens last 15 minutes and must be sent with every request in the `Authorization` header."

- **Lifespan**: 15 minutes
- **Auto-refresh**: The server automatically refreshes them using your refresh token
- **You never need to manually refresh** - it's handled by `session.refresh()`

### Refresh Tokens (Set Once, Use Forever)

- **Lifespan**: Long-lived (should work indefinitely)
- **Purpose**: Used to get new access tokens when they expire
- **Setup**: You provide this once when configuring credentials
- **Renewal**: Only needed if revoked

## Automatic Token Refresh

The server handles all token refresh automatically:

1. **Background Task**: Runs every 5 minutes
   - Checks all active sessions
   - Refreshes access tokens before they expire (10-minute buffer)
   - Uses your refresh token behind the scenes

2. **On-Demand Refresh**: When making API calls
   - Checks if access token expires within 5 seconds
   - Automatically refreshes if needed
   - Uses your refresh token to get a new access token

3. **No Manual Intervention**: You never need to manually refresh tokens

## When Refresh Tokens Get Revoked

According to the [TastyTrade FAQ](https://developer.tastytrade.com/faq/), refresh tokens can be revoked if:

1. **You manually revoke it** in TastyTrade OAuth settings
2. **OAuth app is deleted or modified**
3. **Too many failed login attempts** - IP gets blocked for 8 hours
4. **Security reasons** - TastyTrade revokes it

## Error: "Grant Revoked"

If you see `invalid_grant` or `Grant revoked` errors:

### What It Means
Your refresh token is invalid or has been revoked. This is **not normal** - refresh tokens should work indefinitely.

### How to Fix

1. **Check TastyTrade OAuth Settings:**
   - Visit: https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications
   - Verify your OAuth app is still active
   - Check if Personal OAuth Grants are still valid

2. **Get a New Refresh Token:**
   - Create a new "Personal OAuth Grant" in your OAuth app
   - Copy the new refresh token

3. **Update Credentials:**
   ```bash
   curl -X POST https://tasty.gammabox.app/api/v1/credentials \
     -H "Content-Type: application/json" \
     -d '{
       "api_key": "YOUR_API_KEY",
       "client_secret": "YOUR_CLIENT_SECRET",
       "refresh_token": "YOUR_NEW_REFRESH_TOKEN"
     }'
   ```

4. **Verify It Works:**
   ```bash
   curl -X GET "https://tasty.gammabox.app/api/v1/balances?account_id=YOUR_ACCOUNT_ID" \
     -H "X-API-Key: YOUR_API_KEY"
   ```

## Common Issues from TastyTrade FAQ

### IP Blocking (8-Hour Block)

**From [TastyTrade FAQ](https://developer.tastytrade.com/faq/):**
> "tastytrade will block your IP address outright if we receive too many failed login attempts within a short period of time."

**Symptoms:**
- HTTP requests timing out
- Can't connect to any endpoints

**Solution:**
- Wait 8 hours, or
- Contact api.support@tastytrade.com to request unblocking

### Invalid Credentials

**From [TastyTrade FAQ](https://developer.tastytrade.com/faq/):**
> "This error occurs when you are entering your username/password wrong during login. Be sure that you are hitting the correct environment."

**Check:**
- Sandbox: `https://api.cert.tastyworks.com`
- Production: `https://api.tastyworks.com`

### Unauthorized Errors

**From [TastyTrade FAQ](https://developer.tastytrade.com/faq/):**
> "This error occurs when you don't have a valid access token."

**Solution:**
- The server should auto-refresh access tokens
- If you see this, check if refresh token is valid
- Verify credentials are configured correctly

## Best Practices

1. **Set refresh token once** - It should work indefinitely
2. **Don't manually refresh** - The server handles it automatically
3. **Monitor for "Grant revoked" errors** - This indicates refresh token issue
4. **Update credentials if revoked** - Get new refresh token from TastyTrade
5. **Avoid too many failed attempts** - Can trigger 8-hour IP block

## How Our Server Handles Tokens

### Automatic Session Refresh

```python
# Background task runs every 5 minutes
async def keep_sessions_alive():
    # Checks all sessions
    # Refreshes access tokens before they expire (10-min buffer)
    # Uses refresh token automatically
```

### On-Demand Refresh

```python
# When making API calls
def get_valid_session(api_key):
    session = api_key_sessions[api_key].session
    if session.expires_soon():
        session.refresh()  # Uses refresh token automatically
    return session
```

### Error Handling

- Detects "Grant revoked" errors
- Clears invalid sessions
- Provides clear error messages
- Suggests updating credentials

## Summary

✅ **You provide refresh token ONCE**  
✅ **Server auto-refreshes access tokens every 15 minutes**  
✅ **No manual token management needed**  
❌ **Only update refresh token if it's revoked**

**References:**
- [TastyTrade API FAQ](https://developer.tastytrade.com/faq/)
- [TastyTrade OAuth Settings](https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications)

