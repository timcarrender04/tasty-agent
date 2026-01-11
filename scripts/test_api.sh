++ b/backend_server/tasty-agent/scripts/test_api.sh
#!/bin/bash

# Test script for TastyTrade HTTP API
# Usage: ./test_api.sh

BASE_URL="http://localhost:8000"
API_KEY="5WI12958"
ACCOUNT_ID="5WI12958"

echo "=========================================="
echo "Testing TastyTrade HTTP API"
echo "=========================================="
echo ""

# Test 1: Health Check
echo "1. Testing Health Check..."
curl -s -X GET "${BASE_URL}/health" | python3 -m json.tool
echo ""
echo ""

# Test 2: List Credentials
echo "2. Testing List Credentials..."
curl -s -X GET "${BASE_URL}/api/v1/credentials" | python3 -m json.tool
echo ""
echo ""

# Test 3: Get Account Balances
echo "3. Testing Get Account Balances..."
curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool
echo ""
echo ""

# Test 4: Get Positions
echo "4. Testing Get Positions..."
curl -s -X GET "${BASE_URL}/api/v1/positions?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool
echo ""
echo ""

# Test 5: Get Live Orders
echo "5. Testing Get Live Orders..."
curl -s -X GET "${BASE_URL}/api/v1/live-orders?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool | head -50
echo ""
echo ""

# Test 6: Get Market Status
echo "6. Testing Get Market Status..."
curl -s -X GET "${BASE_URL}/api/v1/market-status" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool
echo ""
echo ""

# Test 7: Get Current Time
echo "7. Testing Get Current Time..."
curl -s -X GET "${BASE_URL}/api/v1/current-time" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool
echo ""
echo ""

echo "=========================================="
echo "Tests Complete"
echo "=========================================="



