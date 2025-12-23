# TastyTrade MCP Server Troubleshooting Guide

## Common Issues and Solutions

### Issue: "TastyTrade Connection Issue" - MCP Server Not Accessible

**Symptoms:**
- MCP server container is running (`docker-compose ps` shows `tasty-agent-mcp` as `Up`)
- Logs show successful authentication
- But MCP client (Cursor, Claude Desktop, etc.) cannot connect

**Root Cause:**
MCP servers communicate via **stdio** (standard input/output), which doesn't work well with Docker containers in detached mode. The Docker container is running but waiting for stdio input that never arrives.

**Solutions:**

#### Option 1: Run MCP Server Directly (Recommended for MCP Clients)

Instead of running in Docker, run the MCP server directly on your host:

```bash
# Make sure you have the environment variables set
export TASTYTRADE_CLIENT_SECRET=your_client_secret
export TASTYTRADE_REFRESH_TOKEN=your_refresh_token
export TASTYTRADE_ACCOUNT_ID=your_account_id  # optional

# Run the MCP server directly
uv run tasty-agent stdio
# OR
python -m tasty_agent.server
```

**For Cursor/Claude Desktop MCP Configuration:**

Update your MCP client config (e.g., `~/.cursor/mcp.json` or Claude Desktop config) to run the server directly:

```json
{
  "mcpServers": {
    "tastytrade": {
      "command": "uv",
      "args": ["run", "tasty-agent", "stdio"],
      "env": {
        "TASTYTRADE_CLIENT_SECRET": "your_client_secret",
        "TASTYTRADE_REFRESH_TOKEN": "your_refresh_token",
        "TASTYTRADE_ACCOUNT_ID": "your_account_id"
      }
    }
  }
}
```

#### Option 2: Use HTTP Server Instead

The HTTP server works perfectly in Docker and provides the same functionality:

```bash
# Start HTTP server
docker-compose up -d http-server

# Test connection
curl -H "X-API-Key: your-api-key" http://localhost:8033/api/v1/balances
```

#### Option 3: Run MCP Server in Interactive Mode (Not Recommended)

If you must use Docker, run it in interactive mode (not detached):

```bash
# Stop the detached container
docker-compose stop mcp-server

# Run interactively (this won't work well with MCP clients)
docker-compose run --rm mcp-server
```

**Note:** This won't work well because MCP clients need to spawn the process themselves.

### Issue: "Grant Revoked" or Authentication Errors

**Symptoms:**
- Error: `invalid_grant` or `Grant revoked`
- Authentication fails on startup

**Solution:**
1. Check your refresh token at: https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications
2. Create a new "Personal OAuth Grant" if needed
3. Update your `.env` file or environment variables with the new refresh token
4. Restart the service: `docker-compose restart mcp-server`

See `TOKEN_MANAGEMENT.md` for detailed token management instructions.

### Issue: Container Won't Start

**Check logs:**
```bash
docker-compose logs mcp-server
```

**Common causes:**
1. Missing environment variables - check `.env` file
2. Invalid credentials - verify `TASTYTRADE_CLIENT_SECRET` and `TASTYTRADE_REFRESH_TOKEN`
3. Network issues - check if you can reach `api.tastyworks.com`

### Issue: HTTP Server Unhealthy

**Check health:**
```bash
# Check container status
docker-compose ps

# Check health endpoint
curl http://localhost:8033/health

# Check logs
docker-compose logs http-server
```

**Common fixes:**
1. Restart the service: `docker-compose restart http-server`
2. Check if port 8033 is available: `netstat -tuln | grep 8033`
3. Verify API_KEY is set in `.env`

### Testing MCP Server Connection

**Test if server can authenticate:**
```bash
# From host (if running directly)
uv run tasty-agent stdio

# From Docker container
docker-compose exec mcp-server python -c "from tasty_agent.server import mcp_app; print('MCP app loaded')"
```

**Test HTTP server:**
```bash
# Health check
curl http://localhost:8033/health

# Get balances (requires API_KEY)
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8033/api/v1/balances?account_id=YOUR_ACCOUNT_ID"
```

### Environment Variables Checklist

Ensure these are set in your `.env` file or environment:

**Required for MCP Server:**
- ✅ `TASTYTRADE_CLIENT_SECRET`
- ✅ `TASTYTRADE_REFRESH_TOKEN`
- ⚠️ `TASTYTRADE_ACCOUNT_ID` (optional, defaults to first account)

**Required for HTTP Server:**
- ✅ All MCP server variables above
- ✅ `API_KEY` (for API authentication)

**Optional:**
- `HOST` (default: `0.0.0.0`)
- `PORT` (default: `8033`)
- `CORS_ORIGINS` (default: `*`)

### Quick Diagnostic Commands

```bash
# Check container status
docker-compose ps

# View all logs
docker-compose logs -f

# View MCP server logs only
docker-compose logs -f mcp-server

# View HTTP server logs only
docker-compose logs -f http-server

# Check environment variables in container
docker-compose exec mcp-server env | grep TASTYTRADE

# Restart services
docker-compose restart

# Rebuild and restart
docker-compose up -d --build
```

### Still Having Issues?

1. **Check TastyTrade API Status**: Visit https://developer.tastytrade.com/
2. **Verify Credentials**: Double-check your OAuth app settings
3. **Review Logs**: Look for specific error messages in container logs
4. **Test Network**: Ensure you can reach `api.tastyworks.com`
5. **Check IP Blocking**: Too many failed attempts can block your IP for 8 hours (see `TOKEN_MANAGEMENT.md`)

