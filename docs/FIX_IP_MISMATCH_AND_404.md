++ b/backend_server/tasty-agent/docs/FIX_IP_MISMATCH_AND_404.md
# Fix for IP Mismatch (403) and Pattern-Alerts 404 Errors

## Problem Summary

### 1. IP Mismatch Error (403 Forbidden)
- **Error**: `IP address mismatch. Client IP: 150.221.204.134 does not match the configured device IP.`
- **Root Cause**: Device's `tailscale_ip` in database is `100.77.64.79` but the client IP being detected is `150.221.204.134`
- **Impact**: All API requests are being rejected with 403 errors
- **Solution**: For local deployments, the IP extraction now uses `request.client.host` directly (✅ FIXED)
- **Note**: If client is connecting from `150.221.204.134`, ensure the device connects via Tailscale (`100.77.64.79`) or update the database to match the actual connection IP

### 2. Pattern-Alerts 404 Errors
- **Error**: `api/v1/user-settings/pattern-alerts:1 Failed to load resource: the server responded with a status of 404`
- **Root Cause**: Frontend is calling `tasty.gammabox.app` (tasty-agent service) but these endpoints are in `webapp_backend`
- **Impact**: Pattern alert settings cannot be loaded or initialized

## Solutions

### Solution 1: Update Device IP in Database

The device's `tailscale_ip` needs to be updated to match the current client IP: `150.221.204.134`

#### Option A: Configure Reverse Proxy (Recommended)

The reverse proxy should forward the Tailscale IP (`100.77.64.79`) in headers. The device's `tailscale_ip` is correctly set to `100.77.64.79`, but the proxy is forwarding the public IP `150.221.204.134`.

**Nginx Configuration Example:**
```nginx
# Add to your reverse proxy configuration
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
# If connecting from Tailscale, ensure the Tailscale IP is used
proxy_set_header X-Forwarded-For 100.77.64.79;
```

**Or use Nginx Proxy Manager:**
- Ensure "Forward Hostname/IP" is set correctly
- Check "X-Forwarded-For" header configuration
- Verify the upstream is correctly identified

#### Option B: Update via API (If device IP changed)

If the device's actual Tailscale IP changed from `100.77.64.79`, update it:

```bash
# Update device IP via PUT request
curl -X PUT "https://api.gammabox.app/api/v1/admin/devices/{device_id}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tailscale_ip": "100.77.64.79"
  }'
```

#### Option C: Update IP Extraction Logic for Local Deployments (✅ IMPLEMENTED)

The IP extraction logic has been updated for local deployments without reverse proxy. The code now:

1. **Uses `request.client.host` directly** (primary method for local setups)
2. Falls back to headers only if `request.client` is not available
3. When checking headers, prefers Tailscale IPs (100.x.x.x) if multiple IPs are present

**Changes made:**
- ✅ Updated `tasty_agent/http_server.py::get_client_ip()` - now prioritizes `request.client.host`
- ✅ Updated `webapp_backend/app/routes/api_keys.py::get_client_ip()` - now prioritizes `request.client.host`

**For local deployments:** The IP should now correctly use the direct client connection IP from `request.client.host`, which should be `100.77.64.79` when connecting via Tailscale.

**If still seeing wrong IP:** 
- Verify the client is connecting via Tailscale network
- Check that `request.client.host` is returning the Tailscale IP (`100.77.64.79`)
- If client is connecting from a different IP, either:
  - Update database: `UPDATE gamma_devices SET tailscale_ip = '150.221.204.134' WHERE device_id = 'YOUR_DEVICE_ID'`
  - OR ensure client connects via Tailscale to use IP `100.77.64.79`

#### Option D: Update via SQL (If device IP actually changed)

If the device's actual Tailscale IP changed from `100.77.64.79`, update it in the database:

```sql
-- Find the device that needs updating (check logs for device_identifier)
SELECT device_id, tailscale_ip, device_identifier 
FROM gamma_devices 
WHERE tailscale_ip = '100.77.64.79' OR device_identifier = 'YOUR_DEVICE_IDENTIFIER';

-- Only update if the device IP actually changed
UPDATE gamma_devices 
SET tailscale_ip = 'NEW_TAILSCALE_IP',
    updated_at = NOW()
WHERE device_identifier = 'YOUR_DEVICE_IDENTIFIER';
```

#### Option C: Auto-detect and Update (Future Enhancement)

Consider adding an endpoint that allows the device to update its own IP when it detects a mismatch:

```python
# This could be added to webapp_backend/app/routes/devices.py
@router.post("/devices/{device_id}/update-ip")
async def update_device_ip_self(
    device_id: str,
    new_ip: str,
    api_key: str = Depends(API_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
):
    # Validate API key
    # Update tailscale_ip if authenticated
    pass
```

### Solution 2: Make IP Validation More Flexible (For Development/Testing)

If you need to allow multiple IPs or make validation less strict temporarily, you can modify the validation logic:

#### Option A: Allow Multiple IPs

Modify `tasty_agent/supabase_client.py` to support multiple IPs:

```python
# In validate_kiosk_api_key method, around line 115
tailscale_ip = row['tailscale_ip']
if tailscale_ip:
    # Support comma-separated list of allowed IPs
    allowed_ips = [ip.strip() for ip in tailscale_ip.split(',')]
    if client_ip.strip() not in allowed_ips:
        logger.error(f"❌ IP MISMATCH: expected one of {allowed_ips}, got '{client_ip}'")
        return None
```

#### Option B: Environment Variable to Disable IP Validation

Add a flag to disable IP validation for development:

```python
# In tasty_agent/supabase_client.py
import os

# Around line 114
skip_ip_validation = os.getenv("DISABLE_IP_VALIDATION", "false").lower() == "true"

if tailscale_ip and not skip_ip_validation:
    # ... existing validation code ...
elif skip_ip_validation:
    logger.warning(f"⚠️  IP validation is DISABLED (DISABLE_IP_VALIDATION=true)")
```

### Solution 3: Fix Pattern-Alerts 404 Errors

The pattern-alerts endpoints exist in `webapp_backend` but the frontend is calling `tasty.gammabox.app`. You need to:

#### Option A: Update Frontend to Use Correct Backend

The frontend should call `api.gammabox.app` (webapp_backend) instead of `tasty.gammabox.app` (tasty-agent) for user-settings endpoints.

Check the `getApiUrl()` function in your frontend and ensure user-settings requests go to the correct backend.

#### Option B: Add Proxy Route in tasty-agent

Add a proxy route in `tasty-agent` to forward user-settings requests to `webapp_backend`:

```python
# In tasty_agent/http_server.py
@router.get("/api/v1/user-settings/{path:path}")
@router.post("/api/v1/user-settings/{path:path}")
async def proxy_user_settings(
    request: Request,
    path: str,
):
    """Proxy user-settings requests to webapp_backend"""
    import httpx
    webapp_backend_url = os.getenv("WEBAPP_BACKEND_URL", "http://webapp_backend:8000")
    target_url = f"{webapp_backend_url}/api/v1/user-settings/{path}"
    
    # Forward request with headers
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=request.method,
            url=target_url,
            headers=dict(request.headers),
            content=await request.body(),
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
```

## Immediate Action Required

1. ✅ **IP extraction logic updated** - now uses `request.client.host` directly for local deployments (Solution 1 Option C - DONE)
2. **Verify the client connection** - ensure client connects via Tailscale network to use IP `100.77.64.79`
3. **If client IP is still `150.221.204.134`**: Either update database to match, or ensure Tailscale connection
4. **Fix the pattern-alerts routing** using Solution 3 (Option A or B)

**Note**: For local deployments, the IP extraction now uses the direct client connection. If `request.client.host` returns `150.221.204.134`, that means the client is connecting from that IP. You can either:
- Update the database to accept `150.221.204.134` as the valid IP
- OR ensure the client connects via Tailscale network to use `100.77.64.79`

## Verification

After applying the fixes:

1. Check logs - IP mismatch errors should be gone
2. Test API endpoints - they should return 200 instead of 403
3. Test pattern-alerts endpoints - they should return data instead of 404

## Notes

- The device's Tailscale IP is correctly configured: `100.77.64.79`
- For local deployments, IP extraction now uses `request.client.host` directly
- If `request.client.host` returns `150.221.204.134`, it means the client is connecting from that IP address
- **Solution**: Ensure client connects via Tailscale network, OR update database to accept the actual client IP


