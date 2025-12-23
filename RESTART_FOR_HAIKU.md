# Restart Server to Use Claude Haiku 3.5

## Issue
The agents are cached in memory and still using Claude Sonnet 4. You need to restart the server to clear the cache and use the new model.

## Solution

### Option 1: Restart Docker Container (Recommended)
```bash
docker-compose restart http-server
```

Or if using docker directly:
```bash
docker restart tasty-agent-http
```

### Option 2: Rebuild and Restart (If needed)
```bash
docker-compose down
docker-compose up -d
```

## Verification

After restarting, check the logs to confirm the new model is being used:
```bash
docker-compose logs http-server | grep -i "model\|haiku\|claude"
```

You should see log messages like:
```
Created Claude agent for API key ... with model anthropic:claude-3-5-haiku-20241022
```

## Code Changes Made

1. ✅ Updated default model to `anthropic:claude-3-5-haiku-20241022` in `http_server.py`
2. ✅ Updated default model in `docker-compose.yml`
3. ✅ Added automatic cache invalidation when model identifier changes
4. ✅ Added model identifier to log messages for verification

The cache invalidation will automatically clear cached agents if the model identifier changes in the future, but you still need to restart once to pick up the new default.

