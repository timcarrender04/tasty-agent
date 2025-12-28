#!/bin/bash

# Test script to verify Claude Haiku 4.5 is working
# Usage: ./test_haiku.sh

BASE_URL="${BASE_URL:-http://localhost:8033}"
API_KEY="${API_KEY:-5WI12958}"

echo "=========================================="
echo "Testing Claude Haiku 4.5"
echo "=========================================="
echo ""

# Test 1: Health Check
echo "1. Testing Health Check..."
HEALTH_RESPONSE=$(curl -s -X GET "${BASE_URL}/health")
echo "$HEALTH_RESPONSE" | python3 -m json.tool
echo ""

# Test 2: Chat endpoint with Haiku 4.5
echo "2. Testing Chat Endpoint (Haiku 4.5)..."
echo "Sending test message: 'Hello, what model are you?'"
echo ""

CHAT_RESPONSE=$(curl -s -X POST "${BASE_URL}/api/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "message": "Hello! Can you tell me what AI model you are? Please respond briefly."
  }')

echo "Response:"
echo "$CHAT_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$CHAT_RESPONSE"
echo ""

# Check if response contains any indication of the model
if echo "$CHAT_RESPONSE" | grep -qi "haiku\|claude\|model"; then
    echo "✅ Chat endpoint responded successfully!"
else
    echo "⚠️  Chat endpoint responded, but couldn't verify model name in response"
fi

echo ""
echo "=========================================="
echo "Test Complete"
echo "=========================================="
echo ""
echo "To check logs for model identifier:"
echo "  docker-compose logs http-server | grep -i 'model\|haiku\|claude'"
echo ""


