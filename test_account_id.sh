++ b/backend_server/tasty-agent/test_account_id.sh
#!/bin/bash

# Test script for account_id functionality
# Usage: ./test_account_id.sh

BASE_URL="http://localhost:8033"
API_KEY="5WI12958"
ACCOUNT_ID="5WI12958"

echo "=========================================="
echo "Testing Account ID Functionality"
echo "=========================================="
echo ""

# Test 1: Chat with account_id in request body
echo "1. Testing Chat with account_id in request body..."
curl -s -X POST "${BASE_URL}/api/v1/chat" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"check my account balance\",
    \"account_id\": \"${ACCOUNT_ID}\",
    \"tab_id\": \"test-account-123\"
  }" | python3 -m json.tool | head -30
echo ""
echo ""

# Test 2: Chat without account_id (should use default)
echo "2. Testing Chat without account_id (uses default account)..."
curl -s -X POST "${BASE_URL}/api/v1/chat" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"check my account balance\",
    \"tab_id\": \"test-default-123\"
  }" | python3 -m json.tool | head -30
echo ""
echo ""

# Test 3: Chat streaming with account_id
echo "3. Testing Chat Streaming with account_id..."
curl -s -X POST "${BASE_URL}/api/v1/chat/stream" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"what is my cash balance?\",
    \"account_id\": \"${ACCOUNT_ID}\",
    \"tab_id\": \"test-stream-123\"
  }" | head -50
echo ""
echo ""

# Test 4: Balances endpoint with account_id as query parameter
echo "4. Testing Balances endpoint with account_id query parameter..."
curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${ACCOUNT_ID}" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool
echo ""
echo ""

# Test 5: Balances endpoint without account_id (should use default)
echo "5. Testing Balances endpoint without account_id (uses default)..."
curl -s -X GET "${BASE_URL}/api/v1/balances" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool
echo ""
echo ""

echo "=========================================="
echo "Tests Complete"
echo "=========================================="


