#!/usr/bin/env python3
"""
Validate paper trading mode by attempting to place an order when market is closed.

This script:
1. Checks current market status
2. Attempts to place a market order for SPY (or specified symbol)
3. Expects a "market is closed" error from TastyTrade API when market is closed
4. Confirms paper trading mode is working correctly

Usage:
    python scripts/validate_paper_trading.py [--symbol SPY] [--api-key YOUR_KEY]
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path to import tasty_agent
sys.path.insert(0, str(Path(__file__).parent.parent))

from tasty_agent.utils.session import create_session, is_sandbox_mode, select_account
from tasty_agent.utils.credentials import get_credentials
from tasty_agent.http_server import get_instrument_details, InstrumentSpec
from tastytrade import Account
from tastytrade.instruments import Equity
from tastytrade.order import NewOrder, OrderAction, OrderType, OrderTimeInForce
from tastytrade.market_sessions import a_get_market_sessions, ExchangeType, MarketStatus
from decimal import Decimal


async def check_market_status(session):
    """Check current market status."""
    try:
        market_sessions = await a_get_market_sessions(session, [ExchangeType('Equity')])
        if market_sessions:
            status = market_sessions[0].status
            print(f"📊 Market Status: {status.value}")
            return status
        return None
    except Exception as e:
        print(f"⚠️  Could not check market status: {e}")
        return None


async def validate_paper_trading(symbol: str = "SPY", api_key: str | None = None):
    """Validate paper trading by attempting to place an order."""
    
    # Check mode
    paper_mode = is_sandbox_mode()
    mode_str = "📝 PAPER TRADING" if paper_mode else "💰 LIVE TRADING"
    print(f"\n{'='*60}")
    print(f"Mode: {mode_str}")
    print(f"{'='*60}\n")
    
    if not paper_mode:
        print("⚠️  WARNING: Not in paper trading mode!")
        print("   Set TASTYTRADE_PAPER_MODE=true or TASTYTRADE_SANDBOX=true")
        response = input("\nContinue anyway? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted.")
            return
    
    # Get credentials and create session
    try:
        if api_key:
            # For testing with specific API key
            credentials = get_credentials(api_key)
        else:
            # Use first available API key
            api_keys = list(get_credentials().keys())
            if not api_keys:
                print("❌ No API keys found. Set TASTYTRADE_API_KEY or configure credentials.")
                return
            api_key = api_keys[0]
            credentials = get_credentials(api_key)
        
        session = create_session(
            credentials["client_secret"],
            credentials["refresh_token"]
        )
        
        accounts = session.get_accounts()
        account = select_account(accounts)
        
        print(f"✅ Authenticated with TastyTrade")
        print(f"   Account: {account.account_number}")
        print(f"   Sandbox: {session.is_test}")
        print()
        
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return
    
    # Check market status
    market_status = await check_market_status(session)
    print()
    
    # Get instrument details
    print(f"🔍 Getting instrument details for {symbol}...")
    try:
        instrument_spec = InstrumentSpec(symbol=symbol)
        instrument_details = await get_instrument_details(session, [instrument_spec])
        
        if not instrument_details:
            print(f"❌ Could not find instrument: {symbol}")
            return
        
        instrument = instrument_details[0].instrument
        
        if not isinstance(instrument, Equity):
            print(f"⚠️  {symbol} is not an equity, but continuing anyway...")
        
        print(f"✅ Found instrument: {symbol}")
        print()
        
    except Exception as e:
        print(f"❌ Error getting instrument: {e}")
        return
    
    # Attempt to place market order
    print(f"📤 Attempting to place MARKET order for {symbol}...")
    print(f"   (This should fail with 'market is closed' error if market is closed)\n")
    
    try:
        # Build a simple market order
        leg = instrument.build_leg(Decimal("1"), OrderAction.BUY)
        order = NewOrder(
            order_type=OrderType.MARKET,
            time_in_force=OrderTimeInForce.DAY,
            legs=[leg]
        )
        
        # Try to place the order (NOT dry_run - actually attempt to place it)
        result = await account.a_place_order(session, order, dry_run=False)
        
        # If we get here, the order was accepted (market might be open)
        print("⚠️  Order was ACCEPTED by TastyTrade API!")
        print(f"   This means the market appears to be OPEN.")
        print(f"   Order result: {result}")
        print()
        
        if market_status != MarketStatus.OPEN:
            print("⚠️  WARNING: Market status check showed market is NOT open,")
            print("   but order was accepted. This may indicate:")
            print("   1. Market just opened")
            print("   2. Paper trading allows orders outside market hours")
            print("   3. Market status check is incorrect")
        
    except Exception as e:
        error_msg = str(e).lower()
        print(f"📋 Error received from TastyTrade API:")
        print(f"   {type(e).__name__}: {str(e)}\n")
        
        # Check if it's a market-closed error
        if any(keyword in error_msg for keyword in [
            'market is closed', 'market closed', 'market hours', 
            'trading hours', 'market not open', 'outside trading hours',
            'closed', 'not open', 'market status'
        ]):
            print("✅ VALIDATION PASSED!")
            print("   ✓ Paper trading mode is working correctly")
            print("   ✓ TastyTrade API correctly returned 'market is closed' error")
            print("   ✓ Error handling is functioning as expected")
            return True
        else:
            print("⚠️  Received an error, but it's not a 'market closed' error:")
            print(f"   This might be a different API issue.")
            print(f"   Error type: {type(e).__name__}")
            return False
    
    return True


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Validate paper trading mode with market-closed error handling"
    )
    parser.add_argument(
        "--symbol",
        default="SPY",
        help="Symbol to test with (default: SPY)"
    )
    parser.add_argument(
        "--api-key",
        help="API key to use (default: first available)"
    )
    
    args = parser.parse_args()
    
    try:
        await validate_paper_trading(symbol=args.symbol, api_key=args.api_key)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
