"""
HTTP Server for TastyTrade API - Allows multiple kiosks to connect via REST API.
"""
import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import humanize
from aiocache import Cache, cached
from aiocache.serializers import PickleSerializer
from aiolimiter import AsyncLimiter
from fastapi import FastAPI, HTTPException, Header, Depends, Query, status
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from tabulate import tabulate
from tastytrade import Account, Session
from tastytrade.dxfeed import Greeks, Quote
from tastytrade.instruments import Equity, Option, a_get_option_chain
from tastytrade.market_sessions import ExchangeType, MarketStatus, a_get_market_holidays, a_get_market_sessions
from tastytrade.metrics import a_get_market_metrics
from tastytrade.order import InstrumentType, NewOrder, OrderAction, OrderTimeInForce, OrderType
from tastytrade.search import a_symbol_search
from tastytrade.streamer import DXLinkStreamer
from tastytrade.utils import now_in_new_york
from tastytrade.watchlists import PrivateWatchlist, PublicWatchlist

logger = logging.getLogger(__name__)

rate_limiter = AsyncLimiter(2, 1)  # 2 requests per second

# API Key authentication
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def load_credentials() -> dict[str, tuple[str, str]]:
    """Load credentials from environment variables and/or JSON configuration."""
    credentials: dict[str, tuple[str, str]] = {}
    
    # Load from JSON environment variable
    credentials_json = os.getenv("TASTYTRADE_CREDENTIALS_JSON")
    if credentials_json:
        try:
            creds_dict = json.loads(credentials_json)
            for api_key, cred_data in creds_dict.items():
                if isinstance(cred_data, dict):
                    client_secret = cred_data.get("client_secret")
                    refresh_token = cred_data.get("refresh_token")
                    if client_secret and refresh_token:
                        credentials[api_key] = (client_secret, refresh_token)
                    else:
                        logger.warning(f"Missing client_secret or refresh_token for API key: {api_key}")
                else:
                    logger.warning(f"Invalid credential format for API key: {api_key}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse TASTYTRADE_CREDENTIALS_JSON: {e}")
    
    # Load from JSON file if it exists
    creds_file = Path("credentials.json")
    if creds_file.exists():
        try:
            with open(creds_file, "r") as f:
                creds_dict = json.load(f)
            for api_key, cred_data in creds_dict.items():
                if isinstance(cred_data, dict):
                    client_secret = cred_data.get("client_secret")
                    refresh_token = cred_data.get("refresh_token")
                    if client_secret and refresh_token:
                        credentials[api_key] = (client_secret, refresh_token)
                    else:
                        logger.warning(f"Missing client_secret or refresh_token for API key: {api_key}")
        except Exception as e:
            logger.error(f"Failed to load credentials.json: {e}")
    
    # Load from legacy environment variables (backward compatibility)
    client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
    refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")
    default_api_key = os.getenv("API_KEY", "default")
    
    if client_secret and refresh_token:
        if default_api_key not in credentials:
            credentials[default_api_key] = (client_secret, refresh_token)
            logger.info(f"Loaded default credentials for API key: {default_api_key}")
        else:
            logger.warning(f"API key {default_api_key} already exists in credentials, skipping default env vars")
    
    if not credentials:
        logger.warning("No credentials loaded. Server may not work correctly.")
    
    return credentials


async def verify_api_key(api_key: str = Depends(API_KEY_HEADER)) -> str:
    """Verify API key from header and ensure credentials exist."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide X-API-Key header."
        )
    
    if api_key not in api_key_credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid API key. No credentials configured for this key."
        )
    
    return api_key


# Reuse helper functions from server.py
async def _stream_events(
    session: Session,
    event_type: type[Quote] | type[Greeks],
    streamer_symbols: list[str],
    timeout: float
) -> list[Any]:
    """Generic streaming helper for Quote/Greeks events."""
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(event_type, streamer_symbols)
        events_by_symbol: dict[str, Any] = {}
        expected = set(streamer_symbols)
        while len(events_by_symbol) < len(expected):
            event = await asyncio.wait_for(streamer.get_event(event_type), timeout=timeout)
            if event.event_symbol in expected:
                events_by_symbol[event.event_symbol] = event
        return [events_by_symbol[s] for s in streamer_symbols]


async def _paginate[T](
    fetch_fn: Callable[[int], Awaitable[list[T]]],
    page_size: int
) -> list[T]:
    """Generic pagination helper for API calls."""
    all_items: list[T] = []
    offset = 0
    while True:
        async with rate_limiter:
            items = await fetch_fn(offset)
        all_items.extend(items or [])
        if not items or len(items) < page_size:
            break
        offset += page_size
    return all_items


def to_table(data: Sequence[BaseModel]) -> str:
    """Format list of Pydantic models as a plain table."""
    if not data:
        return "No data"
    return tabulate([item.model_dump() for item in data], headers='keys', tablefmt='plain')


@dataclass
class ServerContext:
    session: Session
    accounts: list[Account]


# Credential mapping: API key -> (client_secret, refresh_token)
api_key_credentials: dict[str, tuple[str, str]] = {}

# Session cache: API key -> ServerContext
api_key_sessions: dict[str, ServerContext] = {}


def get_api_key_context(api_key: str) -> ServerContext:
    """Get or create ServerContext for an API key with lazy initialization."""
    global api_key_sessions
    
    # Check if context already exists
    if api_key in api_key_sessions:
        context = api_key_sessions[api_key]
        session = context.session
        
        # Refresh if expired or expiring within 5 seconds
        time_until_expiry = (session.session_expiration - now_in_new_york()).total_seconds()
        if time_until_expiry < 5:
            logger.info(f"Session expiring in {time_until_expiry:.0f}s for API key {api_key[:8]}..., refreshing...")
            session.refresh()
            logger.info(f"Session refreshed, new expiration: {session.session_expiration}")
        
        return context
    
    # Create new context if credentials exist
    if api_key not in api_key_credentials:
        raise HTTPException(
            status_code=500,
            detail=f"No credentials configured for API key {api_key[:8]}..."
        )
    
    client_secret, refresh_token = api_key_credentials[api_key]
    
    try:
        session = Session(client_secret, refresh_token)
        accounts = Account.get(session)
        logger.info(f"Successfully authenticated API key {api_key[:8]}... with Tastytrade. Found {len(accounts)} account(s).")
        
        context = ServerContext(session=session, accounts=accounts)
        api_key_sessions[api_key] = context
        return context
    except Exception as e:
        logger.error(f"Failed to authenticate API key {api_key[:8]}... with Tastytrade: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to authenticate with Tastytrade: {e}"
        ) from e


def get_account_context(api_key: str, account_id: str | None) -> tuple[Session, Account]:
    """Get session and account for a given API key and account ID."""
    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="account_id is required. Provide it as a query parameter."
        )
    
    context = get_api_key_context(api_key)
    
    # Find the specified account
    account = next((acc for acc in context.accounts if acc.account_number == account_id), None)
    if not account:
        available_accounts = [acc.account_number for acc in context.accounts]
        raise HTTPException(
            status_code=404,
            detail=f"Account '{account_id}' not found. Available accounts: {available_accounts}"
        )
    
    return context.session, account


def get_valid_session(api_key: str) -> Session:
    """Get a valid session for an API key, refreshing if expired or about to expire."""
    context = get_api_key_context(api_key)
    return context.session


async def keep_sessions_alive():
    """Background task to periodically refresh sessions before they expire."""
    # Check every 5 minutes (sessions expire after 15 minutes)
    check_interval = 5 * 60  # 5 minutes in seconds
    
    while True:
        try:
            await asyncio.sleep(check_interval)
            
            # Check all active sessions
            for api_key, context in list(api_key_sessions.items()):
                try:
                    session = context.session
                    time_until_expiry = (session.session_expiration - now_in_new_york()).total_seconds()
                    
                    # Refresh if expiring within 10 minutes (buffer for network issues)
                    if time_until_expiry < 10 * 60:
                        logger.info(f"Refreshing session for API key {api_key[:8]}... (expires in {time_until_expiry/60:.1f} min)")
                        session.refresh()
                        logger.debug(f"Session refreshed, new expiration: {session.session_expiration}")
                except Exception as e:
                    logger.error(f"Failed to refresh session for API key {api_key[:8]}...: {e}")
                    # Remove invalid session so it can be recreated on next request
                    api_key_sessions.pop(api_key, None)
        except asyncio.CancelledError:
            logger.info("Session keep-alive task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in keep-alive task: {e}")
            # Continue running even if there's an error


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manages Tastytrade credential loading and session lifecycle."""
    global api_key_credentials, api_key_sessions
    
    # Load credentials at startup
    api_key_credentials = load_credentials()
    
    if not api_key_credentials:
        logger.warning(
            "No credentials loaded. Set TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN "
            "environment variables, or configure TASTYTRADE_CREDENTIALS_JSON, or create credentials.json"
        )
    
    logger.info(f"Loaded credentials for {len(api_key_credentials)} API key(s)")
    
    # Start background task to keep sessions alive
    keep_alive_task = asyncio.create_task(keep_sessions_alive())
    
    yield
    
    # Cleanup on shutdown
    keep_alive_task.cancel()
    try:
        await keep_alive_task
    except asyncio.CancelledError:
        pass
    api_key_sessions.clear()
    api_key_credentials.clear()


async def get_session_and_account(
    api_key: str = Depends(verify_api_key),
    account_id: str = Query(..., description="TastyTrade account ID")
) -> tuple[Session, Account]:
    """Dependency to get session and account for an API key and account ID."""
    return get_account_context(api_key, account_id)


async def get_session_only(
    api_key: str = Depends(verify_api_key)
) -> Session:
    """Dependency to get session only (for endpoints that don't need account)."""
    return get_valid_session(api_key)


# FastAPI app
app = FastAPI(
    title="TastyTrade API Server",
    description="REST API server for TastyTrade brokerage operations. Multiple kiosks can connect via HTTP.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware for kiosk access
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class InstrumentSpec(BaseModel):
    """Specification for an instrument (stock or option)."""
    symbol: str = Field(..., description="Stock symbol (e.g., 'AAPL', 'TQQQ')")
    option_type: Literal['C', 'P'] | None = Field(None, description="Option type: 'C' for call, 'P' for put (omit for stocks)")
    strike_price: float | None = Field(None, description="Strike price (required for options)")
    expiration_date: str | None = Field(None, description="Expiration date in YYYY-MM-DD format (required for options)")


class OrderLeg(BaseModel):
    """Specification for an order leg."""
    symbol: str = Field(..., description="Stock symbol (e.g., 'TQQQ', 'AAPL')")
    action: str = Field(..., description="For stocks: 'Buy' or 'Sell'. For options: 'Buy to Open', 'Buy to Close', 'Sell to Open', 'Sell to Close'")
    quantity: int = Field(..., description="Number of contracts/shares")
    option_type: Literal['C', 'P'] | None = Field(None, description="Option type: 'C' for call, 'P' for put (omit for stocks)")
    strike_price: float | None = Field(None, description="Strike price (required for options)")
    expiration_date: str | None = Field(None, description="Expiration date in YYYY-MM-DD format (required for options)")


class WatchlistSymbol(BaseModel):
    """Symbol specification for watchlist operations."""
    symbol: str = Field(..., description="Stock symbol (e.g., 'AAPL', 'TSLA')")
    instrument_type: str = Field(..., description="One of: 'Equity', 'Equity Option', 'Future', 'Future Option', 'Cryptocurrency', 'Warrant'")


@dataclass
class InstrumentDetail:
    """Details for a resolved instrument."""
    streamer_symbol: str
    instrument: Equity | Option


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def validate_date_format(date_string: str) -> date:
    """Validate date format and return date object."""
    try:
        return datetime.strptime(date_string, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid date format '{date_string}'. Expected YYYY-MM-DD format.") from e


def validate_strike_price(strike_price: Any) -> float:
    """Validate and convert strike price to float."""
    try:
        strike = float(strike_price)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid strike price '{strike_price}'. Expected positive number.") from e
    if strike <= 0:
        raise ValueError(f"Invalid strike price '{strike_price}'. Must be positive.")
    return strike


def _option_chain_key_builder(fn, session: Session, symbol: str):
    """Build cache key using only symbol (session changes but symbol is stable)."""
    return f"option_chain:{symbol}"


@cached(ttl=86400, cache=Cache.MEMORY, serializer=PickleSerializer(), key_builder=_option_chain_key_builder)
async def get_cached_option_chain(session: Session, symbol: str):
    """Cache option chains for 24 hours as they rarely change during that timeframe."""
    return await a_get_option_chain(session, symbol)


async def get_instrument_details(session: Session, instrument_specs: list[InstrumentSpec]) -> list[InstrumentDetail]:
    """Get instrument details with validation and caching."""
    async def lookup_single_instrument(spec: InstrumentSpec) -> InstrumentDetail:
        symbol = spec.symbol.upper()
        option_type = spec.option_type
        
        if option_type:
            # Validate option parameters
            strike_price = validate_strike_price(spec.strike_price)
            expiration_date = spec.expiration_date
            if not expiration_date:
                raise ValueError(f"expiration_date is required for option {symbol}")
            
            target_date = validate_date_format(expiration_date)
            option_type = option_type.upper()
            if option_type not in ['C', 'P']:
                raise ValueError(f"Invalid option_type '{option_type}'. Expected 'C' or 'P'.")
            
            # Get cached option chain and find specific option
            chain = await get_cached_option_chain(session, symbol)
            if target_date not in chain:
                available_dates = sorted(chain.keys())
                raise ValueError(f"No options found for {symbol} expiration {expiration_date}. Available: {available_dates}")
            
            for option in chain[target_date]:
                if (option.strike_price == strike_price and
                    option.option_type.value == option_type):
                    return InstrumentDetail(option.streamer_symbol, option)
            
            available_strikes = [opt.strike_price for opt in chain[target_date] if opt.option_type.value == option_type]
            raise ValueError(f"Option not found: {symbol} {expiration_date} {option_type} {strike_price}. Available strikes: {sorted(set(available_strikes))}")
        else:
            # Get equity instrument
            instrument = await Equity.a_get(session, symbol)
            return InstrumentDetail(symbol, instrument)
    
    return await asyncio.gather(*[lookup_single_instrument(spec) for spec in instrument_specs])


def build_order_legs(instrument_details: list[InstrumentDetail], legs: list[OrderLeg]) -> list:
    """Build order legs from instrument details and leg specifications."""
    if len(instrument_details) != len(legs):
        raise ValueError(f"Mismatched legs: {len(instrument_details)} instruments vs {len(legs)} leg specs")
    
    built_legs = []
    for detail, leg_spec in zip(instrument_details, legs, strict=True):
        instrument = detail.instrument
        order_action = (
            OrderAction(leg_spec.action) if isinstance(instrument, Option)
            else OrderAction.BUY if leg_spec.action == 'Buy' else OrderAction.SELL
        )
        built_legs.append(instrument.build_leg(Decimal(str(leg_spec.quantity)), order_action))
    return built_legs


async def _fetch_quotes_raw(session: Session, instrument_details: list[InstrumentDetail], timeout: float = 10.0) -> list[Any]:
    """Fetch raw Quote objects for price calculations (internal use)."""
    return await _stream_events(session, Quote, [d.streamer_symbol for d in instrument_details], timeout)


async def calculate_net_price(session: Session, instrument_details: list[InstrumentDetail], legs: list[OrderLeg]) -> float:
    """Calculate net price from current market quotes."""
    quotes = await _fetch_quotes_raw(session, instrument_details)
    
    net_price = 0.0
    for quote, detail, leg in zip(quotes, instrument_details, legs, strict=True):
        if quote.bid_price is not None and quote.ask_price is not None:
            mid_price = float(quote.bid_price + quote.ask_price) / 2
            leg_price = -mid_price if leg.action.startswith('Buy') else mid_price
            net_price += leg_price * leg.quantity
        else:
            inst = detail.instrument
            symbol_info = (
                f"{inst.underlying_symbol} {inst.option_type.value}{inst.strike_price} {inst.expiration_date}"
                if isinstance(inst, Option) else inst.symbol
            )
            logger.warning(f"Could not get bid/ask prices for {symbol_info}")
            raise ValueError(f"Could not get bid/ask for {symbol_info}")
    
    return round(net_price * 100) / 100


# =============================================================================
# ACCOUNT & POSITION ENDPOINTS
# =============================================================================

@app.get("/api/v1/balances")
async def get_balances(
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Get account balances and buying power."""
    session, account = session_and_account
    balances = await account.a_get_balances(session)
    return {k: v for k, v in balances.model_dump().items() if v is not None and v != 0}


@app.get("/api/v1/positions")
async def get_positions(
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Get all open positions with current values."""
    session, account = session_and_account
    positions = await account.a_get_positions(session, include_marks=True)
    return {
        "positions": [pos.model_dump() for pos in positions],
        "table": to_table(positions)
    }


@app.get("/api/v1/net-liquidating-value-history")
async def get_net_liquidating_value_history(
    time_back: Literal['1d', '1m', '3m', '6m', '1y', 'all'] = '1y',
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Get portfolio value history over time."""
    session, account = session_and_account
    history = await account.a_get_net_liquidating_value_history(session, time_back=time_back)
    return {
        "history": [h.model_dump() for h in history],
        "table": to_table(history)
    }


# =============================================================================
# MARKET DATA ENDPOINTS
# =============================================================================

@app.post("/api/v1/quotes")
async def get_quotes(
    instruments: list[InstrumentSpec],
    timeout: float = 10.0,
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Get live quotes for multiple stocks and/or options."""
    if not instruments:
        raise HTTPException(status_code=400, detail="At least one instrument is required")
    
    instrument_details = await get_instrument_details(session, instruments)
    
    try:
        quotes = await _stream_events(session, Quote, [d.streamer_symbol for d in instrument_details], timeout)
        return {
            "quotes": [q.model_dump() for q in quotes],
            "table": to_table(quotes)
        }
    except TimeoutError as e:
        logger.warning(f"Timeout getting quotes for {len(instruments)} instruments after {timeout}s")
        raise HTTPException(status_code=408, detail=f"Timeout getting quotes after {timeout}s") from e
    except Exception as e:
        logger.error(f"Error getting quotes for instruments {[i.symbol for i in instruments]}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting quotes: {e}") from e


@app.post("/api/v1/greeks")
async def get_greeks(
    options: list[InstrumentSpec],
    timeout: float = 10.0,
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Get Greeks (delta, gamma, theta, vega, rho) for multiple options."""
    if not options:
        raise HTTPException(status_code=400, detail="At least one option is required")
    
    option_details = await get_instrument_details(session, options)
    
    try:
        greeks = await _stream_events(session, Greeks, [d.streamer_symbol for d in option_details], timeout)
        return {
            "greeks": [g.model_dump() for g in greeks],
            "table": to_table(greeks)
        }
    except TimeoutError as e:
        logger.warning(f"Timeout getting Greeks for {len(options)} options after {timeout}s")
        raise HTTPException(status_code=408, detail=f"Timeout getting Greeks after {timeout}s") from e
    except Exception as e:
        logger.error(f"Error getting Greeks for options {[opt.symbol for opt in options]}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting Greeks: {e}") from e


@app.post("/api/v1/market-metrics")
async def get_market_metrics(
    symbols: list[str],
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Get market metrics including IV rank, percentile, beta, liquidity for multiple symbols."""
    metrics = await a_get_market_metrics(session, symbols)
    return {
        "metrics": [m.model_dump() for m in metrics],
        "table": to_table(metrics)
    }


@app.get("/api/v1/market-status")
async def market_status(
    exchanges: list[Literal['Equity', 'CME', 'CFE', 'Smalls']] | None = None,
    session: Session = Depends(get_session_only)
) -> list[dict[str, Any]]:
    """Get market status for each exchange including current open/closed state."""
    if exchanges is None:
        exchanges = ['Equity']
    market_sessions = await a_get_market_sessions(session, [ExchangeType(exchange) for exchange in exchanges])
    
    if not market_sessions:
        raise HTTPException(status_code=404, detail="No market sessions found")
    
    current_time = datetime.now(UTC)
    calendar = await a_get_market_holidays(session)
    is_holiday = current_time.date() in calendar.holidays
    is_half_day = current_time.date() in calendar.half_days
    
    results: list[dict[str, Any]] = []
    for ms in market_sessions:
        result: dict[str, Any] = {"exchange": ms.instrument_collection, "status": ms.status.value}
        
        if ms.status == MarketStatus.OPEN:
            if ms.close_at:
                result["close_at"] = ms.close_at.isoformat()
        else:
            open_at = _get_next_open_time(ms, current_time)
            if open_at:
                result["next_open"] = open_at.isoformat()
                result["time_until_open"] = humanize.naturaldelta(open_at - current_time)
            if is_holiday:
                result["is_holiday"] = True
            if is_half_day:
                result["is_half_day"] = True
        
        results.append(result)
    return results


def _get_next_open_time(session, current_time: datetime) -> datetime | None:
    """Determine next market open time based on current status."""
    if session.status == MarketStatus.PRE_MARKET:
        return session.open_at
    if session.status == MarketStatus.CLOSED:
        if session.open_at and current_time < session.open_at:
            return session.open_at
        if session.close_at and current_time > session.close_at and session.next_session:
            return session.next_session.open_at
    if session.status == MarketStatus.EXTENDED and session.next_session:
        return session.next_session.open_at
    return None


@app.get("/api/v1/search-symbols")
async def search_symbols(
    symbol: str,
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Search for symbols similar to the given search phrase."""
    async with rate_limiter:
        results = await a_symbol_search(session, symbol)
    return {
        "results": [r.model_dump() for r in results],
        "table": to_table(results)
    }


# =============================================================================
# HISTORY ENDPOINTS
# =============================================================================

@app.get("/api/v1/transaction-history")
async def get_transaction_history(
    days: int = 90,
    underlying_symbol: str | None = None,
    transaction_type: Literal["Trade", "Money Movement"] | None = None,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Get account transaction history including trades and money movements."""
    session, account = session_and_account
    start = date.today() - timedelta(days=days)
    
    trades = await _paginate(
        lambda offset: account.a_get_history(
            session, start_date=start, underlying_symbol=underlying_symbol,
            type=transaction_type, per_page=250, page_offset=offset
        ),
        page_size=250
    )
    return {
        "transactions": [t.model_dump() for t in trades],
        "table": to_table(trades)
    }


@app.get("/api/v1/order-history")
async def get_order_history(
    days: int = 7,
    underlying_symbol: str | None = None,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Get all order history for the last N days."""
    session, account = session_and_account
    start = date.today() - timedelta(days=days)
    
    orders = await _paginate(
        lambda offset: account.a_get_order_history(
            session, start_date=start, underlying_symbol=underlying_symbol,
            per_page=50, page_offset=offset
        ),
        page_size=50
    )
    return {
        "orders": [o.model_dump() for o in orders],
        "table": to_table(orders)
    }


# =============================================================================
# TRADING ENDPOINTS
# =============================================================================

@app.get("/api/v1/live-orders")
async def get_live_orders(
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Get currently active orders."""
    session, account = session_and_account
    orders = await account.a_get_live_orders(session)
    return {
        "orders": [o.model_dump() for o in orders],
        "table": to_table(orders)
    }


@app.post("/api/v1/place-order")
async def place_order(
    legs: list[OrderLeg],
    price: float | None = None,
    time_in_force: Literal['Day', 'GTC', 'IOC'] = 'Day',
    dry_run: bool = False,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Place multi-leg options/equity orders."""
    async with rate_limiter:
        if not legs:
            raise HTTPException(status_code=400, detail="At least one leg is required")
        
        session, account = session_and_account
        # Convert OrderLeg to InstrumentSpec for instrument lookup
        instrument_specs = [
            InstrumentSpec(
                symbol=leg.symbol,
                option_type=leg.option_type,
                strike_price=leg.strike_price,
                expiration_date=leg.expiration_date
            )
            for leg in legs
        ]
        instrument_details = await get_instrument_details(session, instrument_specs)
        
        # Calculate price if not provided
        if price is None:
            try:
                price = await calculate_net_price(session, instrument_details, legs)
                logger.info(f"Auto-calculated price ${price:.2f} for {len(legs)}-leg order")
            except Exception as e:
                logger.warning(f"Failed to auto-calculate price for order legs {[leg.symbol for leg in legs]}: {e!s}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not fetch quotes for price calculation: {e!s}. Please provide a price."
                ) from e
        
        result = await account.a_place_order(
            session,
            NewOrder(
                time_in_force=OrderTimeInForce(time_in_force),
                order_type=OrderType.LIMIT,
                legs=build_order_legs(instrument_details, legs),
                price=Decimal(str(price))
            ),
            dry_run=dry_run
        )
        return result.model_dump()


@app.post("/api/v1/replace-order/{order_id}")
async def replace_order(
    order_id: str,
    price: float,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Replace (modify) an existing order with a new price."""
    async with rate_limiter:
        session, account = session_and_account
        
        # Get the existing order
        live_orders = await account.a_get_live_orders(session)
        existing_order = next((order for order in live_orders if str(order.id) == order_id), None)
        
        if not existing_order:
            live_order_ids = [str(order.id) for order in live_orders]
            raise HTTPException(
                status_code=404,
                detail=f"Order {order_id} not found in live orders. Available orders: {live_order_ids}"
            )
        
        # Replace order with modified price
        result = await account.a_replace_order(
            session,
            int(order_id),
            NewOrder(
                time_in_force=existing_order.time_in_force,
                order_type=existing_order.order_type,
                legs=existing_order.legs,
                price=Decimal(str(price))
            )
        )
        return result.model_dump()


@app.delete("/api/v1/orders/{order_id}")
async def delete_order(
    order_id: str,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """Cancel an existing order."""
    session, account = session_and_account
    await account.a_delete_order(session, int(order_id))
    return {"success": True, "order_id": order_id}


# =============================================================================
# WATCHLIST ENDPOINTS
# =============================================================================

@app.get("/api/v1/watchlists")
async def get_watchlists(
    watchlist_type: Literal['public', 'private'] = 'private',
    name: str | None = None,
    session: Session = Depends(get_session_only)
) -> list[dict[str, Any]]:
    """Get watchlists for market insights and tracking."""
    watchlist_class = PublicWatchlist if watchlist_type == 'public' else PrivateWatchlist
    
    if name:
        return [(await watchlist_class.a_get(session, name)).model_dump()]
    return [w.model_dump() for w in await watchlist_class.a_get(session)]


@app.post("/api/v1/watchlists/private/manage")
async def manage_private_watchlist(
    action: Literal["add", "remove"],
    symbols: list[WatchlistSymbol],
    name: str = "main",
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Add or remove multiple symbols from a private watchlist."""
    
    if not symbols:
        raise HTTPException(status_code=400, detail="At least one symbol is required")
    
    if action == "add":
        try:
            watchlist = await PrivateWatchlist.a_get(session, name)
            for symbol_spec in symbols:
                symbol = symbol_spec.symbol
                instrument_type = InstrumentType(symbol_spec.instrument_type)
                watchlist.add_symbol(symbol, instrument_type)
            await watchlist.a_update(session)
            logger.info(f"Added {len(symbols)} symbols to existing watchlist '{name}'")
            return {"success": True, "action": "add", "watchlist": name, "symbols_count": len(symbols)}
        except Exception as e:
            logger.info(f"Watchlist '{name}' not found, creating new one: {e}")
            watchlist_entries = [{"symbol": s.symbol, "instrument_type": s.instrument_type} for s in symbols]
            watchlist = PrivateWatchlist(
                name=name,
                group_name="main",
                watchlist_entries=watchlist_entries
            )
            await watchlist.a_upload(session)
            logger.info(f"Created new watchlist '{name}' with {len(symbols)} symbols")
            return {"success": True, "action": "create", "watchlist": name, "symbols_count": len(symbols)}
    else:
        try:
            watchlist = await PrivateWatchlist.a_get(session, name)
            for symbol_spec in symbols:
                symbol = symbol_spec.symbol
                instrument_type = InstrumentType(symbol_spec.instrument_type)
                watchlist.remove_symbol(symbol, instrument_type)
            await watchlist.a_update(session)
            logger.info(f"Removed {len(symbols)} symbols from watchlist '{name}'")
            return {"success": True, "action": "remove", "watchlist": name, "symbols_count": len(symbols)}
        except Exception as e:
            logger.error(f"Failed to remove symbols from watchlist '{name}': {e}")
            raise HTTPException(status_code=500, detail=f"Failed to remove symbols: {e}") from e


@app.delete("/api/v1/watchlists/private/{name}")
async def delete_private_watchlist(
    name: str,
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Delete a private watchlist."""
    await PrivateWatchlist.a_remove(session, name)
    return {"success": True, "watchlist": name}


# =============================================================================
# UTILITY ENDPOINTS
# =============================================================================

@app.get("/api/v1/current-time")
async def get_current_time_nyc(
    session: Session = Depends(get_session_only)
) -> dict[str, str]:
    """Get current time in New York timezone (market time)."""
    return {"current_time_nyc": now_in_new_york().isoformat()}


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Health check endpoint (no authentication required)."""
    return {"status": "healthy", "service": "tasty-agent-http"}


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Run the HTTP server."""
    import uvicorn
    
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    logger.info(f"Starting TastyTrade HTTP server on {host}:{port}")
    logger.info("Set API_KEY environment variable to secure the server")
    logger.info("Set CORS_ORIGINS environment variable to restrict CORS (comma-separated)")
    
    uvicorn.run(
        "tasty_agent.http_server:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info"
    )


if __name__ == "__main__":
    main()

