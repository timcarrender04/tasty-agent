#!/bin/bash

# Multi-Tenant Test Script for TastyTrade HTTP API
# This demonstrates how to use curl with multiple tenants (API keys)

BASE_URL="http://localhost:8000"

echo "=========================================="
echo "Multi-Tenant TastyTrade API Test"
echo "=========================================="
echo ""
echo "Each tenant uses a different API key in the X-API-Key header"
echo "Each API key maps to different TastyTrade credentials"
echo ""

# Example: Tenant 1
TENANT1_API_KEY="5WI12958"
TENANT1_ACCOUNT_ID="5WI12958"

echo "----------------------------------------"
echo "TENANT 1 (API Key: ${TENANT1_API_KEY})"
echo "----------------------------------------"
echo ""

echo "1. Getting Tenant 1 Balances..."
curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${TENANT1_ACCOUNT_ID}" \
  -H "X-API-Key: ${TENANT1_API_KEY}" | python3 -m json.tool | head -10
echo ""
echo ""

# Example: Tenant 2 (if you had another tenant)
# TENANT2_API_KEY="tenant2_key"
# TENANT2_ACCOUNT_ID="account2_id"
# 
# echo "----------------------------------------"
# echo "TENANT 2 (API Key: ${TENANT2_API_KEY})"
# echo "----------------------------------------"
# echo ""
# 
# echo "1. Getting Tenant 2 Balances..."
# curl -s -X GET "${BASE_URL}/api/v1/balances?account_id=${TENANT2_ACCOUNT_ID}" \
#   -H "X-API-Key: ${TENANT2_API_KEY}" | python3 -m json.tool | head -10
# echo ""
# echo ""

echo "=========================================="
echo "Multi-Tenant Usage Examples"
echo "=========================================="
echo ""
echo "To add a new tenant, use POST /api/v1/credentials:"
echo ""
echo "curl -X POST ${BASE_URL}/api/v1/credentials \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{"
echo "    \"api_key\": \"tenant2_key\","
echo "    \"client_secret\": \"tenant2_client_secret\","
echo "    \"refresh_token\": \"tenant2_refresh_token\""
echo "  }'"
echo ""
echo ""
echo "Then use that API key in subsequent requests:"
echo ""
echo "curl -X GET \"${BASE_URL}/api/v1/balances?account_id=ACCOUNT_ID\" \\"
echo "  -H 'X-API-Key: tenant2_key'"
echo ""
echo ""
echo "Each tenant is completely isolated:"
echo "  - Different API keys"
echo "  - Different TastyTrade credentials"
echo "  - Different account IDs"
echo "  - Separate sessions and authentication"
echo ""

