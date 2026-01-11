#!/usr/bin/env python3
"""
Get today's SPY trades using direct TastyTrade API with bearer token.
"""
import requests
import json
from datetime import date, datetime
from tabulate import tabulate

def get_spy_trades_direct():
    token = 'eyJhbGciOiJFZERTQSIsInR5cCI6InJ0K2p3dCIsImtpZCI6IndZaDBvNzktd0R3b0ZkV2w4WFNzelBrT0t2cjgySmhsUHBUVmVmT09PUFkiLCJqa3UiOiJodHRwczovL2ludGVyaW9yLWFwaS5hcjIudGFzdHl0cmFkZS5zeXN0ZW1zL29hdXRoL2p3a3MifQ.eyJpc3MiOiJodHRwczovL2FwaS50YXN0eXRyYWRlLmNvbSIsInN1YiI6IlVkMzM0YTk2Yy0xZDE5LTRlYWItODU3Zi0wZTU3NmFlY2IwOTkiLCJpYXQiOjE3NjYzNDM0OTksImF1ZCI6Ijk0M2FmN2M0LTgzMDEtNDBlMC1iNmUzLWNiNzE1M2U0MjVhMSIsImdyYW50X2lkIjoiRzU0Y2YxZTc5LWMwMzItNDBhZS04MTk5LTQwMzk0MDY4NDY2OCIsInNjb3BlIjoicmVhZCB0cmFkZSBvcGVuaWQifQ.Ktgg4PrvtmnmHAXgwW1JSOk5tCgSHSFiRcC4CDiE5OMqnD1P_e16w7bxI95IrpTm1-YLnGxWuJh-9L48ZoRwCw'
    account_id = '5e8400d3fa9090ca4e124020581115429bb235a3'
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    # Get account history for SPY trades
    base_url = 'https://api.tastytrade.com'
    today = date.today().isoformat()
    
    # Get transaction history
    url = f'{base_url}/accounts/{account_id}/transactions'
    params = {
        'per-page': 250,
        'page-offset': 0,
        'type': 'Trade',
        'start-date': today
    }
    
    # Also try without /transactions suffix
    if False:  # We'll try this if first fails
        url = f'{base_url}/accounts/{account_id}/transactions'
    
    print(f"🔍 Fetching SPY trades from TastyTrade API for account {account_id}...\n")
    
    try:
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 401:
            print("❌ Authentication failed. Token may be expired.")
            return
        elif response.status_code != 200:
            print(f"❌ Error {response.status_code}: {response.text}")
            return
        
        data = response.json()
        transactions = data.get('data', {}).get('items', [])
        
        # Filter for SPY trades
        spy_trades = []
        for tx in transactions:
            symbol = tx.get('symbol', '')
            instrument_symbol = tx.get('instrument-symbol', '')
            underlying_symbol = tx.get('underlying-symbol', '')
            
            if 'SPY' in str(symbol) or 'SPY' in str(instrument_symbol) or 'SPY' in str(underlying_symbol):
                spy_trades.append(tx)
        
        if not spy_trades:
            print("📊 No SPY trades found for today")
            return
        
        print(f"📊 Found {len(spy_trades)} SPY trade(s) today:\n")
        
        # Format and display trades
        table_data = []
        total_pnl = 0
        wins = 0
        losses = 0
        
        for trade in spy_trades:
            symbol = trade.get('symbol', trade.get('instrument-symbol', 'SPY'))
            action = trade.get('transaction-sub-type', trade.get('action', 'N/A'))
            qty = trade.get('quantity', 0)
            price = trade.get('price', 0)
            fees = trade.get('fees', 0)
            net_value = trade.get('value-effect', 0)
            pnl = trade.get('realized-pnl', trade.get('pnl', None))
            exec_at = trade.get('executed-at', trade.get('transaction-date', 'N/A'))
            
            # Format time
            if isinstance(exec_at, str) and 'T' in exec_at:
                try:
                    dt = datetime.fromisoformat(exec_at.replace('Z', '+00:00'))
                    time_str = dt.strftime('%H:%M:%S')
                except:
                    time_str = exec_at[:19]
            else:
                time_str = str(exec_at)[:19]
            
            if pnl is not None:
                pnl_val = float(pnl)
                total_pnl += pnl_val
                pnl_str = f"${pnl_val:+.2f}"
                if pnl_val > 0:
                    wins += 1
                elif pnl_val < 0:
                    losses += 1
            else:
                pnl_str = "—"
            
            table_data.append([
                time_str,
                symbol,
                action,
                int(qty),
                f"${float(price):.2f}",
                f"${float(net_value):,.2f}" if net_value else "N/A",
                f"${float(fees):.2f}",
                pnl_str
            ])
        
        print("=" * 120)
        print("SPY TRADES TODAY:")
        print("=" * 120)
        print(tabulate(
            table_data,
            headers=["Time", "Symbol", "Action", "Qty", "Price", "Net Value", "Fees", "P&L"],
            tablefmt="grid"
        ))
        
        print(f"\n📈 Summary:")
        print(f"   Total Trades: {len(spy_trades)}")
        print(f"   Winning Trades: {wins}")
        print(f"   Losing Trades: {losses}")
        if wins + losses > 0:
            win_rate = (wins / (wins + losses)) * 100
            print(f"   Win Rate: {win_rate:.1f}%")
        print(f"   Total P&L: ${total_pnl:+.2f}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    get_spy_trades_direct()


