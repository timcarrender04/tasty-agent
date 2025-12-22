# Docker Setup for Tasty Agent

This guide explains how to run the Tasty Agent services using Docker and Docker Compose.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+

## Quick Start

1. **Create a `.env` file** with your TastyTrade credentials:

```bash
# Copy the example and edit with your values
cp .env.example .env

# Required: TastyTrade OAuth credentials
TASTYTRADE_CLIENT_SECRET=your_client_secret_here
TASTYTRADE_REFRESH_TOKEN=your_refresh_token_here

# Optional: Specific account ID
TASTYTRADE_ACCOUNT_ID=your_account_id

# Required for HTTP server: API key for authentication
API_KEY=your-secure-api-key-here

# Optional: Server configuration
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=*
```

2. **Start the services**:

```bash
# Start both HTTP and MCP servers
docker-compose up -d

# Or start only the HTTP server
docker-compose up -d http-server

# Or start only the MCP server
docker-compose up -d mcp-server
```

3. **Check logs**:

```bash
# View all logs
docker-compose logs -f

# View HTTP server logs
docker-compose logs -f http-server

# View MCP server logs
docker-compose logs -f mcp-server
```

4. **Stop the services**:

```bash
docker-compose down
```

## Services

### HTTP Server

The HTTP server provides a REST API for multiple clients/kiosks to connect.

- **Port**: 8000 (configurable via `PORT` env var)
- **Health Check**: `http://localhost:8000/health`
- **API Endpoints**: All endpoints under `/api/v1/` require `X-API-Key` header

**Example request**:
```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/balances?account_id=YOUR_ACCOUNT_ID
```

### MCP Server

The MCP server provides Model Context Protocol tools for LLM integration.

- **Communication**: Via stdio (no ports exposed)
- **Usage**: Typically used with MCP-compatible clients

## Environment Variables

### Required

- `TASTYTRADE_CLIENT_SECRET`: Your TastyTrade OAuth client secret
- `TASTYTRADE_REFRESH_TOKEN`: Your TastyTrade OAuth refresh token
- `API_KEY`: (HTTP server only) API key for authenticating requests

### Optional

- `TASTYTRADE_ACCOUNT_ID`: Specific account ID (defaults to first account)
- `TASTYTRADE_CREDENTIALS_JSON`: Multi-API-key configuration (JSON format)
- `HOST`: Server host (default: `0.0.0.0`)
- `PORT`: Server port (default: `8000`)
- `CORS_ORIGINS`: CORS allowed origins (default: `*`)
- `RELOAD`: Enable auto-reload for development (default: `false`)

## Multi-API-Key Configuration

For the HTTP server, you can configure multiple API keys using `TASTYTRADE_CREDENTIALS_JSON`:

```json
{
  "api_key_1": {
    "client_secret": "secret1",
    "refresh_token": "token1"
  },
  "api_key_2": {
    "client_secret": "secret2",
    "refresh_token": "token2"
  }
}
```

Or create a `credentials.json` file in the project root with the same format.

## Building the Image

```bash
# Build the image
docker-compose build

# Or build without cache
docker-compose build --no-cache
```

## Development

For development with auto-reload:

```bash
# Set RELOAD=true in .env or override in docker-compose
RELOAD=true docker-compose up http-server
```

## Troubleshooting

### Check container status
```bash
docker-compose ps
```

### View container logs
```bash
docker-compose logs http-server
docker-compose logs mcp-server
```

### Access container shell
```bash
docker-compose exec http-server /bin/bash
```

### Restart a service
```bash
docker-compose restart http-server
```

### Rebuild after code changes
```bash
docker-compose up -d --build http-server
```

## Security Notes

1. **Never commit `.env` files** - They contain sensitive credentials
2. **Use strong API keys** - Generate secure random strings for `API_KEY`
3. **Restrict CORS origins** - Set `CORS_ORIGINS` to specific domains in production
4. **Use secrets management** - In production, consider using Docker secrets or a secrets manager

## Production Deployment

For production:

1. Use environment-specific `.env` files or secrets management
2. Set `RELOAD=false` (default)
3. Configure proper `CORS_ORIGINS` instead of `*`
4. Use a reverse proxy (nginx, traefik) for SSL/TLS
5. Set up proper logging and monitoring
6. Use resource limits in docker-compose.yml

Example production docker-compose override:
```yaml
services:
  http-server:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

