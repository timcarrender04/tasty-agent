#!/usr/bin/env python3
"""
Get today's SPY trades with P&L using the HTTP API.
Usage: python get_spy_trades_api.py [API_KEY]
If no API_KEY is provided, tries to use API_KEY from environment.
"""
import sys
import json
import requests
from datetime import date
from tabulate import tabulate

def get_spy_trades(api_key: str = None, api_url: str = "http://localhost:8033"):
    """Fetch today's SPY trades from the HTTP API."""
    if not api_key:
        import os
        api_key = os.getenv("API_KEY")
    
    if not api_key:
        print("❌ Error: API key required")
        print("   Usage: python get_spy_trades_api.py [API_KEY]")
        print("   Or set API_KEY environment variable")
        return
    
    # Get recent trades for SPY (last 1 day)
    url = f"{api_url}/api/v1/recent-trades"
    params = {
        "days": 1,
        "underlying_symbol": "SPY"
    }
    headers = {
        "X-API-Key": api_key
    }
    
    try:
        print(f"🔍 Fetching SPY trades from {api_url}...\n")
        response = requests.get(url, params=params, headers=headers)
        
        if response.status_code == 401:
            print("❌ Authentication failed. Check your API key.")
            return
        elif response.status_code != 200:
            print(f"❌ Error {response.status_code}: {response.text}")
            return
        
        data = response.json()
        
        # Extract trades
        trades = data.get("trades", [])
        if not trades:
            print("📊 No SPY trades found for today")
            return
        
        # Filter to today's trades
        today = date.today().isoformat()
        today_trades = []
        for trade in trades:
            trade_date = trade.get("date_time", "")
            if today in trade_date or trade_date.startswith(today):
                today_trades.append(trade)
        
        if not today_trades:
            print("📊 No SPY trades found for today")
            print(f"   (Found {len(trades)} trade(s) in the last day, but none from today)")
            return
        
        # Display trades
        print(f"📊 Found {len(today_trades)} SPY trade(s) today:\n")
        
        # Separate closed trades (with P&L) and open positions
        closed_trades = [t for t in today_trades if t.get("pnl_value") is not None]
        open_positions = [t for t in today_trades if t.get("pnl_value") is None]
        
        if closed_trades:
            print("=" * 120)
            print("CLOSED TRADES (with P&L):")
            print("=" * 120)
            
            # Prepare table data
            table_data = []
            for trade in closed_trades:
                pnl = trade.get("pnl_value", 0)
                pnl_str = trade.get("pnl", "—")
                table_data.append([
                    trade.get("date_time", "N/A"),
                    trade.get("symbol", "N/A"),
                    trade.get("action", "N/A"),
                    trade.get("quantity", 0),
                    trade.get("price", "N/A"),
                    trade.get("net_value", trade.get("fees", "N/A")),
                    trade.get("fees", "$0.00"),
                    pnl_str
                ])
            
            print(tabulate(
                table_data,
                headers=["Time", "Symbol", "Action", "Qty", "Price", "Net Value", "Fees", "P&L"],
                tablefmt="grid"
            ))
            
            # Calculate summary
            total_pnl = sum(t.get("pnl_value", 0) for t in closed_trades)
            total_fees = sum(float(t.get("fees", "$0").replace("$", "")) for t in closed_trades)
            winning_trades = sum(1 for t in closed_trades if t.get("pnl_value", 0) > 0)
            losing_trades = sum(1 for t in closed_trades if t.get("pnl_value", 0) < 0)
            
            print(f"\n📈 Summary for Closed Trades:")
            print(f"   Total Closed Trades: {len(closed_trades)}")
            print(f"   Winning Trades: {winning_trades}")
            print(f"   Losing Trades: {losing_trades}")
            print(f"   Total Fees: ${total_fees:.2f}")
            print(f"   Total P&L: ${total_pnl:+.2f}")
            if closed_trades:
                win_rate = (winning_trades / len(closed_trades)) * 100
                print(f"   Win Rate: {win_rate:.1f}%")
        
        if open_positions:
            print(f"\n{'=' * 120}")
            print("OPEN POSITIONS (no P&L yet):")
            print("=" * 120)
            
            table_data = []
            for trade in open_positions:
                table_data.append([
                    trade.get("date_time", "N/A"),
                    trade.get("symbol", "N/A"),
                    trade.get("action", "N/A"),
                    trade.get("quantity", 0),
                    trade.get("price", "N/A"),
                    trade.get("net_value", trade.get("fees", "N/A")),
                    trade.get("fees", "$0.00")
                ])
            
            print(tabulate(
                table_data,
                headers=["Time", "Symbol", "Action", "Qty", "Price", "Net Value", "Fees"],
                tablefmt="grid"
            ))
            print(f"\n   Open Positions: {len(open_positions)}")
        
        # Overall summary
        print(f"\n{'=' * 120}")
        print("OVERALL SUMMARY:")
        print("=" * 120)
        total_fees_all = sum(float(t.get("fees", "$0").replace("$", "")) for t in today_trades)
        total_pnl_closed = sum(t.get("pnl_value", 0) for t in closed_trades)
        
        print(f"   Total SPY Trades Today: {len(today_trades)}")
        print(f"   Closed Trades: {len(closed_trades)}")
        print(f"   Open Positions: {len(open_positions)}")
        print(f"   Total Fees: ${total_fees_all:.2f}")
        if closed_trades:
            print(f"   Total Realized P&L: ${total_pnl_closed:+.2f}")
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Error: Could not connect to {api_url}")
        print("   Make sure the HTTP API server is running")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    api_key = sys.argv[1] if len(sys.argv) > 1 else None
    get_spy_trades(api_key)


