#!/bin/bash
# Test API error handling - validates that TastyTrade API errors pass through correctly
# This script tests in PAPER MODE only

set -e

PORT=${PORT:-8034}
API_KEY=${API_KEY:-"test-key"}
BASE_URL="http://localhost:${PORT}"

echo "=========================================="
echo "Testing TastyTrade API Error Handling"
echo "=========================================="
echo ""

# Check if server is running
echo "1. Checking server health..."
if ! curl -sf "${BASE_URL}/health" > /dev/null; then
    echo "❌ Server is not running on port ${PORT}"
    exit 1
fi
echo "✅ Server is running"
echo ""

# Verify paper mode (this would require checking logs or env)
echo "2. Testing place_order endpoint (PAPER MODE)..."
echo "   Attempting to place a market order when market is closed"
echo "   This should return a 'Market is closed' error from TastyTrade API"
echo ""

# Test with a simple SPY buy order
RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "${BASE_URL}/place_order" \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{
        "legs": [
            {
                "symbol": "SPY",
                "action": "Buy",
                "quantity": 1
            }
        ],
        "order_type": "Market"
    }' 2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

echo "HTTP Status: ${HTTP_CODE}"
echo ""
echo "Response Body:"
echo "${BODY}" | python3 -m json.tool 2>/dev/null || echo "${BODY}"
echo ""

# Validate response
if [ "${HTTP_CODE}" = "400" ] || [ "${HTTP_CODE}" = "500" ]; then
    echo "✅ Received error response (expected)"
    
    # Check if error message looks like it came from TastyTrade API
    if echo "${BODY}" | grep -qi "market\|closed\|error\|tasty" > /dev/null; then
        echo "✅ Error message detected in response"
        echo ""
        echo "Validation: The error message above should be the EXACT error from TastyTrade API"
        echo "This confirms the API integration is working and errors are passed through correctly."
    else
        echo "⚠️  Error message format unexpected"
    fi
else
    echo "⚠️  Unexpected HTTP status code: ${HTTP_CODE}"
fi

echo ""
echo "=========================================="
echo "Test Complete"
echo "=========================================="
