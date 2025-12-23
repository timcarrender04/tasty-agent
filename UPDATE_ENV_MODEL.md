# Update .env File to Use Claude Haiku 3.5

## Issue Found
Your `.env` file has `MODEL_IDENTIFIER=anthropic:claude-sonnet-4-20250514` which is overriding the code default.

## Solution
Update your `.env` file to use Haiku 3.5:

```bash
# Change this line in .env:
MODEL_IDENTIFIER=anthropic:claude-sonnet-4-20250514

# To this:
MODEL_IDENTIFIER=anthropic:claude-3-5-haiku-20241022
```

## After Updating
Restart the server:
```bash
docker-compose restart http-server
```

## Verify
Check the logs to confirm:
```bash
docker-compose logs http-server | grep -i "model\|haiku"
```

You should see:
```
Using Claude model: anthropic:claude-3-5-haiku-20241022
Created Claude agent ... with model anthropic:claude-3-5-haiku-20241022
```

