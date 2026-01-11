#!/usr/bin/env python3
"""
Get today's SPY trades using bearer token directly via TastyTrade API.
Uses the token as Authorization header.
"""
import requests
import json
from datetime import date, datetime
from tabulate import tabulate

def get_spy_trades_with_token():
    token = 'eyJhbGciOiJFZERTQSIsInR5cCI6InJ0K2p3dCIsImtpZCI6IndZaDBvNzktd0R3b0ZkV2w4WFNzelBrT0t2cjgySmhsUHBUVmVmT09PUFkiLCJqa3UiOiJodHRwczovL2ludGVyaW9yLWFwaS5hcjIudGFzdHl0cmFkZS5zeXN0ZW1zL29hdXRoL2p3a3MifQ.eyJpc3MiOiJodHRwczovL2FwaS50YXN0eXRyYWRlLmNvbSIsInN1YiI6IlVkMzM0YTk2Yy0xZDE5LTRlYWItODU3Zi0wZTU3NmFlY2IwOTkiLCJpYXQiOjE3NjYzNDM0OTksImF1ZCI6Ijk0M2FmN2M0LTgzMDEtNDBlMC1iNmUzLWNiNzE1M2U0MjVhMSIsImdyYW50X2lkIjoiRzU0Y2YxZTc5LWMwMzItNDBhZS04MTk5LTQwMzk0MDY4NDY2OCIsInNjb3BlIjoicmVhZCB0cmFkZSBvcGVuaWQifQ.Ktgg4PrvtmnmHAXgwW1JSOk5tCgSHSFiRcC4CDiE5OMqnD1P_e16w7bxI95IrpTm1-YLnGxWuJh-9L48ZoRwCw'
    account_id = '5e8400d3fa9090ca4e124020581115429bb235a3'
    account_number = '5WI12958'
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }
    
    base_url = 'https://api.tastytrade.com'
    today = date.today().isoformat()
    
    print(f"🔍 Fetching SPY trades from TastyTrade for account {account_number}...\n")
    
    try:
        # First, get accounts to verify token works
        accounts_url = f'{base_url}/customers/me/accounts'
        print(f"Checking accounts...")
        acc_response = requests.get(accounts_url, headers=headers)
        if acc_response.status_code != 200:
            print(f"❌ Failed to get accounts: {acc_response.status_code}")
            print(f"Response: {acc_response.text[:500]}")
            return
        
        accounts = acc_response.json().get('data', {}).get('items', [])
        print(f"✅ Found {len(accounts)} account(s)")
        
        # Find the right account
        account = None
        for acc in accounts:
            if acc.get('account-number') == account_number or acc.get('external-id') == account_id:
                account = acc
                break
        
        if not account:
            print(f"❌ Account {account_number} not found")
            return
        
        print(f"✅ Using account: {account.get('account-number')}")
        
        # Get transaction history - try different endpoint formats
        # The tastytrade library uses: /accounts/{account_id}/history with query params
        account_num = account.get('account-number')
        
        # Try equity history endpoint which includes transactions
        history_url = f'{base_url}/accounts/{account_num}/equity-history'
        params = {
            'start-date': today,
            'end-date': today
        }
        
        print(f"\nFetching equity history...")
        history_response = requests.get(history_url, headers=headers, params=params)
        
        # Also try positions to see open trades
        positions_url = f'{base_url}/accounts/{account_num}/positions'
        print(f"Fetching positions...")
        positions_response = requests.get(positions_url, headers=headers)
        
        if positions_response.status_code == 200:
            positions = positions_response.json().get('data', {}).get('items', [])
            spy_positions = [p for p in positions if 'SPY' in str(p.get('symbol', ''))]
            
            if spy_positions:
                print(f"\n📊 Found {len(spy_positions)} SPY position(s):\n")
                for pos in spy_positions:
                    symbol = pos.get('symbol', 'N/A')
                    quantity = pos.get('quantity', 0)
                    avg_open = pos.get('average-open-price', 0)
                    print(f"  {symbol}: {quantity} @ ${avg_open}")
        
        # Try to get transaction history via streaming or different endpoint
        # The actual transaction endpoint might be different
        print(f"\n📊 Note: Full transaction history may require different endpoint.")
        print(f"   For today's closed trades with P&L, you may need to check:")
        print(f"   1. Positions endpoint (above) for open positions")
        print(f"   2. Order history for executed orders")
        print(f"   3. Account statements for detailed P&L")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    get_spy_trades_with_token()


