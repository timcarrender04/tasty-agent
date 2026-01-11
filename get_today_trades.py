#!/usr/bin/env python3
"""
Get today's trades from live TastyWorks account.
"""
import asyncio
import os
from datetime import date, timedelta
from tastytrade import Account

from tasty_agent.utils.session import create_session, is_sandbox_mode, select_account


async def _paginate(fetch_fn, page_size):
    """Generic pagination helper for API calls."""
    all_items = []
    offset = 0
    while True:
        items = await fetch_fn(offset)
        all_items.extend(items or [])
        if not items or len(items) < page_size:
            break
        offset += page_size
    return all_items


async def get_todays_trades():
    """Get all trades from today."""
    # Ensure we're using LIVE account (not paper)
    client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
    refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")
    account_id = os.getenv("TASTYTRADE_ACCOUNT_ID")
    
    # Force live mode - ensure paper/sandbox mode is disabled
    if is_sandbox_mode():
        print("⚠️  WARNING: Paper mode is enabled. Use live account credentials.")
        print("   Set TASTYTRADE_PAPER_MODE=false or unset TASTYTRADE_PAPER_MODE")
        return
    
    if not client_secret or not refresh_token:
        print("❌ Missing TastyTrade credentials")
        print("   Set TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN")
        return
    
    try:
        # Force live mode (is_test=False)
        session = create_session(client_secret, refresh_token, is_test=False)
        accounts = Account.get(session)
        
        try:
            account = select_account(accounts, account_id)
        except ValueError as e:
            print(f"❌ {e}")
            return
        
        print(f"✅ Connected to account: {account.account_number}")
        print(f"   Fetching trades from today ({date.today()})...\n")
        
        # Get trades from today (last 1 day to be safe, then filter to today)
        start = date.today() - timedelta(days=1)
        
        all_trades = await _paginate(
            lambda offset: account.a_get_history(
                session,
                start_date=start,
                type="Trade",
                per_page=250,
                page_offset=offset
            ),
            page_size=250
        )
        
        # Filter to only today's trades
        today = date.today()
        today_trades = []
        for trade in all_trades:
            trade_date = None
            # Check various date fields
            if hasattr(trade, 'transaction_date') and trade.transaction_date:
                trade_date = trade.transaction_date
            elif hasattr(trade, 'executed_at') and trade.executed_at:
                if isinstance(trade.executed_at, date):
                    trade_date = trade.executed_at
                elif isinstance(trade.executed_at, str):
                    trade_date = trade.executed_at.split('T')[0]
                    if trade_date:
                        try:
                            trade_date = date.fromisoformat(trade_date)
                        except:
                            pass
            elif hasattr(trade, 'created_at') and trade.created_at:
                if isinstance(trade.created_at, date):
                    trade_date = trade.created_at
                elif isinstance(trade.created_at, str):
                    trade_date = trade.created_at.split('T')[0]
                    if trade_date:
                        try:
                            trade_date = date.fromisoformat(trade_date)
                        except:
                            pass
            
            # Try to get date from model_dump if available
            if not trade_date:
                trade_dict = trade.model_dump() if hasattr(trade, 'model_dump') else {}
                if 'transaction_date' in trade_dict and trade_dict['transaction_date']:
                    trade_date_str = trade_dict['transaction_date']
                    if isinstance(trade_date_str, str):
                        trade_date = trade_date_str.split('T')[0]
                        try:
                            trade_date = date.fromisoformat(trade_date)
                        except:
                            pass
                elif 'executed_at' in trade_dict and trade_dict['executed_at']:
                    exec_str = trade_dict['executed_at']
                    if isinstance(exec_str, str):
                        trade_date = exec_str.split('T')[0]
                        try:
                            trade_date = date.fromisoformat(trade_date)
                        except:
                            pass
            
            if trade_date and isinstance(trade_date, date) and trade_date == today:
                today_trades.append(trade)
            elif isinstance(trade_date, str) and trade_date.startswith(str(today)):
                today_trades.append(trade)
        
        if not today_trades:
            print("📊 No trades found for today")
            return
        
        # Format trades for display
        print(f"📊 Found {len(today_trades)} trade(s) today:\n")
        
        # Convert to list of dicts for tabulate
        trade_rows = []
        for trade in today_trades:
            trade_dict = trade.model_dump() if hasattr(trade, 'model_dump') else {}
            
            # Extract relevant fields
            symbol = trade_dict.get('symbol', trade_dict.get('instrument_symbol', 'N/A'))
            action = trade_dict.get('action', trade_dict.get('transaction_sub_type', 'N/A'))
            quantity = trade_dict.get('quantity', trade_dict.get('quantity_decimal', 0))
            price = trade_dict.get('price', trade_dict.get('price_effect', 'N/A'))
            value = trade_dict.get('value', trade_dict.get('value_effect', 'N/A'))
            fees = trade_dict.get('fees', trade_dict.get('commission', 0))
            executed_at = trade_dict.get('executed_at', trade_dict.get('transaction_date', 'N/A'))
            
            trade_rows.append({
                'Time': executed_at if isinstance(executed_at, str) else str(executed_at)[:19] if executed_at else 'N/A',
                'Symbol': symbol,
                'Action': action,
                'Quantity': quantity,
                'Price': f"${float(price):.2f}" if isinstance(price, (int, float)) else price,
                'Value': f"${float(value):.2f}" if isinstance(value, (int, float)) else value,
                'Fees': f"${float(fees):.2f}" if isinstance(fees, (int, float)) else f"${fees}" if fees else '$0.00'
            })
        
        # Print table manually
        if trade_rows:
            # Print header
            headers = list(trade_rows[0].keys())
            col_widths = {h: max(len(h), max(len(str(row.get(h, ''))) for row in trade_rows)) for h in headers}
            
            # Header row
            header_line = ' | '.join(h.ljust(col_widths[h]) for h in headers)
            print(header_line)
            print('-' * len(header_line))
            
            # Data rows
            for row in trade_rows:
                print(' | '.join(str(row.get(h, '')).ljust(col_widths[h]) for h in headers))
        
        # Summary
        total_value = sum(
            float(row['Value'].replace('$', '').replace(',', ''))
            for row in trade_rows
            if '$' in str(row['Value']) and row['Value'] != 'N/A'
        )
        total_fees = sum(
            float(row['Fees'].replace('$', '').replace(',', ''))
            for row in trade_rows
            if '$' in str(row['Fees'])
        )
        
        print(f"\n📈 Summary:")
        print(f"   Total Trades: {len(today_trades)}")
        print(f"   Total Value: ${total_value:,.2f}")
        print(f"   Total Fees: ${total_fees:,.2f}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(get_todays_trades())


