#!/usr/bin/env python3
"""
Get today's SPY trades with P&L from live TastyWorks account.
"""
import asyncio
import os
from datetime import date, timedelta, datetime
from tastytrade import Account
from tabulate import tabulate

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


async def get_spy_trades_today():
    """Get all SPY trades from today with P&L calculation."""
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
        print(f"   Fetching SPY trades from today ({date.today()})...\n")
        
        # Get trades from today (last 1 day to be safe, then filter to today)
        start = date.today() - timedelta(days=1)
        
        all_trades = await _paginate(
            lambda offset: account.a_get_history(
                session,
                start_date=start,
                underlying_symbol="SPY",  # Filter for SPY
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
            print("📊 No SPY trades found for today")
            return
        
        # Group trades by symbol and calculate P&L
        # Track open positions for P&L calculation
        position_tracker: dict[str, list[dict[str, any]]] = {}
        formatted_trades = []
        
        # Sort trades by time
        today_trades.sort(key=lambda t: (
            t.model_dump().get('executed_at') or 
            t.model_dump().get('transaction_date') or 
            ''
        ))
        
        for trade in today_trades:
            trade_dict = trade.model_dump() if hasattr(trade, 'model_dump') else {}
            
            # Extract relevant fields
            symbol = trade_dict.get('symbol', trade_dict.get('instrument_symbol', 'SPY'))
            action = trade_dict.get('action', trade_dict.get('transaction_sub_type', 'N/A'))
            quantity = float(trade_dict.get('quantity', trade_dict.get('quantity_decimal', 0)))
            price = float(trade_dict.get('price', trade_dict.get('fill_price', trade_dict.get('value', 0))))
            fees = float(trade_dict.get('fees', trade_dict.get('commission', trade_dict.get('fee', 0))))
            
            # Get executed time
            executed_at = trade_dict.get('executed_at', trade_dict.get('transaction_date', ''))
            if isinstance(executed_at, str):
                try:
                    dt = datetime.fromisoformat(executed_at.replace('Z', '+00:00'))
                    time_str = dt.strftime('%H:%M:%S')
                except:
                    time_str = str(executed_at)[:19]
            else:
                time_str = str(executed_at)[:19] if executed_at else 'N/A'
            
            # Calculate P&L
            pnl = None
            pnl_display = "—"
            
            # Try to get P&L directly from transaction
            pnl_fields = ["realized_pnl", "pnl", "profit_loss", "net_amount", "value_effect", 
                         "realized_profit_loss", "realized_pnl_dollars"]
            for field in pnl_fields:
                if field in trade_dict and trade_dict[field] is not None:
                    try:
                        pnl = float(trade_dict[field])
                        pnl_display = f"${pnl:+.2f}"
                        break
                    except (ValueError, TypeError):
                        continue
            
            # If P&L not directly available, calculate from position matching
            if pnl is None:
                # Check if this is a closing transaction
                if "Sell to Close" in action or "Buy to Close" in action:
                    # Try to find matching open position
                    if symbol in position_tracker and position_tracker[symbol]:
                        # Use FIFO: match with first open position
                        open_pos = position_tracker[symbol][0]
                        open_price = open_pos["price"]
                        open_quantity = open_pos["quantity"]
                        
                        # Determine if this is an option (has strike/expiration in symbol) or stock
                        is_option = any(char.isdigit() for char in symbol) and len(symbol) > 5
                        multiplier = 100 if is_option else 1  # Options are per 100 shares
                        
                        # Calculate P&L based on action
                        if "Sell to Close" in action:
                            # Sold to close long: profit = (sell_price - buy_price) * quantity
                            pnl = (price - open_price) * min(quantity, open_quantity) * multiplier
                        elif "Buy to Close" in action:
                            # Bought to close short: profit = (sell_price - buy_price) * quantity
                            pnl = (open_price - price) * min(quantity, open_quantity) * multiplier
                        
                        # Subtract fees
                        pnl = pnl - fees - open_pos.get("fees", 0)
                        
                        # Update position tracker
                        if quantity >= open_quantity:
                            position_tracker[symbol].pop(0)
                            if quantity > open_quantity:
                                # Partial close, track remaining
                                remaining_qty = quantity - open_quantity
                                position_tracker[symbol].insert(0, {
                                    "price": open_price,
                                    "quantity": remaining_qty,
                                    "fees": 0
                                })
                        else:
                            open_pos["quantity"] -= quantity
                        
                        if pnl is not None:
                            pnl_display = f"${pnl:+.2f}"
            
            # Track open positions for future P&L calculation
            if "Buy to Open" in action or "Sell to Open" in action:
                if symbol not in position_tracker:
                    position_tracker[symbol] = []
                position_tracker[symbol].append({
                    "price": price,
                    "quantity": quantity,
                    "fees": fees
                })
            
            # Get net value
            net_value = float(trade_dict.get('value', trade_dict.get('value_effect', trade_dict.get('net_value', 0))))
            if net_value == 0:
                # Calculate from price and quantity
                is_option = any(char.isdigit() for char in symbol) and len(symbol) > 5
                multiplier = 100 if is_option else 1
                net_value = price * quantity * multiplier
            
            formatted_trades.append({
                'Time': time_str,
                'Symbol': symbol,
                'Action': action,
                'Qty': int(quantity),
                'Price': f"${price:.2f}",
                'Net Value': f"${net_value:,.2f}",
                'Fees': f"${fees:.2f}",
                'P&L': pnl_display,
                'P&L Value': pnl  # For calculations
            })
        
        # Display trades
        print(f"📊 Found {len(formatted_trades)} SPY trade(s) today:\n")
        
        if formatted_trades:
            # Filter out trades without P&L for display (open positions)
            trades_with_pnl = [t for t in formatted_trades if t['P&L Value'] is not None]
            
            if trades_with_pnl:
                print("=" * 120)
                print("CLOSED TRADES (with P&L):")
                print("=" * 120)
                print(tabulate(
                    [[t[k] for k in ['Time', 'Symbol', 'Action', 'Qty', 'Price', 'Net Value', 'Fees', 'P&L']] 
                     for t in trades_with_pnl],
                    headers=['Time', 'Symbol', 'Action', 'Qty', 'Price', 'Net Value', 'Fees', 'P&L'],
                    tablefmt='grid'
                ))
                
                # Calculate totals
                total_pnl = sum(t['P&L Value'] for t in trades_with_pnl if t['P&L Value'] is not None)
                total_fees = sum(float(t['Fees'].replace('$', '')) for t in trades_with_pnl)
                winning_trades = sum(1 for t in trades_with_pnl if t['P&L Value'] and t['P&L Value'] > 0)
                losing_trades = sum(1 for t in trades_with_pnl if t['P&L Value'] and t['P&L Value'] < 0)
                
                print(f"\n📈 Summary for Closed Trades:")
                print(f"   Total Closed Trades: {len(trades_with_pnl)}")
                print(f"   Winning Trades: {winning_trades}")
                print(f"   Losing Trades: {losing_trades}")
                print(f"   Total Fees: ${total_fees:.2f}")
                print(f"   Total P&L: ${total_pnl:+.2f}")
            
            # Show open positions separately
            open_trades = [t for t in formatted_trades if t['P&L Value'] is None]
            if open_trades:
                print(f"\n{'=' * 120}")
                print("OPEN POSITIONS (no P&L yet):")
                print("=" * 120)
                print(tabulate(
                    [[t[k] for k in ['Time', 'Symbol', 'Action', 'Qty', 'Price', 'Net Value', 'Fees']] 
                     for t in open_trades],
                    headers=['Time', 'Symbol', 'Action', 'Qty', 'Price', 'Net Value', 'Fees'],
                    tablefmt='grid'
                ))
                print(f"\n   Open Positions: {len(open_trades)}")
            
            # Overall summary
            print(f"\n{'=' * 120}")
            print("OVERALL SUMMARY:")
            print("=" * 120)
            all_with_pnl = [t for t in formatted_trades if t['P&L Value'] is not None]
            total_pnl = sum(t['P&L Value'] for t in all_with_pnl if t['P&L Value'] is not None)
            total_fees = sum(float(t['Fees'].replace('$', '')) for t in formatted_trades)
            
            print(f"   Total SPY Trades Today: {len(formatted_trades)}")
            print(f"   Closed Trades: {len(trades_with_pnl) if trades_with_pnl else 0}")
            print(f"   Open Positions: {len(open_trades) if open_trades else 0}")
            print(f"   Total Fees: ${total_fees:.2f}")
            if all_with_pnl:
                print(f"   Total Realized P&L: ${total_pnl:+.2f}")
                win_rate = (winning_trades / len(all_with_pnl) * 100) if all_with_pnl else 0
                print(f"   Win Rate: {win_rate:.1f}%")
        else:
            print("No trades to display")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(get_spy_trades_today())


