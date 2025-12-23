"""
HTTP Server for TastyTrade API - Allows multiple kiosks to connect via REST API.
- Account info endpoints: Direct HTTP (no Claude)
- Analytics endpoints: Uses Claude via MCP tools
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
from fastapi import FastAPI, HTTPException, Header, Depends, Query, status, Request
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message, ASGIApp
from starlette.requests import Request as StarletteRequest
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
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

# Import MCP tools and shared utilities
from tasty_agent.server import (
    get_balances as mcp_get_balances,
    get_positions as mcp_get_positions,
    get_quotes as mcp_get_quotes,
    get_greeks as mcp_get_greeks,
    get_market_metrics as mcp_get_market_metrics,
    place_order as mcp_place_order,
    get_live_orders as mcp_get_live_orders,
    ServerContext as MCPServerContext,
    get_instrument_details as mcp_get_instrument_details,
    InstrumentSpec as MCPInstrumentSpec
)

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
    # Use absolute path to credentials.json in project root
    project_root = Path(__file__).parent.parent
    creds_file = project_root / "credentials.json"
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


def save_credentials(credentials: dict[str, tuple[str, str]]) -> None:
    """Save credentials to JSON file."""
    project_root = Path(__file__).parent.parent
    creds_file = project_root / "credentials.json"
    
    # Convert tuple format to dict format for JSON
    creds_dict = {}
    for api_key, (client_secret, refresh_token) in credentials.items():
        creds_dict[api_key] = {
            "client_secret": client_secret,
            "refresh_token": refresh_token
        }
    
    try:
        with open(creds_file, "w") as f:
            json.dump(creds_dict, f, indent=2)
        logger.info(f"Saved credentials to {creds_file}")
    except Exception as e:
        logger.error(f"Failed to save credentials.json: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save credentials: {e}"
        ) from e


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

# Claude agent cache: API key -> Agent
api_key_agents: dict[str, Agent] = {}

# Track current model identifier to invalidate cache when it changes
# Initialize with current default to detect changes after restart
_current_model_identifier: str | None = os.getenv("MODEL_IDENTIFIER", "anthropic:claude-3-5-haiku-20241022")


def get_api_key_context(api_key: str) -> ServerContext:
    """Get or create ServerContext for an API key with lazy initialization."""
    global api_key_sessions
    
    # Check if context already exists
    if api_key in api_key_sessions:
        context = api_key_sessions[api_key]
        session = context.session
        
        # Refresh if expired or expiring within 5 seconds
        # Per TastyTrade FAQ: Access tokens expire every 15 minutes
        time_until_expiry = (session.session_expiration - now_in_new_york()).total_seconds()
        if time_until_expiry < 5:
            logger.info(f"Session expiring in {time_until_expiry:.0f}s for API key {api_key[:8]}..., refreshing...")
            try:
                session.refresh()
                logger.info(f"Session refreshed, new expiration: {session.session_expiration}")
            except Exception as refresh_err:
                error_msg = str(refresh_err)
                if "invalid_grant" in error_msg or "Grant revoked" in error_msg:
                    # Clear invalid session
                    api_key_sessions.pop(api_key, None)
                    raise HTTPException(
                        status_code=401,
                        detail=f"TastyTrade refresh token is invalid or revoked. Please update your credentials using POST /api/v1/credentials. Error: {error_msg}"
                    ) from refresh_err
                else:
                    raise
        
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
        error_msg = str(e)
        logger.error(f"Failed to authenticate API key {api_key[:8]}... with Tastytrade: {e}")
        
        # Check for specific error types
        if "invalid_grant" in error_msg or "Grant revoked" in error_msg:
            raise HTTPException(
                status_code=401,
                detail=f"TastyTrade refresh token is invalid or revoked. Please update your credentials using POST /api/v1/credentials. "
                       f"To get a new refresh token, visit https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications "
                       f"and create a new Personal OAuth Grant. Error: {error_msg}"
            ) from e
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to authenticate with Tastytrade: {error_msg}"
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


# Cache for instruction file content
_instruction_prompt_cache: str | None = None


def load_instruction_prompt() -> str:
    """Load the instruction prompt file. Cached after first load."""
    global _instruction_prompt_cache
    
    if _instruction_prompt_cache is not None:
        return _instruction_prompt_cache
    
    # Get the path to instruction_live_prompt.md
    project_root = Path(__file__).parent
    instruction_file = project_root / "instruction_live_prompt.md"
    
    if not instruction_file.exists():
        logger.warning(f"Instruction file not found at {instruction_file}. Agent will run without custom instructions.")
        return ""
    
    try:
        with open(instruction_file, "r", encoding="utf-8") as f:
            _instruction_prompt_cache = f.read()
        logger.info(f"Loaded instruction prompt from {instruction_file} ({len(_instruction_prompt_cache):,} chars)")
        return _instruction_prompt_cache
    except Exception as e:
        logger.error(f"Failed to load instruction file {instruction_file}: {e}")
        return ""


def get_claude_agent(api_key: str) -> Agent:
    """Get or create Claude agent for an API key. Uses Claude API from environment variables."""
    global api_key_agents, _current_model_identifier
    
    # Get model identifier (default to Claude Haiku 3.5 - faster and cheaper)
    model_identifier = os.getenv("MODEL_IDENTIFIER", "anthropic:claude-3-5-haiku-20241022")
    
    # If model identifier changed, clear all cached agents
    if _current_model_identifier is not None and _current_model_identifier != model_identifier:
        logger.info(f"Model identifier changed from {_current_model_identifier} to {model_identifier}. Clearing agent cache.")
        api_key_agents.clear()
    
    # Always update current model identifier
    if _current_model_identifier != model_identifier:
        logger.info(f"Using model identifier: {model_identifier}")
        _current_model_identifier = model_identifier
    
    if api_key in api_key_agents:
        return api_key_agents[api_key]
    
    # Get Claude API key from environment (lines 6-10 in .env typically)
    claude_api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    if not claude_api_key:
        raise HTTPException(
            status_code=500,
            detail="Claude API key not configured. Set ANTHROPIC_API_KEY or CLAUDE_API_KEY environment variable."
        )
    
    # Load instruction prompt
    system_prompt = load_instruction_prompt()
    
    try:
        # Create MCP server connection with credentials for this API key
        client_secret, refresh_token = api_key_credentials[api_key]
        
        # Validate credentials before creating MCP server
        try:
            test_session = Session(client_secret, refresh_token)
            test_accounts = Account.get(test_session)
            logger.info(f"Credentials validated for API key {api_key[:8]}... Found {len(test_accounts)} account(s)")
        except Exception as auth_error:
            error_msg = str(auth_error)
            if "invalid_grant" in error_msg or "Grant revoked" in error_msg:
                logger.error(f"TastyTrade credentials revoked or invalid for API key {api_key[:8]}...")
                raise HTTPException(
                    status_code=401,
                    detail=f"TastyTrade credentials are invalid or revoked. Please update your credentials using POST /api/v1/credentials. Error: {error_msg}"
                ) from auth_error
            else:
                logger.error(f"Failed to validate TastyTrade credentials for API key {api_key[:8]}...: {auth_error}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to authenticate with TastyTrade: {auth_error}"
                ) from auth_error
        
        env = dict(os.environ)
        env["TASTYTRADE_CLIENT_SECRET"] = client_secret
        env["TASTYTRADE_REFRESH_TOKEN"] = refresh_token
        env["ANTHROPIC_API_KEY"] = claude_api_key
        
        server = MCPServerStdio(
            'python', args=['run_mcp_stdio.py'], timeout=120, env=env
        )
        
        # Create agent with system prompt if available
        if system_prompt:
            # pydantic-ai Agent accepts system_prompt parameter
            agent = Agent(model_identifier, toolsets=[server], system_prompt=system_prompt)
            logger.info(f"Created Claude agent for API key {api_key[:8]}... with model {model_identifier} and custom instructions ({len(system_prompt):,} chars)")
        else:
            agent = Agent(model_identifier, toolsets=[server])
            logger.info(f"Created Claude agent for API key {api_key[:8]}... with model {model_identifier} without custom instructions")
        
        api_key_agents[api_key] = agent
        return agent
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Failed to create Claude agent for API key {api_key[:8]}...: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Claude agent: {e}"
        ) from e


def create_mcp_context(session: Session, account: Account) -> Context:
    """Create a mock MCP Context for calling MCP tools from HTTP server."""
    # Create a mock request context with lifespan_context
    class MockRequestContext:
        def __init__(self, lifespan_context: MCPServerContext):
            self.lifespan_context = lifespan_context
    
    # Create a mock Context
    class MockContext:
        def __init__(self, session: Session, account: Account):
            mcp_server_context = MCPServerContext(session=session, account=account)
            self.request_context = MockRequestContext(mcp_server_context)
    
    return MockContext(session, account)


async def keep_sessions_alive():
    """
    Background task to periodically refresh sessions before they expire.
    
    Per TastyTrade API FAQ: Access tokens expire every 15 minutes.
    This task proactively refreshes them every 5 minutes to prevent expiration.
    Refresh tokens are long-lived and don't need manual renewal.
    """
    # Check every 5 minutes (sessions expire after 15 minutes per TastyTrade FAQ)
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
                        try:
                            session.refresh()
                            logger.debug(f"Session refreshed, new expiration: {session.session_expiration}")
                        except Exception as refresh_err:
                            error_msg = str(refresh_err)
                            if "invalid_grant" in error_msg or "Grant revoked" in error_msg:
                                logger.error(f"Refresh token revoked for API key {api_key[:8]}... - credentials need to be updated")
                                # Remove invalid session so it can be recreated with new credentials
                                api_key_sessions.pop(api_key, None)
                                # Also clear agent cache
                                global api_key_agents
                                if api_key in api_key_agents:
                                    del api_key_agents[api_key]
                            else:
                                logger.error(f"Failed to refresh session for API key {api_key[:8]}...: {refresh_err}")
                                # Remove invalid session so it can be recreated on next request
                                api_key_sessions.pop(api_key, None)
                except Exception as e:
                    logger.error(f"Error checking session for API key {api_key[:8]}...: {e}")
                    # Continue running even if there's an error
        except asyncio.CancelledError:
            logger.info("Session keep-alive task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in keep-alive task: {e}")
            # Continue running even if there's an error


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manages Tastytrade credential loading and session lifecycle."""
    global api_key_credentials, api_key_sessions, api_key_agents, _current_model_identifier
    
    # Load credentials at startup
    api_key_credentials = load_credentials()
    
    if not api_key_credentials:
        logger.warning(
            "No credentials loaded. Set TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN "
            "environment variables, or configure TASTYTRADE_CREDENTIALS_JSON, or create credentials.json"
        )
    
    logger.info(f"Loaded credentials for {len(api_key_credentials)} API key(s)")
    
    # Clear agent cache on startup to ensure fresh agents with current model
    model_identifier = os.getenv("MODEL_IDENTIFIER", "anthropic:claude-3-5-haiku-20241022")
    if api_key_agents:
        logger.info(f"Clearing {len(api_key_agents)} cached agent(s) on startup to use model: {model_identifier}")
        api_key_agents.clear()
    _current_model_identifier = model_identifier
    logger.info(f"Using Claude model: {model_identifier}")
    
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
    api_key_agents.clear()


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


# ASGI wrapper to increase max body size for image uploads
# Starlette's default max_request_size is 1MB, we increase it to 50MB
class LargeBodyASGIApp:
    """ASGI wrapper to increase max request body size for image uploads."""
    def __init__(self, app: ASGIApp):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Set max_request_size in the scope (50MB = 52428800 bytes)
            # Starlette's Request class reads this from the scope
            scope = dict(scope)
            scope['max_request_size'] = 52428800
            await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)

# FastAPI app (routes will be added to this via decorators)
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


class CredentialEntry(BaseModel):
    """Credential entry for an API key."""
    api_key: str = Field(..., description="API key identifier")
    client_secret: str = Field(..., description="TastyTrade client secret")
    refresh_token: str = Field(..., description="TastyTrade refresh token")


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


def build_new_order(
    order_type: str,
    legs: list,
    time_in_force: str,
    price: Decimal | None = None,
    stop_price: Decimal | None = None,
    trail_price: Decimal | None = None,
    trail_percent: Decimal | None = None
) -> NewOrder:
    """
    Build a NewOrder with proper parameters based on order type.
    
    Args:
        order_type: 'Market', 'Limit', 'Stop', 'StopLimit', 'TrailingStop'
        legs: Built order legs
        time_in_force: 'Day', 'GTC', 'IOC', etc.
        price: Limit price (required for Limit, StopLimit)
        stop_price: Stop trigger price (required for Stop, StopLimit)
        trail_price: Trailing stop amount in dollars (for TrailingStop)
        trail_percent: Trailing stop percentage (for TrailingStop, alternative to trail_price)
    
    Returns:
        Configured NewOrder object
    """
    # Convert string order_type to enum, handling different naming conventions
    order_type_upper = order_type.upper().replace('_', '')
    order_type_mapping = {
        'MARKET': 'MARKET',
        'LIMIT': 'LIMIT',
        'STOP': 'STOP',
        'STOPLIMIT': 'STOP_LIMIT',
        'STOP_LIMIT': 'STOP_LIMIT',
        'TRAILINGSTOP': 'TRAILING_STOP',
        'TRAILING_STOP': 'TRAILING_STOP'
    }
    
    enum_name = order_type_mapping.get(order_type_upper)
    if enum_name is None:
        # Try to use the order_type string directly as enum value name
        try:
            order_type_enum = OrderType[order_type_upper]
        except (KeyError, AttributeError):
            # Fall back to using the string directly and let OrderType constructor handle it
            order_type_enum = OrderType(order_type)
    else:
        try:
            order_type_enum = OrderType[enum_name]
        except (KeyError, AttributeError):
            # Fall back to using the string directly
            order_type_enum = OrderType(order_type)
    
    time_in_force_enum = OrderTimeInForce(time_in_force)
    
    # Build base order kwargs
    order_kwargs = {
        "order_type": order_type_enum,
        "time_in_force": time_in_force_enum,
        "legs": legs
    }
    
    # Add parameters based on order type (try enum comparison first, then string fallback)
    order_type_str = str(order_type_enum).upper()
    if hasattr(order_type_enum, 'value'):
        order_type_str = str(order_type_enum.value).upper()
    
    # Determine order type category using string matching (works regardless of enum naming)
    is_market = 'MARKET' in order_type_str
    is_limit_only = 'LIMIT' in order_type_str and 'STOP' not in order_type_str
    is_stop_limit = 'STOP' in order_type_str and 'LIMIT' in order_type_str
    is_stop_only = 'STOP' in order_type_str and 'TRAILING' not in order_type_str and 'LIMIT' not in order_type_str
    is_trailing_stop = 'TRAILING' in order_type_str or 'TRAIL' in order_type_str
    
    if is_market:
        # Market orders don't need price
        pass
    elif is_limit_only:
        if price is None:
            raise ValueError("price is required for Limit orders")
        order_kwargs["price"] = price
    elif is_stop_limit:
        if price is None:
            raise ValueError("price is required for StopLimit orders")
        if stop_price is None:
            raise ValueError("stop_price is required for StopLimit orders")
        order_kwargs["price"] = price
        order_kwargs["stop_price"] = stop_price
    elif is_stop_only:
        if stop_price is None:
            raise ValueError("stop_price is required for Stop orders")
        order_kwargs["stop_price"] = stop_price
    elif is_trailing_stop:
        if trail_price is None and trail_percent is None:
            raise ValueError("trail_price or trail_percent is required for TrailingStop orders")
        if trail_price is not None:
            order_kwargs["trail_price"] = trail_price
        if trail_percent is not None:
            order_kwargs["trail_percent"] = trail_percent
    else:
        # For unknown order types, try to use price if provided
        if price is not None:
            order_kwargs["price"] = price
        if stop_price is not None:
            order_kwargs["stop_price"] = stop_price
    
    return NewOrder(**order_kwargs)


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
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get live quotes for multiple stocks and/or options. Uses Claude via MCP tools."""
    if not instruments:
        raise HTTPException(status_code=400, detail="At least one instrument is required")
    
    try:
        # Use Claude agent to get quotes
        agent = get_claude_agent(api_key)
        
        # Build prompt for Claude
        instruments_json = json.dumps([inst.model_dump() for inst in instruments], default=str)
        prompt = f"Get live quotes for these instruments: {instruments_json}. Use the get_quotes tool with timeout={timeout}."
        
        # Run Claude agent with context manager
        async with agent:
            result = await agent.run(prompt)
        
        # Extract the table result from Claude's response
        table_result = result.output if hasattr(result, 'output') else (result.data if isinstance(result.data, str) else str(result.data))
        claude_analysis = result.output if hasattr(result, 'output') else None
        
        # Also get raw quotes for structured response
        session = get_valid_session(api_key)
        instrument_details = await get_instrument_details(session, instruments)
        quotes = await _stream_events(session, Quote, [d.streamer_symbol for d in instrument_details], timeout)
        
        return {
            "quotes": [q.model_dump() for q in quotes],
            "table": table_result,
            "claude_analysis": claude_analysis
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quotes via Claude: {e}")
        # Fallback to direct call if Claude fails
        logger.warning(f"Falling back to direct quotes call: {e}")
        try:
            session = get_valid_session(api_key)
            instrument_details = await get_instrument_details(session, instruments)
            quotes = await _stream_events(session, Quote, [d.streamer_symbol for d in instrument_details], timeout)
            return {
                "quotes": [q.model_dump() for q in quotes],
                "table": to_table(quotes),
                "claude_analysis": None,
                "claude_error": str(e)
            }
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            raise HTTPException(status_code=500, detail=f"Error getting quotes: {fallback_error}") from fallback_error


@app.post("/api/v1/greeks")
async def get_greeks(
    options: list[InstrumentSpec],
    timeout: float = 10.0,
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get Greeks (delta, gamma, theta, vega, rho) for multiple options. Uses Claude via MCP tools."""
    if not options:
        raise HTTPException(status_code=400, detail="At least one option is required")
    
    try:
        # Use Claude agent to get Greeks
        agent = get_claude_agent(api_key)
        
        # Build prompt for Claude
        options_json = json.dumps([opt.model_dump() for opt in options], default=str)
        prompt = f"Get Greeks for these options: {options_json}. Use the get_greeks tool with timeout={timeout}."
        
        # Run Claude agent
        result = await agent.run(prompt)
        
        # Extract the table result from Claude's response
        table_result = result.data if isinstance(result.data, str) else str(result.data)
        
        # Also get raw Greeks for structured response
        session = get_valid_session(api_key)
        option_details = await get_instrument_details(session, options)
        greeks = await _stream_events(session, Greeks, [d.streamer_symbol for d in option_details], timeout)
        
        return {
            "greeks": [g.model_dump() for g in greeks],
            "table": table_result,
            "claude_analysis": result.data if hasattr(result, 'data') else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Greeks via Claude: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting Greeks: {e}") from e


@app.post("/api/v1/market-metrics")
async def get_market_metrics(
    symbols: list[str],
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get market metrics including IV rank, percentile, beta, liquidity for multiple symbols. Uses Claude via MCP tools."""
    try:
        # Use Claude agent to get market metrics
        agent = get_claude_agent(api_key)
        
        # Build prompt for Claude
        symbols_json = json.dumps(symbols)
        prompt = f"Get market metrics for these symbols: {symbols_json}. Use the get_market_metrics tool."
        
        # Run Claude agent
        result = await agent.run(prompt)
        
        # Extract the table result from Claude's response
        table_result = result.data if isinstance(result.data, str) else str(result.data)
        
        # Also get raw metrics for structured response
        session = get_valid_session(api_key)
        metrics = await a_get_market_metrics(session, symbols)
        
        return {
            "metrics": [m.model_dump() for m in metrics],
            "table": table_result,
            "claude_analysis": result.data if hasattr(result, 'data') else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting market metrics via Claude: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting market metrics: {e}") from e


@app.get("/api/v1/market-status")
async def market_status(
    exchanges: list[Literal['Equity', 'CME', 'CFE', 'Smalls']] | None = None,
    api_key: str = Depends(verify_api_key)
) -> list[dict[str, Any]]:
    """Get market status for each exchange including current open/closed state. Uses Claude for analysis."""
    if exchanges is None:
        exchanges = ['Equity']
    
    try:
        # Get raw data first
        session = get_valid_session(api_key)
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
        
        # Use Claude for additional analysis/insights
        try:
            agent = get_claude_agent(api_key)
            exchanges_str = ", ".join(exchanges)
            prompt = f"Analyze the current market status for {exchanges_str} exchanges. Provide insights about trading opportunities and market conditions."
            claude_result = await agent.run(prompt)
            # Add Claude analysis to response if available
            if hasattr(claude_result, 'data'):
                results.append({"claude_analysis": claude_result.data})
        except Exception as e:
            logger.warning(f"Claude analysis failed for market-status: {e}")
            # Continue without Claude analysis
        
        return results
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting market status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting market status: {e}") from e


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
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Search for symbols similar to the given search phrase. Uses Claude via MCP tools."""
    try:
        # Use Claude agent to search symbols
        agent = get_claude_agent(api_key)
        
        # Build prompt for Claude
        prompt = f"Search for symbols similar to '{symbol}'. Use the search_symbols tool."
        
        # Run Claude agent
        result = await agent.run(prompt)
        
        # Extract the table result from Claude's response
        table_result = result.data if isinstance(result.data, str) else str(result.data)
        
        # Also get raw results for structured response
        session = get_valid_session(api_key)
        async with rate_limiter:
            results = await a_symbol_search(session, symbol)
        
        return {
            "results": [r.model_dump() for r in results],
            "table": table_result,
            "claude_analysis": result.data if hasattr(result, 'data') else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching symbols via Claude: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching symbols: {e}") from e


@app.get("/api/v1/option-chain")
async def get_option_chain(
    symbol: str,
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get complete option chain for a symbol, returning all available expiration dates, strikes, and option contracts."""
    try:
        session = get_valid_session(api_key)
        chain = await get_cached_option_chain(session, symbol.upper())
        
        # Convert chain to a structured format
        # chain is a dict: {date: [Option, Option, ...]}
        chain_data = {}
        expiration_dates = []
        all_options = []  # Flat list of all options
        
        for exp_date, options in chain.items():
            exp_date_str = exp_date.strftime("%Y-%m-%d") if hasattr(exp_date, 'strftime') else str(exp_date)
            expiration_dates.append(exp_date_str)
            
            # Group options by type (C/P) and collect strikes
            calls = []
            puts = []
            
            for option in options:
                # Get option type
                option_type = option.option_type.value if hasattr(option.option_type, 'value') else str(option.option_type)
                
                option_data = {
                    "strike_price": float(option.strike_price),
                    "option_type": option_type,
                    "streamer_symbol": option.streamer_symbol,
                    "expiration_date": exp_date_str,
                    "symbol": option.streamer_symbol  # For easy reference
                }
                
                # Add to flat list
                all_options.append(option_data)
                
                # Add to calls or puts
                if option_type == 'C':
                    calls.append(option_data)
                else:
                    puts.append(option_data)
            
            chain_data[exp_date_str] = {
                "calls": sorted(calls, key=lambda x: x["strike_price"]),
                "puts": sorted(puts, key=lambda x: x["strike_price"]),
                "strikes": sorted(set([opt["strike_price"] for opt in calls + puts])),
                "total_options": len(calls) + len(puts)
            }
        
        # Build a summary table
        today = date.today()
        
        table_lines = [f"Complete Option Chain for {symbol.upper()}\n"]
        table_lines.append(f"{'Expiration':<12} {'DTE':<6} {'Calls':<8} {'Puts':<8} {'Strikes':<10} {'Total':<8}")
        table_lines.append("-" * 60)
        
        for exp_date_str in sorted(expiration_dates):
            exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            calls_count = len(chain_data[exp_date_str]["calls"])
            puts_count = len(chain_data[exp_date_str]["puts"])
            strikes_count = len(chain_data[exp_date_str]["strikes"])
            total_count = chain_data[exp_date_str]["total_options"]
            
            table_lines.append(f"{exp_date_str:<12} {dte:<6} {calls_count:<8} {puts_count:<8} {strikes_count:<10} {total_count:<8}")
        
        table = "\n".join(table_lines)
        
        return {
            "symbol": symbol.upper(),
            "total_expirations": len(expiration_dates),
            "total_options": len(all_options),
            "expiration_dates": sorted(expiration_dates),
            "chain": chain_data,  # Organized by expiration
            "all_options": sorted(all_options, key=lambda x: (x["expiration_date"], x["strike_price"], x["option_type"])),  # Flat list of all options
            "table": table
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting option chain: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting option chain: {e}") from e


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
    order_type: Literal['Market', 'Limit', 'Stop', 'StopLimit', 'TrailingStop'] = 'Limit',
    price: float | None = None,
    stop_price: float | None = None,
    trail_price: float | None = None,
    trail_percent: float | None = None,
    time_in_force: Literal['Day', 'GTC', 'IOC'] = 'Day',
    dry_run: bool = False,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """
    Place multi-leg options/equity orders with support for various order types.
    
    Order Types:
    - Market: Execute immediately at market price (no price parameter needed)
    - Limit: Execute at specified price or better (price required)
    - Stop: Trigger market order when stop_price is hit (stop_price required)
    - StopLimit: Trigger limit order at price when stop_price is hit (both price and stop_price required)
    - TrailingStop: Trailing stop order (trail_price or trail_percent required)
    """
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
        built_legs = build_order_legs(instrument_details, legs)
        
        # Convert prices to Decimal
        price_decimal = Decimal(str(price)) if price is not None else None
        stop_price_decimal = Decimal(str(stop_price)) if stop_price is not None else None
        trail_price_decimal = Decimal(str(trail_price)) if trail_price is not None else None
        trail_percent_decimal = Decimal(str(trail_percent)) if trail_percent is not None else None
        
        # For Limit and StopLimit orders, calculate price if not provided
        if order_type in ['Limit', 'StopLimit'] and price_decimal is None:
            try:
                calculated_price = await calculate_net_price(session, instrument_details, legs)
                price_decimal = Decimal(str(calculated_price))
                logger.info(f"Auto-calculated price ${float(price_decimal):.2f} for {len(legs)}-leg {order_type} order")
            except Exception as e:
                logger.warning(f"Failed to auto-calculate price for order legs {[leg.symbol for leg in legs]}: {e!s}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not fetch quotes for price calculation: {e!s}. Please provide a price."
                ) from e
        
        # Build and place the order
        try:
            new_order = build_new_order(
                order_type=order_type,
                legs=built_legs,
                time_in_force=time_in_force,
                price=price_decimal,
                stop_price=stop_price_decimal,
                trail_price=trail_price_decimal,
                trail_percent=trail_percent_decimal
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        
        result = await account.a_place_order(session, new_order, dry_run=dry_run)
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
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get watchlists for market insights and tracking. Uses Claude for analysis."""
    try:
        # Get raw watchlist data
        session = get_valid_session(api_key)
        watchlist_class = PublicWatchlist if watchlist_type == 'public' else PrivateWatchlist
        
        if name:
            watchlists = [(await watchlist_class.a_get(session, name)).model_dump()]
        else:
            watchlists = [w.model_dump() for w in await watchlist_class.a_get(session)]
        
        # Use Claude for analysis
        try:
            agent = get_claude_agent(api_key)
            watchlists_json = json.dumps(watchlists, default=str)
            prompt = f"Analyze these {watchlist_type} watchlists: {watchlists_json}. Use the get_watchlists tool and provide insights about the symbols, trends, and potential trading opportunities."
            claude_result = await agent.run(prompt)
            
            return {
                "watchlists": watchlists,
                "claude_analysis": claude_result.data if hasattr(claude_result, 'data') else str(claude_result)
            }
        except Exception as e:
            logger.warning(f"Claude analysis failed for watchlists: {e}")
            return {"watchlists": watchlists}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting watchlists: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting watchlists: {e}") from e


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


@app.post("/api/v1/credentials")
async def add_or_update_credentials(
    credential: CredentialEntry
) -> dict[str, Any]:
    """Add or update credentials for an API key. Saves to credentials.json file."""
    global api_key_credentials
    
    # Load existing credentials
    project_root = Path(__file__).parent.parent
    creds_file = project_root / "credentials.json"
    existing_creds = {}
    
    if creds_file.exists():
        try:
            with open(creds_file, "r") as f:
                existing_creds = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read existing credentials: {e}")
    
    # Update with new credential
    existing_creds[credential.api_key] = {
        "client_secret": credential.client_secret,
        "refresh_token": credential.refresh_token
    }
    
    # Convert to tuple format for internal storage
    updated_credentials = {}
    for api_key, cred_data in existing_creds.items():
        if isinstance(cred_data, dict):
            client_secret = cred_data.get("client_secret")
            refresh_token = cred_data.get("refresh_token")
            if client_secret and refresh_token:
                updated_credentials[api_key] = (client_secret, refresh_token)
    
    # Save to file
    save_credentials(updated_credentials)
    
    # Reload credentials in memory
    api_key_credentials = updated_credentials
    
    # Clear any existing session for this API key to force re-authentication
    if credential.api_key in api_key_sessions:
        del api_key_sessions[credential.api_key]
        logger.info(f"Cleared existing session for API key {credential.api_key[:8]}...")
    
    # Clear any existing agent for this API key to force recreation with new credentials
    global api_key_agents
    if credential.api_key in api_key_agents:
        del api_key_agents[credential.api_key]
        logger.info(f"Cleared existing agent for API key {credential.api_key[:8]}... to use new credentials")
    
    logger.info(f"Added/updated credentials for API key {credential.api_key[:8]}...")
    return {
        "success": True,
        "api_key": credential.api_key,
        "message": "Credentials saved successfully. Existing sessions and agents cleared."
    }


@app.get("/api/v1/credentials")
async def list_credentials() -> dict[str, Any]:
    """List all configured API keys (without sensitive data)."""
    global api_key_credentials
    
    api_keys = []
    for api_key in api_key_credentials.keys():
        api_keys.append({
            "api_key": api_key,
            "configured": True
        })
    
    return {
        "api_keys": api_keys,
        "count": len(api_keys)
    }


@app.delete("/api/v1/credentials/{api_key}")
async def delete_credentials(api_key: str) -> dict[str, Any]:
    """Delete credentials for an API key."""
    global api_key_credentials
    
    # Load existing credentials
    project_root = Path(__file__).parent.parent
    creds_file = project_root / "credentials.json"
    existing_creds = {}
    
    if creds_file.exists():
        try:
            with open(creds_file, "r") as f:
                existing_creds = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read existing credentials: {e}")
    
    # Remove the API key
    if api_key not in existing_creds:
        raise HTTPException(
            status_code=404,
            detail=f"API key '{api_key}' not found in credentials"
        )
    
    del existing_creds[api_key]
    
    # Convert to tuple format for internal storage
    updated_credentials = {}
    for key, cred_data in existing_creds.items():
        if isinstance(cred_data, dict):
            client_secret = cred_data.get("client_secret")
            refresh_token = cred_data.get("refresh_token")
            if client_secret and refresh_token:
                updated_credentials[key] = (client_secret, refresh_token)
    
    # Save to file
    save_credentials(updated_credentials)
    
    # Reload credentials in memory
    api_key_credentials = updated_credentials
    
    # Clear any existing session for this API key
    if api_key in api_key_sessions:
        del api_key_sessions[api_key]
        logger.info(f"Cleared existing session for API key {api_key[:8]}...")
    
    # Clear any existing agent for this API key
    global api_key_agents
    if api_key in api_key_agents:
        del api_key_agents[api_key]
        logger.info(f"Cleared existing agent for API key {api_key[:8]}...")
    
    logger.info(f"Deleted credentials for API key {api_key[:8]}...")
    return {
        "success": True,
        "api_key": api_key,
        "message": "Credentials deleted successfully. Existing sessions and agents cleared."
    }


# =============================================================================
# CHAT ENDPOINT
# =============================================================================

class ChatMessage(BaseModel):
    """Chat message model."""
    role: Literal["user", "assistant"] = "user"
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat request model."""
    message: str = Field(..., description="User message to send to the agent")
    message_history: list[ChatMessage] | None = Field(None, description="Optional conversation history")
    images: list[str] | None = Field(None, description="Optional list of base64-encoded images")


class ChatResponse(BaseModel):
    """Chat response model."""
    response: str = Field(..., description="Agent's response")
    message_history: list[ChatMessage] = Field(..., description="Updated conversation history")


@app.post("/api/v1/chat/stream", include_in_schema=True)
async def chat_stream(
    request: ChatRequest,
    api_key: str = Depends(verify_api_key)
):
    """Stream chat responses from the Claude agent using Server-Sent Events (SSE)."""
    async def generate_stream():
        try:
            try:
                agent = get_claude_agent(api_key)
            except HTTPException as http_err:
                yield f"data: {json.dumps({'type': 'error', 'error': str(http_err.detail)})}\n\n"
                return
            except Exception as agent_err:
                logger.error(f"Failed to get/create agent for API key {api_key[:8]}...: {agent_err}")
                yield f"data: {json.dumps({'type': 'error', 'error': f'Failed to initialize AI agent: {str(agent_err)}'})}\n\n"
                return
            
            # Build user message with history context and images
            user_message = request.message
            if request.message_history:
                context_parts = []
                for msg in request.message_history:
                    context_parts.append(f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}")
                context = "\n".join(context_parts)
                user_message = f"Previous conversation:\n{context}\n\nCurrent question: {request.message}"
            
            # Prepare message content with images if provided
            message_content = user_message
            if request.images:
                image_count = len(request.images)
                logger.info(f"Processing chat request with {image_count} image(s)")
                # Include image references in the message
                # The images are base64-encoded and will be processed by the agent
                image_refs = "\n".join([f"[Image {i+1} attached (base64)]" for i in range(image_count)])
                message_content = f"{user_message}\n\nAttached {image_count} image(s)."
            
            logger.info(f"Starting streaming Claude agent run for message: {message_content[:100]}...")
            
            # Send initial "thinking" event for immediate feedback
            yield f"data: {json.dumps({'type': 'status', 'content': 'Processing request...'})}\n\n"
            
            try:
                async with agent:
                    # Send "analyzing" status
                    yield f"data: {json.dumps({'type': 'status', 'content': 'Analyzing market data and preparing response...'})}\n\n"
                    
                    # Run the agent with message
                    # Note: pydantic-ai's agent.run() accepts a prompt string
                    # Images can be included in the message content if the model supports it
                    # For now, we'll pass the message and let the agent handle it
                    start_time = asyncio.get_event_loop().time()
                    result = await asyncio.wait_for(
                        agent.run(message_content),
                        timeout=90.0
                    )
                    elapsed_time = asyncio.get_event_loop().time() - start_time
                    logger.info(f"Agent completed in {elapsed_time:.2f}s")
                    
                    # Extract response text
                    response_text = None
                    if hasattr(result, 'output') and result.output is not None:
                        response_text = str(result.output)
                    elif hasattr(result, 'data') and result.data is not None:
                        response_text = str(result.data)
                    elif hasattr(result, 'all_messages'):
                        # Try to extract from messages
                        messages = result.all_messages() if callable(result.all_messages) else result.all_messages
                        if messages:
                            # Get the last assistant message
                            for msg in reversed(messages):
                                if hasattr(msg, 'content'):
                                    response_text = str(msg.content)
                                    break
                    
                    if not response_text:
                        response_text = str(result) if result else "No response generated"
                    
                    # Stream the response in chunks for better UX
                    # Use larger chunks for faster transmission, no artificial delay
                    chunk_size = 100  # Larger chunks for faster streaming
                    for i in range(0, len(response_text), chunk_size):
                        chunk = response_text[i:i + chunk_size]
                        yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
                        # No artificial delay - stream as fast as possible
                    
                    # Send done event with timing info
                    yield f"data: {json.dumps({'type': 'done', 'elapsed_time': f'{elapsed_time:.2f}s'})}\n\n"
                        
            except asyncio.TimeoutError:
                logger.error("Claude agent timed out")
                yield f"data: {json.dumps({'type': 'error', 'error': 'Request timed out. The query may be too complex.'})}\n\n"
            except Exception as e:
                logger.error(f"Error in streaming chat: {e}")
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        except Exception as e:
            logger.error(f"Error in chat stream endpoint: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': f'Error processing chat message: {str(e)}'})}\n\n"
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    api_key: str = Depends(verify_api_key)
) -> ChatResponse:
    """Chat with the Claude agent. Supports conversation history for context."""
    try:
        try:
            agent = get_claude_agent(api_key)
        except HTTPException as http_err:
            # Re-raise HTTP exceptions (like invalid credentials)
            raise http_err
        except Exception as agent_err:
            # Catch other agent creation errors
            logger.error(f"Failed to get/create agent for API key {api_key[:8]}...: {agent_err}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize AI agent. Please check your TastyTrade credentials. Error: {str(agent_err)}"
            ) from agent_err
        
        # Note: Message history conversion is complex in pydantic-ai
        # For now, we'll skip it and users can include context in their messages
        # The agent maintains state per API key, so context is preserved within a session
        # If message_history is provided, we'll prepend it to the current message for context
        user_message = request.message
        if request.message_history:
            # Build context from history
            context_parts = []
            for msg in request.message_history:
                context_parts.append(f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}")
            context = "\n".join(context_parts)
            user_message = f"Previous conversation:\n{context}\n\nCurrent question: {request.message}"
        
        # Handle images if provided
        if request.images:
            image_count = len(request.images)
            logger.info(f"Processing chat request with {image_count} image(s)")
            user_message = f"{user_message}\n\nAttached {image_count} image(s)."
        
        # Run Claude agent with timeout
        # Note: We use async with to ensure proper cleanup, but the agent is cached per API key
        # so the MCP server connection is reused across requests
        import asyncio
        logger.info(f"Starting Claude agent run for message: {user_message[:100]}...")
        start_time = asyncio.get_event_loop().time()
        
        try:
            async with agent:
                result = await asyncio.wait_for(
                    agent.run(user_message),
                    timeout=90.0  # 90 second timeout for complex queries like option chains
                )
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"Claude agent completed in {elapsed:.2f}s")
        except asyncio.TimeoutError:
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.error(f"Claude agent timed out after {elapsed:.2f}s")
            raise HTTPException(
                status_code=504,
                detail="Request timed out. The query may be too complex. Try breaking it into smaller requests."
            )
        
        # Build response with updated history
        # Extract response text - try multiple attributes
        response_text = None
        if hasattr(result, 'output') and result.output is not None:
            response_text = str(result.output)
        elif hasattr(result, 'data') and result.data is not None:
            response_text = str(result.data)
        elif hasattr(result, 'all_messages'):
            # Try to extract from messages
            messages = result.all_messages() if callable(result.all_messages) else result.all_messages
            if messages:
                # Get the last assistant message
                for msg in reversed(messages):
                    if hasattr(msg, 'content'):
                        response_text = str(msg.content)
                        break
        
        if not response_text:
            response_text = str(result) if result else "No response generated"
            logger.warning(f"Could not extract response text from result: {type(result)}, attributes: {dir(result)}")
        
        # Get new messages from result
        new_messages = result.new_messages() if hasattr(result, 'new_messages') else []
        
        # Convert to our format
        updated_history = request.message_history.copy() if request.message_history else []
        updated_history.append(ChatMessage(role="user", content=request.message))
        updated_history.append(ChatMessage(role="assistant", content=response_text))
        
        return ChatResponse(
            response=response_text,
            message_history=updated_history
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing chat message: {e}"
        ) from e


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Health check endpoint (no authentication required)."""
    return {"status": "healthy", "service": "tasty-agent-http"}


# =============================================================================
# WRAP APP FOR LARGE BODY SIZE
# =============================================================================

# Wrap the FastAPI app to increase body size limit to 50MB
# This must be done after all routes are defined
app = LargeBodyASGIApp(app)


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
    
    # When running directly, pass the app object directly to avoid import issues
    # When run as a module, uvicorn can use the string path for reload support
    if __name__ == "__main__":
        uvicorn.run(
            app,  # Pass app object directly when running as script
            host=host,
            port=port,
            reload=False,  # Reload not supported when passing app object directly
            log_level="info"
        )
    else:
        uvicorn.run(
            "tasty_agent.http_server:app",
            host=host,
            port=port,
            reload=os.getenv("RELOAD", "false").lower() == "true",
            log_level="info"
        )


if __name__ == "__main__":
    main()

