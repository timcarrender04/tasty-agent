++ b/backend_server/tasty-agent/docs/IP_EXTRACTION_EXPLANATION.md
# IP Extraction Explanation

## Where is `150.221.204.134` coming from?

Based on log analysis, the IP `150.221.204.134` is coming from **HTTP headers** that are being sent by the client or an intermediate proxy:

```
X-Forwarded-For: 150.221.204.134
X-Real-IP: 150.221.204.134
```

However, the actual direct connection IP is:
```
request.client.host: 192.168.144.1
```

## The Problem

The old code was prioritizing headers (`X-Forwarded-For`, `X-Real-IP`) over the direct connection IP (`request.client.host`). This meant:

1. Client sends request with headers containing `150.221.204.134`
2. Server extracts IP from headers first → gets `150.221.204.134`
3. Database expects `100.77.64.79` (Tailscale IP)
4. Mismatch → 403 error

## The Solution

The code has been updated to **always use `request.client.host` first** for local deployments. This:

1. Prevents IP spoofing via headers
2. Uses the actual connection IP
3. Should match the Tailscale IP when connecting via Tailscale network

## Current Behavior (After Fix)

1. Extract IP from `request.client.host` directly → `192.168.144.1` (or `100.77.64.79` if via Tailscale)
2. Ignore headers completely for local deployments
3. Only check headers if `request.client` is unavailable (shouldn't happen)

## Why Headers Contain Wrong IP

The `X-Forwarded-For` and `X-Real-IP` headers containing `150.221.204.134` could be coming from:

1. **Client-side**: The browser/client might be setting these headers
2. **Network Gateway**: An intermediate router/gateway adding headers
3. **Proxy Software**: Some proxy software on the client side
4. **Development Tool**: A development proxy or tool setting these headers

Since this is a local deployment, these headers should be ignored.

## Verification

After restarting the service, check logs for:
```
Extracted IP from request.client.host (ignoring headers): 192.168.144.1
```

Or if connecting via Tailscale:
```
Extracted IP from request.client.host (ignoring headers): 100.77.64.79
```

The IP should now match the database configuration.


