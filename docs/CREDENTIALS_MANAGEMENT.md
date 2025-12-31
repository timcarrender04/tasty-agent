# Credentials Management

## Overview

The tasty-agent server uses a SQLite database (`credentials.db`) to store TastyTrade API credentials. Credentials are managed via the HTTP API, eliminating the need for JSON files.

## Database Location

- **Local**: `/home/ert/projects/infrastructure/tasty-agent/credentials.db`
- **Docker**: `/app/credentials.db`

## Adding Credentials

### Via API Endpoint (Recommended)

Use the `POST /api/v1/credentials` endpoint to add or update credentials:

```bash
curl -X POST https://tasty.gammabox.app/api/v1/credentials \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_existing_api_key" \
  -d '{
    "api_key": "your_api_key",
    "client_secret": "your_client_secret",
    "refresh_token": "your_refresh_token"
  }'
```

### Via Environment Variables (Initial Setup)

For initial setup, you can use environment variables:

```bash
# Single credential (uses "default" as API key)
export TASTYTRADE_CLIENT_SECRET="your_client_secret"
export TASTYTRADE_REFRESH_TOKEN="your_refresh_token"
export API_KEY="default"  # Optional, defaults to "default"

# Multiple credentials via JSON string
export TASTYTRADE_CREDENTIALS_JSON='{
  "api_key_1": {
    "client_secret": "secret1",
    "refresh_token": "token1"
  },
  "api_key_2": {
    "client_secret": "secret2",
    "refresh_token": "token2"
  }
}'
```

## Listing Credentials

```bash
curl -X GET https://tasty.gammabox.app/api/v1/credentials \
  -H "X-API-Key: your_api_key"
```

## Deleting Credentials

```bash
curl -X DELETE https://tasty.gammabox.app/api/v1/credentials/{api_key} \
  -H "X-API-Key: your_api_key"
```

## How GammaBox_Kiosk_v2 Connects

The GammaBox_Kiosk_v2 application connects to the tasty-agent server via HTTP API:

### Configuration

Set these environment variables in `config/var.txt`:

```bash
# TastyTrade HTTP API Configuration
NEXT_PUBLIC_TASTY_HTTP_API_KEY=your_api_key_here
NEXT_PUBLIC_TASTY_HTTP_API_URL=https://tasty.gammabox.app  # Optional, defaults to this value
```

### Connection Flow

1. **API Key**: The app uses `NEXT_PUBLIC_TASTY_HTTP_API_KEY` as the API key for authentication
2. **Base URL**: Requests go to `NEXT_PUBLIC_TASTY_HTTP_API_URL` (defaults to `https://tasty.gammabox.app`)
3. **Authentication**: All requests include the `X-API-Key` header with the API key
4. **Account ID**: The API key typically corresponds to a TastyTrade account ID

### Registering Credentials from GammaBox_Kiosk_v2

The app can register credentials via the Settings page or programmatically:

```typescript
// From lib/api.ts
await apiClient.addCredentials({
  api_key: "your_api_key",
  client_secret: "your_client_secret",
  refresh_token: "your_refresh_token"
});
```

### API Endpoints Used

The GammaBox_Kiosk_v2 app uses these endpoints:

- `GET /api/v1/balances?account_id={account_id}` - Get account balance
- `GET /api/v1/positions?account_id={account_id}` - Get positions
- `GET /api/v1/transaction-history?account_id={account_id}` - Get transaction history
- `POST /api/v1/place-order` - Place orders
- `POST /api/v1/chat` - Chat with AI agent
- `POST /api/v1/credentials` - Register/update credentials
- And many more...

All requests include the `X-API-Key` header for authentication.

## Migration from JSON Files

If you have an existing `credentials.json` file, credentials can be migrated to the database:

```python
from pathlib import Path
from tasty_agent.database import CredentialsDB, get_db_path

project_root = Path.cwd()
db_path = get_db_path(project_root)
credentials_db = CredentialsDB(db_path)

json_file = project_root / "credentials.json"
if json_file.exists():
    migrated_count = credentials_db.migrate_from_json(json_file)
    print(f"Migrated {migrated_count} credential(s)")
```

After migration, the JSON file can be safely removed or backed up.

## Security Notes

- The database file contains sensitive credentials - ensure proper file permissions
- Never commit `credentials.db` or `credentials.json` to version control
- Use environment variables or API endpoints for credential management in production
- API keys should be unique per user/account for multi-tenant scenarios

