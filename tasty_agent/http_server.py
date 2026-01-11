"""
HTTP Server for TastyTrade API - Allows multiple kiosks to connect via REST API.
- Account info endpoints: Direct HTTP (no Claude)
- Analytics endpoints: Uses Claude via MCP tools
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
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
from pydantic_ai import Agent, BinaryContent
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

# Import database module
from tasty_agent.database import CredentialsDB, get_db_path
from tasty_agent.supabase_client import SupabaseClient
from tasty_agent.logging_config import get_http_server_logger, LOGS_DIR
from tasty_agent.utils.session import create_session, is_sandbox_mode, select_account
from tasty_agent.utils.credentials import unpack_credentials
from tasty_agent.utils.errors import handle_tastytrade_auth_error

logger = get_http_server_logger()

rate_limiter = AsyncLimiter(2, 1)  # 2 requests per second

# API Key authentication
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Database instance (initialized in lifespan)
credentials_db: CredentialsDB | None = None
supabase_client: SupabaseClient | None = None

# Cache for kiosk API key credentials (api_key -> credentials dict)
# Type: api_key -> {device_id, user_id, account_id, client_secret, refresh_token, device_identifier}
kiosk_api_key_cache: dict[str, dict] = {}


# is_sandbox_mode is now imported from tasty_agent.utils.session


def load_credentials() -> dict[str, tuple[str, str]]:
    """Load credentials from SQLite database and environment variables.
    
    Returns:
        Dictionary mapping api_key to (client_secret, refresh_token) tuple
    """
    global credentials_db
    credentials: dict[str, tuple[str, str]] = {}
    
    # Load from SQLite database
    if credentials_db:
        try:
            db_creds = credentials_db.get_all_credentials()
            credentials.update(db_creds)
            logger.info(f"Loaded {len(db_creds)} credential(s) from database")
        except Exception as e:
            logger.error(f"Failed to load credentials from database: {e}")
    
    # Load from JSON environment variable (for initial setup)
    credentials_json = os.getenv("TASTYTRADE_CREDENTIALS_JSON")
    if credentials_json:
        try:
            creds_dict = json.loads(credentials_json)
            for api_key, cred_data in creds_dict.items():
                if isinstance(cred_data, dict):
                    client_secret = cred_data.get("client_secret")
                    refresh_token = cred_data.get("refresh_token")
                    if client_secret and refresh_token:
                        # Save to database if not already present
                        if api_key not in credentials and credentials_db:
                            try:
                                credentials_db.insert_or_update_credentials(api_key, client_secret, refresh_token)
                            except Exception as e:
                                logger.warning(f"Failed to save credential from env to database: {e}")
                        credentials[api_key] = (client_secret, refresh_token)
                    else:
                        logger.warning(f"Missing client_secret or refresh_token for API key: {api_key}")
                else:
                    logger.warning(f"Invalid credential format for API key: {api_key}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse TASTYTRADE_CREDENTIALS_JSON: {e}")
    
    # Load from legacy environment variables (backward compatibility)
    client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
    refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")
    # Handle empty string as "default" for API key
    api_key_env = os.getenv("API_KEY", "").strip()
    default_api_key = api_key_env if api_key_env else "default"
    
    if client_secret and refresh_token:
        if default_api_key not in credentials:
            # Save to database if not already present
            if credentials_db:
                try:
                    credentials_db.insert_or_update_credentials(default_api_key, client_secret, refresh_token)
                except Exception as e:
                    logger.warning(f"Failed to save default credential to database: {e}")
            credentials[default_api_key] = (client_secret, refresh_token)
            logger.info(f"Loaded default credentials for API key: {default_api_key}")
        else:
            logger.warning(f"API key {default_api_key} already exists in credentials, skipping default env vars")
    
    if not credentials:
        logger.warning("No credentials loaded. Server may not work correctly.")
    
    return credentials


def save_credentials(credentials: dict[str, tuple[str, str]]) -> None:
    """Save credentials to SQLite database."""
    global credentials_db
    
    if not credentials_db:
        raise HTTPException(
            status_code=500,
            detail="Database not initialized"
        )
    
    try:
        # Save each credential to database
        for api_key, (client_secret, refresh_token) in credentials.items():
            credentials_db.insert_or_update_credentials(api_key, client_secret, refresh_token)
        logger.info(f"Saved {len(credentials)} credential(s) to database")
    except Exception as e:
        logger.error(f"Failed to save credentials to database: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save credentials: {e}"
        ) from e


def load_settings() -> dict[str, UserSettings]:
    """Load user settings from JSON file."""
    project_root = Path(__file__).parent.parent
    settings_file = project_root / "settings.json"
    settings: dict[str, UserSettings] = {}
    
    if settings_file.exists():
        try:
            with open(settings_file, "r") as f:
                settings_dict = json.load(f)
            for api_key, settings_data in settings_dict.items():
                try:
                    settings[api_key] = UserSettings(**settings_data)
                except Exception as e:
                    logger.warning(f"Failed to load settings for API key {api_key[:8]}...: {e}")
        except Exception as e:
            logger.error(f"Failed to load settings.json: {e}")
    
    return settings


def save_settings(settings: dict[str, UserSettings]) -> None:
    """Save user settings to JSON file."""
    project_root = Path(__file__).parent.parent
    settings_file = project_root / "settings.json"
    
    # Convert UserSettings to dict format for JSON
    settings_dict = {}
    for api_key, user_settings in settings.items():
        settings_dict[api_key] = user_settings.model_dump()
    
    try:
        with open(settings_file, "w") as f:
            json.dump(settings_dict, f, indent=2)
        logger.info(f"Saved settings to {settings_file}")
    except Exception as e:
        logger.error(f"Failed to save settings.json: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save settings: {e}"
        ) from e


def update_env_file(key: str, value: str) -> None:
    """
    Update an environment variable in the .env file while preserving formatting and comments.
    
    Args:
        key: Environment variable name (e.g., "MODE")
        value: New value to set
    
    Raises:
        HTTPException: If .env file cannot be read or written
    """
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"
    
    if not env_file.exists():
        logger.warning(f".env file not found at {env_file}. Creating new file.")
        try:
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")
            logger.info(f"Created .env file with {key}={value}")
            return
        except Exception as e:
            logger.error(f"Failed to create .env file: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create .env file: {e}"
            ) from e
    
    try:
        # Read all lines from .env file
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # Find the line with the key (may have comment after =)
        key_found = False
        updated_lines = []
        
        for line in lines:
            stripped = line.strip()
            # Check if line starts with key= (with optional whitespace before =)
            # Handle cases like "MODE=  #comment" or "MODE = value #comment"
            if stripped.startswith(f"{key}=") or (stripped.startswith(key) and "=" in stripped and stripped.split("=")[0].strip() == key):
                # Found the key, update it
                # Preserve comment if present
                if "#" in line:
                    # Extract comment (everything after first #)
                    comment_part = line[line.index("#"):]
                    # Preserve original indentation if any
                    leading_spaces = len(line) - len(line.lstrip())
                    indent = " " * leading_spaces
                    updated_lines.append(f"{indent}{key}={value}  {comment_part}")
                else:
                    # Preserve original indentation if any
                    leading_spaces = len(line) - len(line.lstrip())
                    indent = " " * leading_spaces
                    # Preserve newline if original had it
                    newline = "\n" if line.endswith("\n") else ""
                    updated_lines.append(f"{indent}{key}={value}{newline}")
                key_found = True
            else:
                # Keep original line
                updated_lines.append(line)
        
        # If key not found, add it at the end
        if not key_found:
            updated_lines.append(f"{key}={value}\n")
            logger.info(f"Added new {key}={value} to .env file")
        else:
            logger.info(f"Updated {key}={value} in .env file")
        
        # Write back to file
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(updated_lines)
        
    except Exception as e:
        logger.error(f"Failed to update .env file: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update .env file: {e}"
        ) from e


def get_user_settings(api_key: str) -> UserSettings:
    """Get user settings for an API key, returning defaults if not found."""
    if api_key in api_key_settings:
        return api_key_settings[api_key]
    # Return default settings
    return UserSettings(
        auto_stop_loss_enabled=True,
        max_loss_per_contract=50.0,
        default_order_type='Limit',
        default_time_in_force='Day'
    )


def extract_symbol_from_tab_id(tab_id: str) -> str | None:
    """Extract symbol from tabId format (SYMBOL-timestamp).
    
    Args:
        tab_id: Tab ID in format "SYMBOL-timestamp" (e.g., "SPY-1735689600000")
    
    Returns:
        Symbol string (e.g., "SPY") or None if format is invalid
    """
    if not tab_id:
        return None
    # Extract symbol from tabId format: "SYMBOL-timestamp"
    match = tab_id.split("-")[0] if "-" in tab_id else None
    if match and match.isalpha() and match.isupper():
        return match
    return None


def get_conversation_history(api_key: str, tab_id: str | None) -> list[ChatMessage]:
    """Get conversation history for a specific (api_key, tab_id) pair.
    
    Args:
        api_key: API key identifier
        tab_id: Tab ID (optional)
    
    Returns:
        List of ChatMessage objects, empty list if no history exists
    """
    if not tab_id:
        return []
    
    key = (api_key, tab_id)
    return api_key_tab_conversations.get(key, []).copy()


def save_conversation_message(api_key: str, tab_id: str | None, message: ChatMessage) -> None:
    """Save a message to conversation history for a specific (api_key, tab_id) pair.
    
    Args:
        api_key: API key identifier
        tab_id: Tab ID (optional)
        message: ChatMessage to save
    """
    if not tab_id:
        return
    
    key = (api_key, tab_id)
    if key not in api_key_tab_conversations:
        api_key_tab_conversations[key] = []
    api_key_tab_conversations[key].append(message)
    
    # Limit conversation history to prevent unbounded growth
    # Keep last 100 messages (approximately 50 exchanges)
    max_messages = 100
    if len(api_key_tab_conversations[key]) > max_messages:
        api_key_tab_conversations[key] = api_key_tab_conversations[key][-max_messages:]


def get_client_ip(request: Request) -> str:
    """Extract client IP address from request.
    
    When behind a reverse proxy, checks headers first (X-Tailscale-IP, X-Forwarded-For, X-Real-IP).
    Falls back to request.client.host for direct connections.
    """
    # First, check for Tailscale-specific header (most reliable when behind proxy)
    tailscale_ip_header = request.headers.get("X-Tailscale-IP")
    if tailscale_ip_header:
        ip = tailscale_ip_header.strip()
        logger.debug(f"Extracted IP from X-Tailscale-IP header: {ip}")
        return ip
    
    # Check X-Forwarded-For for Tailscale IPs (100.x.x.x) first
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ips = [ip.strip() for ip in forwarded_for.split(",")]
        # Prefer Tailscale IPs (100.x.x.x range) if present - these are trusted from Tailscale
        for ip in ips:
            if ip.startswith("100."):
                logger.debug(f"Extracted Tailscale IP from X-Forwarded-For: {ip}")
                return ip
    
    # Check X-Real-IP
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        ip = real_ip.strip()
        # If it's a Tailscale IP, trust it
        if ip.startswith("100."):
            logger.debug(f"Extracted Tailscale IP from X-Real-IP: {ip}")
            return ip
    
    # If we have request.client, check if it's an internal IP
    # If so, we're likely behind a proxy, so trust headers if they exist
    if request.client:
        client_host = request.client.host
        is_internal_ip = (
            client_host.startswith("192.168.") or
            client_host.startswith("10.") or
            client_host.startswith("172.") or
            client_host.startswith("127.") or
            client_host == "localhost"
        )
        
        # If we're behind a proxy (internal IP) and have headers, prefer headers
        if is_internal_ip:
            if forwarded_for:
                # Parse X-Forwarded-For and take first IP (original client)
                forwarded_ips = [ip.strip() for ip in forwarded_for.split(",")]
                ip = forwarded_ips[0]
                logger.debug(f"Behind proxy: Using IP from X-Forwarded-For header: {ip} (client.host: {client_host})")
                return ip
            if real_ip:
                logger.debug(f"Behind proxy: Using IP from X-Real-IP header: {real_ip} (client.host: {client_host})")
                return real_ip.strip()
        
        # Direct connection - use client host
        logger.debug(f"Direct connection: Using request.client.host: {client_host}")
        return client_host
    
    # Fallback: Use first IP from X-Forwarded-For if available
    if forwarded_for:
        ips = [ip.strip() for ip in forwarded_for.split(",")]
        ip = ips[0]
        logger.debug(f"Fallback: Extracted IP from X-Forwarded-For: {ip}")
        return ip
    
    logger.warning("Could not extract client IP from request")
    return "unknown"


async def get_credentials_or_api_key(
    request: Request,
    api_key: str = Depends(API_KEY_HEADER)
) -> dict[str, str]:
    """
    Get credentials from headers or API key.
    For internal Docker network: accepts credentials directly without API key.
    For external network: uses API key authentication (legacy).
    
    Returns dict with: {type: 'credentials'|'api_key', client_secret, refresh_token, account_id, api_key}
    """
    # Check if credentials are provided directly in headers (internal network)
    client_secret = request.headers.get("X-TastyTrade-Client-Secret")
    refresh_token = request.headers.get("X-TastyTrade-Refresh-Token")
    account_id = request.headers.get("X-TastyTrade-Account-ID")
    
    if client_secret and refresh_token:
        # Credentials provided directly - use them (internal network)
        return {
            "type": "credentials",
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "account_id": account_id,
            "api_key": account_id or "credentials"
        }
    
    # Fall back to API key authentication (legacy/external network)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials. Provide X-TastyTrade-Client-Secret, X-TastyTrade-Refresh-Token headers, or X-API-Key header."
        )
    
    return {
        "type": "api_key",
        "api_key": api_key
    }


async def verify_api_key(
    request: Request,
    api_key: str = Depends(API_KEY_HEADER)
) -> str:
    """
    Verify API key from header OR credentials from headers.
    For internal network: accepts credentials directly (X-TastyTrade-Client-Secret, X-TastyTrade-Refresh-Token).
    For external network: uses API key authentication (legacy).
    
    Returns an identifier string (account_id for credentials, api_key for API key auth).
    """
    global supabase_client, kiosk_api_key_cache
    
    # Check if credentials are provided directly (preferred for internal network - no API key needed)
    client_secret = request.headers.get("X-TastyTrade-Client-Secret")
    refresh_token = request.headers.get("X-TastyTrade-Refresh-Token")
    
    if client_secret and refresh_token:
        # Credentials provided directly - return account_id as identifier
        account_id = request.headers.get("X-TastyTrade-Account-ID") or "credentials"
        logger.info(f"Using credentials-based authentication (account_id: {account_id})")
        return account_id
    
    # Fall back to API key authentication (legacy/external network)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials. Provide X-TastyTrade-Client-Secret, X-TastyTrade-Refresh-Token headers, or X-API-Key header."
        )
    
    # Check if paper trading mode is enabled (skip Supabase validation)
    paper_mode = is_sandbox_mode()
    
    # Get client IP for validation
    client_ip = get_client_ip(request)
    
    # Log all IP-related headers for debugging
    forwarded_for = request.headers.get("X-Forwarded-For", "not set")
    real_ip = request.headers.get("X-Real-IP", "not set")
    logger.info(
        f"🔍 Validating API key {api_key[:8]}... | "
        f"Paper Mode: {paper_mode} | "
        f"Client IP: {client_ip} | "
        f"X-Forwarded-For: {forwarded_for} | "
        f"X-Real-IP: {real_ip} | "
        f"request.client.host: {request.client.host if request.client else 'None'}"
    )
    
    # In paper mode, skip Supabase validation and use local credentials directly
    if paper_mode:
        logger.info(f"📝 Paper trading mode enabled - skipping Supabase validation for API key {api_key[:8]}...")
        # Fall through to local credentials check
    
    # First, try Supabase (for kiosk devices) - only if not in paper mode
    elif supabase_client:
        try:
            # Always validate against Supabase first (to catch revocations)
            # This ensures revoked keys are immediately rejected even if cached
            kiosk_info = await supabase_client.validate_kiosk_api_key(api_key, client_ip=client_ip)
            if kiosk_info:
                # Update cache with fresh validation result (only if IP validation passed)
                kiosk_api_key_cache[api_key] = kiosk_info
                logger.info(
                    f"✅ Validated kiosk API key for device {kiosk_info.get('device_identifier')} "
                    f"with IP: {client_ip}"
                )
                return api_key
            else:
                # Key is invalid, revoked, or IP mismatch - clear from cache if present
                if api_key in kiosk_api_key_cache:
                    logger.info(f"API key {api_key[:8]}... is invalid, revoked, or IP mismatch, removing from cache")
                    kiosk_api_key_cache.pop(api_key, None)
                    # Also clear any associated sessions
                    api_key_sessions.pop(api_key, None)
                # Check if it's likely an IP mismatch (client IP was provided but validation failed)
                if client_ip and client_ip != "unknown":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"IP address mismatch. Client IP: {client_ip} does not match the configured device IP. Please contact administrator."
                    )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or revoked API key"
                )
        except HTTPException:
            # Re-raise HTTP exceptions (like invalid key)
            raise
        except Exception as e:
            logger.warning(f"Failed to validate API key against Supabase: {e}")
            # If database check fails, check cache as fallback (for resilience)
            # But only if IP validation is not required (device has no tailscale_ip)
            if api_key in kiosk_api_key_cache:
                cached_info = kiosk_api_key_cache[api_key]
                # Only use cache if we can't validate IP (device might not have tailscale_ip)
                logger.warning(f"Database validation failed but found cached credentials for {api_key[:8]}..., using cache (IP validation skipped)")
                return api_key
            # Fall through to local credentials check
    
    # Fall back to local credentials (for backward compatibility)
    if api_key in api_key_credentials:
        return api_key
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Invalid API key. No credentials configured for this key."
    )


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

# Claude agent cache: (api_key, account_id) -> Agent
# account_id can be None for default account
api_key_agents: dict[tuple[str, str | None], Agent] = {}

# User settings cache: API key -> UserSettings
api_key_settings: dict[str, UserSettings] = {}

# Conversation history cache: (api_key, tab_id) -> list[ChatMessage]
# This allows separate conversation contexts per tab
api_key_tab_conversations: dict[tuple[str, str], list[ChatMessage]] = {}

# Track current model identifier to invalidate cache when it changes
# Initialize with current default to detect changes after restart
_current_model_identifier: str | None = os.getenv("MODEL_IDENTIFIER", "anthropic:claude-3-5-haiku-20241022")


def get_api_key_context(api_key: str, client_secret: str | None = None, refresh_token: str | None = None) -> ServerContext:
    """
    Get or create ServerContext for an API key or credentials with lazy initialization.
    Supports both kiosk API keys (from Supabase), local API keys, and direct credentials.
    
    Args:
        api_key: API key identifier (or "credentials" for credential-based auth)
        client_secret: Optional client secret for credential-based auth
        refresh_token: Optional refresh token for credential-based auth
    """
    global api_key_sessions, kiosk_api_key_cache
    
    # If credentials provided directly, use them
    if client_secret and refresh_token:
        # Create a cache key for credentials-based sessions
        cred_key = f"creds_{client_secret[:8]}"
        if cred_key in api_key_sessions:
            context = api_key_sessions[cred_key]
            session = context.session
            
            # Refresh if expired
            time_until_expiry = (session.session_expiration - now_in_new_york()).total_seconds()
            if time_until_expiry < 5:
                logger.info(f"Session expiring in {time_until_expiry:.0f}s for credentials, refreshing...")
                try:
                    session.refresh()
                    logger.info(f"Session refreshed, new expiration: {session.session_expiration}")
                except Exception as refresh_err:
                    error_msg = str(refresh_err)
                    if "invalid_grant" in error_msg or "Grant revoked" in error_msg:
                        api_key_sessions.pop(cred_key, None)
                        raise HTTPException(
                            status_code=401,
                            detail=f"TastyTrade refresh token is invalid or revoked. Error: {error_msg}"
                        ) from refresh_err
                    else:
                        raise
            return context
        
        # Create new session from credentials
        try:
            session = create_session(client_secret, refresh_token)
            accounts = Account.get(session)
            logger.info(f"Successfully authenticated with credentials (sandbox={is_sandbox_mode()}). Found {len(accounts)} account(s).")
            
            context = ServerContext(session=session, accounts=accounts)
            api_key_sessions[cred_key] = context
            return context
        except Exception as e:
            logger.error(f"Failed to authenticate with credentials: {e}")
            raise handle_tastytrade_auth_error(
                e,
                "credentials",
                "Invalid credentials. Please check TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN."
            )
    
    # Check if context already exists (API key-based)
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
    
    # Try to get credentials from kiosk cache (Supabase)
    if api_key in kiosk_api_key_cache:
        kiosk_info = kiosk_api_key_cache[api_key]
        client_secret = kiosk_info['client_secret']
        refresh_token = kiosk_info['refresh_token']
        account_id = kiosk_info.get('account_id')
        
        try:
            session = create_session(client_secret, refresh_token)
            accounts = Account.get(session)
            logger.info(f"Successfully authenticated kiosk API key {api_key[:8]}... with Tastytrade (sandbox={is_sandbox_mode()}). Found {len(accounts)} account(s).")
            
            # Filter to the specific account_id if provided
            if account_id:
                try:
                    account = select_account(accounts, account_id)
                    accounts = [account]
                    logger.info(f"Using account {account_id} for kiosk device {kiosk_info.get('device_identifier')}")
                except ValueError:
                    logger.warning(f"Account {account_id} not found, using first available account")
            
            context = ServerContext(session=session, accounts=accounts)
            api_key_sessions[api_key] = context
            return context
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to authenticate kiosk API key {api_key[:8]}... with Tastytrade: {e}")
            raise handle_tastytrade_auth_error(
                e,
                api_key,
                "Please contact administrator to update broker connection credentials."
            )
    
    # Fall back to local credentials (for backward compatibility)
    if api_key not in api_key_credentials:
        raise HTTPException(
            status_code=500,
            detail=f"No credentials configured for API key {api_key[:8]}..."
        )
    
    # Local credentials are stored as tuples: (client_secret, refresh_token)
    creds = api_key_credentials[api_key]
    try:
        client_secret, refresh_token = unpack_credentials(creds)
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid credential format for API key {api_key[:8]}...: {type(creds)} - {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Invalid credential format for API key {api_key[:8]}..."
        ) from e
    
    try:
        session = create_session(client_secret, refresh_token)
        accounts = Account.get(session)
        logger.info(f"Successfully authenticated API key {api_key[:8]}... with Tastytrade (sandbox={is_sandbox_mode()}). Found {len(accounts)} account(s).")
        
        context = ServerContext(session=session, accounts=accounts)
        api_key_sessions[api_key] = context
        return context
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to authenticate API key {api_key[:8]}... with Tastytrade: {e}")
        raise handle_tastytrade_auth_error(
            e,
            api_key,
            "Please update your credentials using POST /api/v1/credentials. "
            "To get a new refresh token, visit https://my.tastytrade.com/app.html#/manage/api-access/oauth-applications "
            "and create a new Personal OAuth Grant."
        )


def get_account_context(api_key: str, account_id: str | None) -> tuple[Session, Account]:
    """
    Get session and account for a given API key and account ID.
    For kiosk API keys, account_id is auto-determined from broker connection.
    """
    global kiosk_api_key_cache
    
    context = get_api_key_context(api_key)
    
    # For kiosk API keys, account_id is already determined from broker connection
    # If account_id is not provided, use the first account (or the one from kiosk info)
    if not account_id:
        # Check if this is a kiosk API key with a specific account
        if api_key in kiosk_api_key_cache:
            kiosk_info = kiosk_api_key_cache[api_key]
            account_id = kiosk_info.get('account_id')
            if account_id:
                # Find the matching account
                account = next((acc for acc in context.accounts if acc.account_number == account_id), None)
                if account:
                    logger.debug(f"Using account {account_id} from kiosk broker connection")
                    return context.session, account
        
        # Use first available account
        if context.accounts:
            account = context.accounts[0]
            logger.debug(f"No account_id provided, using default account: {account.account_number}")
            return context.session, account
        else:
            raise HTTPException(
                status_code=404,
                detail="No accounts found for this API key"
            )
    
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
_core_rules_cache: str | None = None
_mode_instructions_cache: str | None = None
_current_mode: str | None = None


def load_core_rules() -> str:
    """Load the core rules file. Cached after first load. Always loaded."""
    global _core_rules_cache
    
    if _core_rules_cache is not None:
        return _core_rules_cache
    
    # Get the path to core_rules.md
    project_root = Path(__file__).parent
    core_file = project_root / "core_rules.md"
    
    if not core_file.exists():
        logger.warning(f"Core rules file not found at {core_file}. Agent will run without core rules.")
        return ""
    
    try:
        with open(core_file, "r", encoding="utf-8") as f:
            _core_rules_cache = f.read()
        logger.info(f"Loaded core rules from {core_file} ({len(_core_rules_cache):,} chars)")
        return _core_rules_cache
    except Exception as e:
        logger.error(f"Failed to load core rules file {core_file}: {e}")
        return ""


def load_mode_instructions() -> str:
    """
    Load mode-specific trading instructions based on MODE environment variable.
    Cached after first load, invalidated when MODE changes.
    
    Returns:
        Mode-specific instructions string, or empty string for 'custom' mode
    """
    global _mode_instructions_cache, _current_mode
    
    # Get current MODE from environment
    current_mode = os.getenv("MODE", "custom").strip().lower()
    
    # If mode changed, clear cache
    if _current_mode is not None and _current_mode != current_mode:
        logger.info(f"Mode changed from {_current_mode} to {current_mode}. Clearing mode instructions cache.")
        _mode_instructions_cache = None
    
    # If custom mode, return empty (no mode-specific instructions)
    if current_mode == "custom":
        _current_mode = current_mode
        return ""
    
    # Check cache
    if _mode_instructions_cache is not None and _current_mode == current_mode:
        return _mode_instructions_cache
    
    # Map mode to filename
    mode_file_map = {
        "day": "day_trader.md",
        "scalp": "scalpe_trader.md",
        "swing": "swing_trader.md",
    }
    
    filename = mode_file_map.get(current_mode)
    if not filename:
        logger.warning(f"Unknown MODE value: {current_mode}. Valid modes: custom, day, scalp, swing. Using custom mode.")
        _current_mode = "custom"
        return ""
    
    # Get the path to mode file
    project_root = Path(__file__).parent
    mode_file = project_root / filename
    
    if not mode_file.exists():
        logger.warning(f"Mode file not found at {mode_file}. Agent will run without mode-specific instructions.")
        _current_mode = current_mode
        _mode_instructions_cache = ""
        return ""
    
    try:
        with open(mode_file, "r", encoding="utf-8") as f:
            _mode_instructions_cache = f.read()
        _current_mode = current_mode
        logger.info(f"Loaded {current_mode} mode instructions from {mode_file} ({len(_mode_instructions_cache):,} chars)")
        return _mode_instructions_cache
    except Exception as e:
        logger.error(f"Failed to load mode file {mode_file}: {e}")
        _current_mode = current_mode
        _mode_instructions_cache = ""
        return ""


def load_instruction_prompt(include_full: bool = True) -> str:
    """
    Load the instruction prompt files. Cached after first load.
    
    Args:
        include_full: If True, loads core_rules.md + mode_instructions + instruction_live_prompt.md.
                     If False, loads only core_rules.md (faster, less context).
    
    Returns:
        Combined prompt string with core rules always included, mode instructions if applicable, and live prompt.
    """
    global _instruction_prompt_cache, _current_mode
    
    # Always load core rules first
    core_rules = load_core_rules()
    
    # If only core rules requested, return early
    if not include_full:
        return core_rules
    
    # Check if mode changed (invalidate cache if so)
    current_mode = os.getenv("MODE", "custom").strip().lower()
    if _current_mode is not None and _current_mode != current_mode:
        logger.info(f"Mode changed, clearing instruction prompt cache")
        _instruction_prompt_cache = None
    
    # Check cache for full prompt
    if _instruction_prompt_cache is not None:
        return _instruction_prompt_cache
    
    # Load mode-specific instructions
    mode_instructions = load_mode_instructions()
    
    # Get the path to instruction_live_prompt.md
    project_root = Path(__file__).parent
    instruction_file = project_root / "instruction_live_prompt.md"
    
    if not instruction_file.exists():
        logger.warning(f"Instruction file not found at {instruction_file}. Using core rules and mode instructions only.")
        # Combine core rules and mode instructions
        if mode_instructions:
            combined = f"{core_rules}\n\n---\n\n# MODE-SPECIFIC INSTRUCTIONS ({current_mode.upper()} MODE)\n\n{mode_instructions}"
        else:
            combined = core_rules
        _instruction_prompt_cache = combined
        return _instruction_prompt_cache
    
    try:
        with open(instruction_file, "r", encoding="utf-8") as f:
            full_instructions = f.read()
        
        # Combine: core rules first, then mode instructions (if any) PROMINENTLY, then full instructions
        parts = [core_rules]
        
        if mode_instructions:
            # Make mode instructions very prominent - this is your active trading mode
            mode_header = f"""
{'='*80}
# ⚡ ACTIVE TRADING MODE: {current_mode.upper()} MODE ⚡
{'='*80}

**⚠️ CRITICAL: You are currently operating in {current_mode.upper()} MODE.**

**When users ask about your trading rules, strategy, approach, or "what are your scalping/day/swing rules?", you MUST:**
1. **Immediately reference your {current_mode.upper()} mode instructions below**
2. **Explain your {current_mode.upper()} mode characteristics, entry criteria, position management, and risk rules**
3. **Do NOT give generic trading advice - be specific to {current_mode.upper()} mode**

**Available Tools:**
- **TastyTrade MCP** - Order execution, account management, market data (quotes, greeks, metrics)
- **ThetaData** - Real-time market data streaming (data only, no trading)
- **Browser/TradingView** - Visual chart analysis

**⚠️ IMPORTANT: You use TastyTrade MCP for all trading operations. There is NO Alpaca integration.**
"""
            parts.append(mode_header)
            parts.append(f"\n{mode_instructions}\n")
            parts.append(f"\n{'='*80}\n")
        
        parts.append(f"\n\n---\n\n# FULL INSTRUCTIONS (On-Demand Content)\n\n{full_instructions}")
        
        combined = "".join(parts)
        
        _instruction_prompt_cache = combined
        mode_info = f" + mode ({len(mode_instructions):,} chars)" if mode_instructions else ""
        logger.info(f"Loaded full instruction prompt: core ({len(core_rules):,} chars){mode_info} + full ({len(full_instructions):,} chars) = {len(combined):,} total chars")
        return _instruction_prompt_cache
    except Exception as e:
        logger.error(f"Failed to load instruction file {instruction_file}: {e}")
        # Fall back to core rules and mode instructions
        if mode_instructions:
            combined = f"{core_rules}\n\n---\n\n# MODE-SPECIFIC INSTRUCTIONS ({current_mode.upper()} MODE)\n\n{mode_instructions}"
        else:
            combined = core_rules
        _instruction_prompt_cache = combined
        return _instruction_prompt_cache


def get_claude_agent(api_key: str, account_id: str | None = None) -> Agent:
    """Get or create Claude agent for an API key and account ID. Uses Claude API from environment variables.
    
    Args:
        api_key: API key identifier
        account_id: Optional TastyTrade account ID. If None, uses default account.
    
    Returns:
        Agent instance configured for the specified account
    """
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
    
    # Use (api_key, account_id) as cache key
    cache_key = (api_key, account_id)
    if cache_key in api_key_agents:
        return api_key_agents[cache_key]
    
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
        # Handle different credential formats
        creds = api_key_credentials[api_key]
        try:
            client_secret, refresh_token = unpack_credentials(creds)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid credential format for API key {api_key[:8]}...: {type(creds)} - {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Invalid credential format for API key {api_key[:8]}..."
            ) from e
        
        # Validate credentials before creating MCP server
        try:
            test_session = create_session(client_secret, refresh_token)
            test_accounts = Account.get(test_session)
            logger.info(f"Credentials validated for API key {api_key[:8]}... (sandbox={is_sandbox_mode()}) Found {len(test_accounts)} account(s)")
        except HTTPException:
            raise
        except Exception as auth_error:
            logger.error(f"Failed to validate TastyTrade credentials for API key {api_key[:8]}...: {auth_error}")
            raise handle_tastytrade_auth_error(
                auth_error,
                api_key,
                "Please update your credentials using POST /api/v1/credentials."
            )
        
        env = dict(os.environ)
        env["TASTYTRADE_CLIENT_SECRET"] = str(client_secret)
        env["TASTYTRADE_REFRESH_TOKEN"] = str(refresh_token)
        env["ANTHROPIC_API_KEY"] = str(claude_api_key)
        
        # Set account_id in environment if provided
        if account_id:
            env["TASTYTRADE_ACCOUNT_ID"] = account_id
            logger.info(f"Using account_id {account_id} for MCP server")
        else:
            # Remove TASTYTRADE_ACCOUNT_ID from env to use default account
            env.pop("TASTYTRADE_ACCOUNT_ID", None)
            logger.info(f"Using default account for MCP server")
        
        server = MCPServerStdio(
            'python', args=['run_mcp_stdio.py'], timeout=120, env=env
        )
        
        # Create agent with system prompt if available
        account_info = f"account {account_id}" if account_id else "default account"
        if system_prompt:
            # pydantic-ai Agent accepts system_prompt parameter
            agent = Agent(model_identifier, toolsets=[server], system_prompt=system_prompt)
            logger.info(f"Created Claude agent for API key {api_key[:8]}... with {account_info}, model {model_identifier} and custom instructions ({len(system_prompt):,} chars)")
        else:
            agent = Agent(model_identifier, toolsets=[server])
            logger.info(f"Created Claude agent for API key {api_key[:8]}... with {account_info}, model {model_identifier} without custom instructions")
        
        api_key_agents[cache_key] = agent
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


def create_mcp_context(session: Session, account: Account) -> Context[Any, Any, Any]:  # type: ignore[type-arg]
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
    
    return MockContext(session, account)  # type: ignore[return-value]


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
                                # Also clear agent cache (delete all agents for this api_key)
                                global api_key_agents
                                keys_to_delete = [key for key in api_key_agents.keys() if key[0] == api_key]
                                for key in keys_to_delete:
                                    del api_key_agents[key]
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
    global api_key_credentials, api_key_sessions, api_key_agents, api_key_settings, api_key_tab_conversations, _current_model_identifier, credentials_db, supabase_client
    
    # Initialize logging
    from tasty_agent.logging_config import initialize_tasty_agent_loggers
    initialize_tasty_agent_loggers()
    logger.info("TastyAgent loggers initialized")
    
    # Initialize database
    project_root = Path(__file__).parent.parent
    db_path = get_db_path(project_root)
    credentials_db = CredentialsDB(db_path)
    
    # Initialize Supabase client for kiosk API key validation
    try:
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            supabase_client = SupabaseClient(database_url)
            await supabase_client.connect()
            logger.info("Supabase client initialized for kiosk API key validation")
        else:
            logger.warning("DATABASE_URL not set, kiosk API key validation via Supabase will be disabled")
            supabase_client = None
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        supabase_client = None
    
    # Load credentials at startup (from database and environment variables)
    api_key_credentials = load_credentials()
    
    if not api_key_credentials:
        logger.warning(
            "No credentials loaded. Set TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN "
            "environment variables, or configure TASTYTRADE_CREDENTIALS_JSON, or add via POST /api/v1/credentials"
        )
    
    logger.info(f"Loaded credentials for {len(api_key_credentials)} API key(s)")
    
    # Load user settings at startup
    api_key_settings.update(load_settings())
    logger.info(f"Loaded settings for {len(api_key_settings)} API key(s)")
    
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
    if supabase_client:
        await supabase_client.close()
    api_key_sessions.clear()
    api_key_credentials.clear()
    api_key_agents.clear()
    api_key_settings.clear()
    api_key_tab_conversations.clear()


async def get_session_and_account(
    request: Request,
    api_key: str = Depends(verify_api_key),
    account_id: str | None = Query(None, description="TastyTrade account ID (optional, uses default if not provided)")
) -> tuple[Session, Account]:
    """
    Dependency to get session and account.
    Supports credentials-based auth (internal network) or API key auth (legacy).
    """
    # Check if credentials are provided directly in headers (internal network - no API key needed)
    client_secret = request.headers.get("X-TastyTrade-Client-Secret")
    refresh_token = request.headers.get("X-TastyTrade-Refresh-Token")
    header_account_id = request.headers.get("X-TastyTrade-Account-ID")
    
    if client_secret and refresh_token:
        # Use credentials directly (internal Docker network)
        context = get_api_key_context(api_key, client_secret, refresh_token)
        
        # Use account_id from header, query param, or first available
        target_account_id = header_account_id or account_id
        if target_account_id:
            try:
                account = select_account(context.accounts, target_account_id)
                logger.info(f"Using account {target_account_id} from credentials")
                return context.session, account
            except ValueError:
                logger.warning(f"Account {target_account_id} not found, using first available")
        
        if context.accounts:
            account = context.accounts[0]
            logger.debug(f"No account_id provided, using default account: {account.account_number}")
            return context.session, account
        else:
            raise HTTPException(
                status_code=404,
                detail="No accounts found for provided credentials"
            )
    
    # Fall back to API key authentication (legacy/external network)
    if not account_id:
        context = get_api_key_context(api_key)
        if context.accounts:
            account = context.accounts[0]
            logger.debug(f"No account_id provided, using default account: {account.account_number}")
            return context.session, account
        else:
            raise HTTPException(
                status_code=404,
                detail="No accounts found for this API key"
            )
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
# Explicitly list headers to ensure preflight requests work correctly
# Some browsers don't accept "*" for allow_headers in preflight responses
# Note: When allow_credentials=True, browsers don't allow allow_origins=["*"]
# So we need to either disable credentials or specify explicit origins
cors_origins = os.getenv("CORS_ORIGINS", "*")
if cors_origins == "*":
    cors_origins_list = ["*"]
    # When using wildcard origins, disable credentials to avoid browser restrictions
    allow_creds = False
else:
    cors_origins_list = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
    # When using explicit origins, we can enable credentials
    allow_creds = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins_list,
    allow_credentials=allow_creds,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-API-Key",
        "x-api-key",  # Lowercase variant
        "api-key",
        "X-Requested-With",
        "Accept",
        "Origin",
    ],
    expose_headers=["*"],
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


class UserSettings(BaseModel):
    """User-specific trading settings."""
    auto_stop_loss_enabled: bool = Field(True, description="Enable automatic stop-loss orders")
    max_loss_per_contract: float = Field(50.0, description="Maximum loss per contract in dollars", ge=1.0, le=1000.0)
    default_order_type: Literal['Market', 'Limit', 'Stop', 'StopLimit', 'TrailingStop'] = Field('Limit', description="Default order type")
    default_time_in_force: Literal['Day', 'GTC', 'IOC'] = Field('Day', description="Default time in force")


class UserSettingsUpdate(BaseModel):
    """Partial update for user settings."""
    auto_stop_loss_enabled: bool | None = None
    max_loss_per_contract: float | None = Field(None, ge=1.0, le=1000.0)
    default_order_type: Literal['Market', 'Limit', 'Stop', 'StopLimit', 'TrailingStop'] | None = None
    default_time_in_force: Literal['Day', 'GTC', 'IOC'] | None = None


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


async def monitor_order_fill(session: Session, account: Account, order_id: int, timeout: int = 60) -> Any:
    """
    Poll order status using direct API calls until order fills or times out.
    
    Args:
        session: TastyTrade session
        account: Account object
        order_id: Order ID to monitor
        timeout: Maximum time to wait in seconds (default: 60)
    
    Returns:
        Order object when filled
    
    Raises:
        ValueError: If order is rejected or canceled
        TimeoutError: If order doesn't fill within timeout
    """
    import time
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Direct API call - no AI
        live_orders = await account.a_get_live_orders(session)
        order = next((o for o in live_orders if o.id == order_id), None)
        
        if order:
            # Check order status
            order_status = str(order.status).lower() if hasattr(order.status, 'value') else str(order.status).lower()
            
            if 'filled' in order_status or order_status == 'filled':
                logger.info(f"Order {order_id} filled successfully")
                return order
            elif 'rejected' in order_status or order_status == 'rejected':
                raise ValueError(f"Order {order_id} was rejected")
            elif 'canceled' in order_status or order_status == 'canceled':
                raise ValueError(f"Order {order_id} was canceled")
        
        await asyncio.sleep(1.5)  # Poll every 1.5 seconds
    
    raise TimeoutError(f"Order {order_id} did not fill within {timeout} seconds")


def calculate_stop_loss_price(
    entry_price: float,
    quantity: int,
    action: str,
    is_option: bool = False,
    max_loss_per_contract: float = 50.0
) -> float:
    """
    Calculate stop-loss price based on entry price, quantity, and max loss per contract.
    
    Args:
        entry_price: Fill price of the entry order
        quantity: Number of contracts/shares
        action: Order action ('Buy to Open', 'Sell to Open', 'Buy to Close', 'Sell to Close', 'Buy', 'Sell')
        is_option: Whether this is an option (affects multiplier)
        max_loss_per_contract: Maximum loss per contract in dollars (default: 50.0)
    
    Returns:
        Stop-loss price
    """
    # Dynamic max loss: max_loss_per_contract × quantity
    max_loss = max_loss_per_contract * quantity
    
    # Calculate price difference
    # For options: max_loss / (quantity * 100) = price difference per contract
    # For stocks: max_loss / quantity = price difference per share
    multiplier = 100 if is_option else 1
    price_diff = max_loss / (quantity * multiplier)
    
    # Determine direction based on action
    action_lower = action.lower()
    is_buy = 'buy' in action_lower
    is_sell = 'sell' in action_lower
    
    # For buy orders (Buy to Open, Buy to Close, Buy), stop is below entry
    # For sell orders (Sell to Open, Sell to Close, Sell), stop is above entry
    if is_buy:
        stop_price = entry_price - price_diff
    elif is_sell:
        stop_price = entry_price + price_diff
    else:
        # Default to buy behavior if action is unclear
        logger.warning(f"Unclear action '{action}', defaulting to buy behavior for stop-loss")
        stop_price = entry_price - price_diff
    
    return round(stop_price, 2)


async def place_stop_loss_order(
    session: Session,
    account: Account,
    leg: OrderLeg,
    instrument_detail: InstrumentDetail,
    entry_fill_price: float,
    dry_run: bool = False,
    max_loss_per_contract: float = 50.0
) -> dict[str, Any] | None:
    """
    Place a stop-loss order for a single leg after entry order fills.
    
    Args:
        session: TastyTrade session
        account: Account object
        leg: Original order leg specification
        instrument_detail: Instrument details for the leg
        entry_fill_price: Fill price of the entry order
        dry_run: If True, validate order without placing it
    
    Returns:
        Order result dict or None if placement fails
    """
    try:
        instrument = instrument_detail.instrument
        is_option = isinstance(instrument, Option)
        
        # Calculate stop-loss price
        stop_price = calculate_stop_loss_price(
            entry_price=entry_fill_price,
            quantity=leg.quantity,
            action=leg.action,
            is_option=is_option,
            max_loss_per_contract=max_loss_per_contract
        )
        
        # Determine opposite action for stop-loss
        # Note: Stop-loss is only valid for "to open" positions
        # "to close" positions don't need stop-loss as they're already exiting
        action_lower = leg.action.lower()
        if 'buy' in action_lower:
            # For buy orders, stop-loss is a sell order
            if is_option:
                # Only place stop-loss for "Buy to Open" (long positions)
                # "Buy to Close" means closing a short, no stop-loss needed
                if 'to open' in action_lower:
                    stop_action = 'Sell to Close'
                elif 'to close' in action_lower:
                    logger.warning(f"Entry action '{leg.action}' is already a closing trade, skipping stop-loss")
                    return None
                else:
                    # Fallback: assume "Buy to Open" if unclear
                    stop_action = 'Sell to Close'
            else:
                stop_action = 'Sell'
        elif 'sell' in action_lower:
            # For sell orders, stop-loss is a buy order
            if is_option:
                # Only place stop-loss for "Sell to Open" (short positions)
                # "Sell to Close" means closing a long, no stop-loss needed
                if 'to open' in action_lower:
                    stop_action = 'Buy to Close'
                elif 'to close' in action_lower:
                    logger.warning(f"Entry action '{leg.action}' is already a closing trade, skipping stop-loss")
                    return None
                else:
                    # Fallback: assume "Sell to Open" if unclear
                    stop_action = 'Buy to Close'
            else:
                stop_action = 'Buy'
        else:
            logger.error(f"Unknown action '{leg.action}' for stop-loss calculation")
            return None
        
        # Build stop-loss leg
        stop_leg_spec = OrderLeg(
            symbol=leg.symbol,
            action=stop_action,
            quantity=leg.quantity,
            option_type=leg.option_type,
            strike_price=leg.strike_price,
            expiration_date=leg.expiration_date
        )
        
        # Build order leg
        order_action = (
            OrderAction(stop_action) if is_option
            else OrderAction.BUY if stop_action == 'Buy' else OrderAction.SELL
        )
        stop_leg = instrument.build_leg(Decimal(str(leg.quantity)), order_action)
        
        # Build stop order using build_new_order to properly handle stop_price
        stop_order = build_new_order(
            order_type='Stop',
            legs=[stop_leg],
            time_in_force='Day',
            stop_price=Decimal(str(stop_price))
        )
        
        # Place stop-loss order
        logger.info(f"Placing stop-loss order for {leg.symbol}: {stop_action} {leg.quantity} @ stop ${stop_price:.2f} (entry was {leg.action} @ ${entry_fill_price:.2f})")
        
        # Validate stop price is reasonable (not negative, not zero for options)
        if stop_price <= 0:
            logger.error(f"Invalid stop price ${stop_price:.2f} calculated for {leg.symbol} (entry: ${entry_fill_price:.2f})")
            return None
        
        result = await account.a_place_order(session, stop_order, dry_run=dry_run)
        
        # Check if order was rejected - check model_dump() for status
        result_dict = result.model_dump() if hasattr(result, 'model_dump') else {}
        if isinstance(result_dict, dict) and result_dict.get('status') in ['rejected', 'canceled']:
            logger.error(f"Stop-loss order rejected for {leg.symbol}: {result_dict.get('message', 'Unknown reason')}")
            return None
        
        return result_dict if isinstance(result_dict, dict) else result.model_dump()
    except Exception as e:
        logger.error(f"Failed to place stop-loss order for {leg.symbol}: {e}", exc_info=True)
        import traceback
        logger.debug(f"Stop-loss error traceback: {traceback.format_exc()}")
        return None


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
        table_result = result.output if hasattr(result, 'output') and result.output is not None else (getattr(result, 'data', None) if hasattr(result, 'data') else str(result))
        claude_analysis = result.output if hasattr(result, 'output') and result.output is not None else None
        
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
        table_result = result.output if hasattr(result, 'output') and result.output is not None else str(result)
        
        # Also get raw Greeks for structured response
        session = get_valid_session(api_key)
        option_details = await get_instrument_details(session, options)
        greeks = await _stream_events(session, Greeks, [d.streamer_symbol for d in option_details], timeout)
        
        return {
            "greeks": [g.model_dump() for g in greeks],
            "table": table_result,
            "claude_analysis": result.output if hasattr(result, 'output') and result.output is not None else None
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
        table_result = result.output if hasattr(result, 'output') and result.output is not None else str(result)
        
        # Also get raw metrics for structured response
        session = get_valid_session(api_key)
        metrics = await a_get_market_metrics(session, symbols)
        
        return {
            "metrics": [m.model_dump() for m in metrics],
            "table": table_result,
            "claude_analysis": result.output if hasattr(result, 'output') and result.output is not None else None
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
            if hasattr(claude_result, 'output') and claude_result.output is not None:
                results.append({"claude_analysis": claude_result.output})
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
        table_result = result.output if hasattr(result, 'output') and result.output is not None else str(result)
        
        # Also get raw results for structured response
        session = get_valid_session(api_key)
        async with rate_limiter:
            results = await a_symbol_search(session, symbol)
        
        return {
            "results": [r.model_dump() for r in results],
            "table": table_result,
            "claude_analysis": result.output if hasattr(result, 'output') and result.output is not None else None
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


@app.get("/api/v1/recent-trades")
async def get_recent_trades(
    days: int = 7,
    underlying_symbol: str | None = None,
    session_and_account: tuple[Session, Account] = Depends(get_session_and_account)
) -> dict[str, Any]:
    """
    Get recent trades with calculated P&L.
    Returns trades formatted similar to TastyTrade UI with Date/Time, Symbol, Action, Quantity, Price, Fees, and P&L.
    """
    session, account = session_and_account
    start = date.today() - timedelta(days=days)
    
    # Get only trade transactions
    trades = await _paginate(
        lambda offset: account.a_get_history(
            session, start_date=start, underlying_symbol=underlying_symbol,
            type="Trade", per_page=250, page_offset=offset
        ),
        page_size=250
    )
    
    # Format trades with P&L calculation
    formatted_trades = []
    position_tracker: dict[str, list[dict[str, Any]]] = {}  # Track open positions by symbol
    
    for trade in trades:
        trade_dict = trade.model_dump()
        
        # Extract key fields (field names may vary in tastytrade library)
        symbol = trade_dict.get("symbol") or trade_dict.get("instrument_symbol") or trade_dict.get("underlying_symbol", "")
        action = trade_dict.get("action") or trade_dict.get("transaction_sub_type") or ""
        quantity = float(trade_dict.get("quantity", 0))
        price = float(trade_dict.get("price") or trade_dict.get("fill_price") or trade_dict.get("value", 0))
        fees = float(trade_dict.get("fees") or trade_dict.get("commission") or trade_dict.get("fee", 0))
        
        # Get transaction date/time
        trans_date = trade_dict.get("transaction_date") or trade_dict.get("executed_at") or trade_dict.get("date")
        if isinstance(trans_date, str):
            try:
                trans_date = datetime.fromisoformat(trans_date.replace("Z", "+00:00"))
            except:
                trans_date = datetime.now()
        elif hasattr(trans_date, "isoformat"):
            trans_date = trans_date
        else:
            trans_date = datetime.now()
        
        # Format date/time
        if isinstance(trans_date, datetime):
            date_str = trans_date.strftime("%b %d, %Y, %I:%M %p")
        else:
            date_str = str(trans_date)
        
        # Calculate P&L for closed positions
        pnl = None
        pnl_display = "—"
        
        # First, try to get P&L directly from transaction fields (most reliable)
        # Check various possible field names for P&L
        pnl_fields = ["realized_pnl", "pnl", "profit_loss", "net_amount", "value_effect", 
                     "realized_profit_loss", "realized_pnl_dollars"]
        for field in pnl_fields:
            if field in trade_dict and trade_dict[field] is not None:
                try:
                    pnl = float(trade_dict[field])
                    pnl_display = f"${pnl:.2f}"
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
                        pnl_display = f"${pnl:.2f}"
        
        # Track open positions for future P&L calculation
        if "Buy to Open" in action or "Sell to Open" in action:
            if symbol not in position_tracker:
                position_tracker[symbol] = []
            position_tracker[symbol].append({
                "price": price,
                "quantity": quantity,
                "fees": fees
            })
        
        formatted_trades.append({
            "date_time": date_str,
            "symbol": symbol,
            "action": action,
            "quantity": int(quantity),
            "price": f"${price:.2f}",
            "fees": f"${fees:.2f}",
            "pnl": pnl_display,
            "pnl_value": pnl,  # Raw numeric value for calculations
            "raw_transaction": trade_dict  # Include full transaction data
        })
    
    # Calculate summary statistics
    total_pnl = sum(t["pnl_value"] for t in formatted_trades if t["pnl_value"] is not None)
    winning_trades = sum(1 for t in formatted_trades if t["pnl_value"] is not None and t["pnl_value"] > 0)
    losing_trades = sum(1 for t in formatted_trades if t["pnl_value"] is not None and t["pnl_value"] < 0)
    total_trades = len(formatted_trades)
    
    # Build formatted table
    table_lines = ["Recent Trades\n"]
    table_lines.append(f"{'Date/Time':<25} {'Symbol':<30} {'Action':<20} {'Quantity':<10} {'Price':<10} {'Fees':<10} {'P&L':<10}")
    table_lines.append("-" * 125)
    
    for trade in formatted_trades:
        table_lines.append(
            f"{trade['date_time']:<25} {trade['symbol']:<30} {trade['action']:<20} "
            f"{trade['quantity']:<10} {trade['price']:<10} {trade['fees']:<10} {trade['pnl']:<10}"
        )
    
    table_lines.append("-" * 125)
    table_lines.append(f"\nSummary: {total_trades} trades | Wins: {winning_trades} | Losses: {losing_trades} | Total P&L: ${total_pnl:.2f}")
    
    return {
        "trades": formatted_trades,
        "summary": {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "total_pnl": round(total_pnl, 2),
            "average_pnl": round(total_pnl / total_trades, 2) if total_trades > 0 else 0
        },
        "table": "\n".join(table_lines)
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
    order_type: Literal['Market', 'Limit', 'Stop', 'StopLimit', 'TrailingStop'] | None = None,
    price: float | None = None,
    stop_price: float | None = None,
    trail_price: float | None = None,
    trail_percent: float | None = None,
    time_in_force: Literal['Day', 'GTC', 'IOC'] | None = None,
    dry_run: bool = False,
    api_key: str = Depends(verify_api_key),
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
        
        # Get user settings and apply defaults if not provided
        user_settings = get_user_settings(api_key)
        if order_type is None:
            # Always default to 'Limit' for safety (user can explicitly pass 'Market' if needed)
            # User settings default_order_type is only used as a hint, not enforced
            order_type = 'Limit'
        if time_in_force is None:
            time_in_force = user_settings.default_time_in_force
        
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
        
        # Place entry order
        entry_result = await account.a_place_order(session, new_order, dry_run=dry_run)
        entry_order_data = entry_result.model_dump()
        
        # Initialize response with entry order
        response = {
            "entry_order": entry_order_data,
            "stop_loss_orders": [],
            "auto_stop_loss_enabled": user_settings.auto_stop_loss_enabled,
            "max_loss_per_leg": [user_settings.max_loss_per_contract * leg.quantity for leg in legs]
        }
        
        # If dry_run or auto_stop_loss disabled, skip stop-loss placement
        if dry_run or not user_settings.auto_stop_loss_enabled:
            response["stop_loss_orders"] = None
            if not user_settings.auto_stop_loss_enabled:
                response["stop_loss_skipped_reason"] = "Auto stop-loss is disabled in settings"
            return response
        
        # Extract order ID from result
        entry_order_id = None
        if isinstance(entry_order_data, dict):
            entry_order_id = entry_order_data.get('id') or entry_order_data.get('order_id')
        elif hasattr(entry_result, 'model_dump'):
            result_dict = entry_result.model_dump()
            if isinstance(result_dict, dict):
                entry_order_id = result_dict.get('id') or result_dict.get('order_id')
        
        if not entry_order_id:
            logger.warning("Could not extract order ID from entry order result, skipping stop-loss placement")
            response["stop_loss_orders"] = None
            return response
        
        # Monitor entry order fill and place stop-loss orders
        try:
            # Monitor for fill (only for Market orders or if order might fill quickly)
            # For Limit orders, we'll try to monitor but timeout quickly if it doesn't fill
            timeout = 10 if order_type == 'Market' else 5  # Shorter timeout for limit orders
            
            try:
                filled_order = await monitor_order_fill(session, account, int(entry_order_id), timeout=timeout)
                
                # Get fill prices for each leg
                # For stop-loss calculation, we need individual leg prices
                # Try to get from order result first, then fall back to current market quotes
                fill_prices = []
                
                # First, try to get current market quotes (most reliable for stop-loss)
                try:
                    quotes = await _fetch_quotes_raw(session, instrument_details, timeout=5.0)
                    for i, (quote, leg) in enumerate(zip(quotes, legs)):
                        if quote.bid_price is not None and quote.ask_price is not None:
                            # Use mid price as fill price estimate
                            mid_price = float(quote.bid_price + quote.ask_price) / 2
                            fill_prices.append(mid_price)
                        else:
                            fill_prices.append(0.0)
                except Exception as e:
                    logger.warning(f"Failed to fetch quotes for fill price: {e}")
                    fill_prices = [0.0] * len(legs)
                
                # Try to override with actual fill prices from order if available
                if hasattr(filled_order, 'legs') and filled_order.legs:
                    for i, leg in enumerate(filled_order.legs):
                        if i < len(fill_prices):
                            # Try to get fill price from leg
                            if hasattr(leg, 'fill_price') and leg.fill_price:
                                fill_prices[i] = float(leg.fill_price)
                            elif hasattr(leg, 'average_fill_price') and leg.average_fill_price:
                                fill_prices[i] = float(leg.average_fill_price)
                
                # Also try to get from order data dict
                if isinstance(entry_order_data, dict):
                    if 'legs' in entry_order_data:
                        for i, leg_data in enumerate(entry_order_data['legs']):
                            if i < len(fill_prices) and isinstance(leg_data, dict):
                                fill_price = leg_data.get('fill_price') or leg_data.get('average_fill_price')
                                if fill_price:
                                    fill_prices[i] = float(fill_price)
                
                # Final fallback: use limit price if available (for limit orders)
                for i in range(len(fill_prices)):
                    if fill_prices[i] <= 0 and price_decimal:
                        fill_prices[i] = float(price_decimal)
                
                # Place stop-loss orders for each leg
                stop_loss_results = []
                for i, (leg, instrument_detail) in enumerate(zip(legs, instrument_details, strict=True)):
                    fill_price = fill_prices[i] if i < len(fill_prices) else fill_prices[0] if fill_prices else 0.0
                    
                    if fill_price <= 0:
                        logger.warning(f"Invalid fill price {fill_price} for leg {i}, skipping stop-loss")
                        stop_loss_results.append({"leg_index": i, "order": None, "error": "Invalid fill price"})
                        continue
                    
                    stop_result = await place_stop_loss_order(
                        session=session,
                        account=account,
                        leg=leg,
                        instrument_detail=instrument_detail,
                        entry_fill_price=fill_price,
                        dry_run=False,
                        max_loss_per_contract=user_settings.max_loss_per_contract
                    )
                    
                    if stop_result:
                        stop_loss_results.append({"leg_index": i, "order": stop_result})
                    else:
                        stop_loss_results.append({"leg_index": i, "order": None, "error": "Failed to place stop-loss"})
                
                response["stop_loss_orders"] = stop_loss_results
                
            except TimeoutError:
                # Order didn't fill within timeout - this is OK for limit orders
                logger.info(f"Entry order {entry_order_id} did not fill within timeout, stop-loss will be placed when order fills")
                response["stop_loss_orders"] = None
                response["stop_loss_pending"] = True
            except ValueError as e:
                # Order was rejected or canceled
                logger.warning(f"Entry order {entry_order_id} was rejected/canceled: {e}")
                response["stop_loss_orders"] = None
                response["stop_loss_error"] = str(e)
        
        except Exception as e:
            # Log error but don't fail the entire request
            logger.error(f"Error placing stop-loss orders: {e}")
            response["stop_loss_orders"] = None
            response["stop_loss_error"] = str(e)
        
        return response


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
                "claude_analysis": claude_result.output if hasattr(claude_result, 'output') and claude_result.output is not None else str(claude_result)
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


@app.get("/api/v1/quick-queries")
async def get_quick_queries(
    symbol: str | None = None,
    session: Session = Depends(get_session_only)
) -> dict[str, Any]:
    """Get time-based templated queries for quick AI chat interactions.
    
    Returns queries based on current EST time:
    - 0930-1000: Direction of market queries
    - 1000-1015: Trend queries
    - 1020-1130: Find positions for calls and puts queries
    """
    current_time_nyc = now_in_new_york()
    current_hour = current_time_nyc.hour
    current_minute = current_time_nyc.minute
    time_minutes = current_hour * 60 + current_minute
    
    # Default symbol if not provided
    symbol_placeholder = symbol or "{{SYMBOL}}"
    
    queries = []
    active_window = None
    
    # 0930-1000 EST: Direction queries
    if 9 * 60 + 30 <= time_minutes < 10 * 60:
        active_window = "0930-1000"
        queries = [
            {
                "title": "Market Direction",
                "message": f"What is the current direction of {symbol_placeholder}?",
                "timeWindow": "0930-1000",
                "category": "direction"
            },
            {
                "title": "Bullish or Bearish?",
                "message": f"Is {symbol_placeholder} bullish or bearish right now?",
                "timeWindow": "0930-1000",
                "category": "direction"
            },
            {
                "title": "Opening Direction",
                "message": f"What's the opening direction for {symbol_placeholder}?",
                "timeWindow": "0930-1000",
                "category": "direction"
            }
        ]
    # 1000-1015 EST: Trend queries
    elif 10 * 60 <= time_minutes < 10 * 60 + 15:
        active_window = "1000-1015"
        queries = [
            {
                "title": "Current Trend",
                "message": f"What is the current trend for {symbol_placeholder}?",
                "timeWindow": "1000-1015",
                "category": "trend"
            },
            {
                "title": "Trend Direction",
                "message": f"Is {symbol_placeholder} trending up or down?",
                "timeWindow": "1000-1015",
                "category": "trend"
            },
            {
                "title": "Trend Strength",
                "message": f"Analyze the trend strength for {symbol_placeholder}",
                "timeWindow": "1000-1015",
                "category": "trend"
            }
        ]
    # 1020-1130 EST: Position finding queries
    elif 10 * 60 + 20 <= time_minutes < 11 * 60 + 30:
        active_window = "1020-1130"
        queries = [
            {
                "title": "Best Call Options",
                "message": f"Find the best call options for {symbol_placeholder}",
                "timeWindow": "1020-1130",
                "category": "positions"
            },
            {
                "title": "Best Put Options",
                "message": f"Find the best put options for {symbol_placeholder}",
                "timeWindow": "1020-1130",
                "category": "positions"
            },
            {
                "title": "Call & Put Positions",
                "message": f"What are good call and put positions for {symbol_placeholder} right now?",
                "timeWindow": "1020-1130",
                "category": "positions"
            }
        ]
    
    return {
        "queries": queries,
        "currentTime": current_time_nyc.isoformat(),
        "activeWindow": active_window
    }


@app.post("/api/v1/credentials")
async def add_or_update_credentials(
    credential: CredentialEntry
) -> dict[str, Any]:
    """Add or update credentials for an API key. Saves to SQLite database."""
    global api_key_credentials, credentials_db
    
    if not credentials_db:
        raise HTTPException(
            status_code=500,
            detail="Database not initialized"
        )
    
    # Save to database
    try:
        credentials_db.insert_or_update_credentials(
            credential.api_key,
            credential.client_secret,
            credential.refresh_token
        )
    except Exception as e:
        logger.error(f"Failed to save credentials to database: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save credentials: {e}"
        ) from e
    
    # Reload credentials in memory
    api_key_credentials = load_credentials()
    
    # Clear any existing session for this API key to force re-authentication
    if credential.api_key in api_key_sessions:
        del api_key_sessions[credential.api_key]
        logger.info(f"Cleared existing session for API key {credential.api_key[:8]}...")
    
    # Clear any existing agents for this API key to force recreation with new credentials
    global api_key_agents
    keys_to_delete = [key for key in api_key_agents.keys() if key[0] == credential.api_key]
    for key in keys_to_delete:
        del api_key_agents[key]
    if keys_to_delete:
        logger.info(f"Cleared {len(keys_to_delete)} agent(s) for API key {credential.api_key[:8]}... to use new credentials")
    
    logger.info(f"Added/updated credentials for API key {credential.api_key[:8]}...")
    return {
        "success": True,
        "api_key": credential.api_key,
        "message": "Credentials saved successfully. Existing sessions and agents cleared."
    }


@app.get("/api/v1/credentials")
async def list_credentials(
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """List all configured API keys (without sensitive data). Requires authentication."""
    global credentials_db
    
    if not credentials_db:
        raise HTTPException(
            status_code=500,
            detail="Database not initialized"
        )
    
    try:
        api_key_list = credentials_db.list_api_keys()
        api_keys = [{"api_key": key, "configured": True} for key in api_key_list]
        
        return {
            "api_keys": api_keys,
            "count": len(api_keys)
        }
    except Exception as e:
        logger.error(f"Failed to list credentials: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list credentials: {e}"
        ) from e


@app.delete("/api/v1/credentials/{api_key}")
async def delete_credentials(api_key: str) -> dict[str, Any]:
    """Delete credentials for an API key."""
    global api_key_credentials, credentials_db
    
    if not credentials_db:
        raise HTTPException(
            status_code=500,
            detail="Database not initialized"
        )
    
    # Delete from database
    try:
        deleted = credentials_db.delete_credentials(api_key)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"API key '{api_key}' not found in credentials"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete credentials: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete credentials: {e}"
        ) from e
    
    # Reload credentials in memory
    api_key_credentials = load_credentials()
    
    # Clear any existing session for this API key
    if api_key in api_key_sessions:
        del api_key_sessions[api_key]
        logger.info(f"Cleared existing session for API key {api_key[:8]}...")
    
    # Clear any existing agents for this API key
    global api_key_agents
    keys_to_delete = [key for key in api_key_agents.keys() if key[0] == api_key]
    for key in keys_to_delete:
        del api_key_agents[key]
    if keys_to_delete:
        logger.info(f"Cleared {len(keys_to_delete)} agent(s) for API key {api_key[:8]}...")
    
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
    message_history: list[ChatMessage] | None = Field(None, description="Optional conversation history (overrides tabId history if provided)")
    images: list[str] | None = Field(None, description="Optional list of base64-encoded images")
    tab_id: str | None = Field(None, alias="tabId", description="Optional tab ID for maintaining separate conversation contexts per tab")
    account_id: str | None = Field(None, description="Optional TastyTrade account ID. If not provided, uses default account from credentials.")
    
    model_config = {"populate_by_name": True}  # Allow both tab_id and tabId


class ChatResponse(BaseModel):
    """Chat response model."""
    response: str = Field(..., description="Agent's response")
    message_history: list[ChatMessage] = Field(..., description="Updated conversation history")


def _serialize_error(error: Exception) -> dict[str, Any]:
    """Safely serialize an exception to a JSON-serializable dict."""
    error_type = type(error).__name__
    error_msg = str(error)
    
    # Build error response with consistent structure
    error_response = {
        "type": "error",
        "error": error_msg,
        "error_type": error_type,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    
    # Add additional context for HTTP exceptions
    if isinstance(error, HTTPException):
        error_response["status_code"] = str(error.status_code)
        error_response["detail"] = str(error.detail)
    
    return error_response


def format_message_with_images(text: str, images: list[str] | None) -> str | list[str | BinaryContent]:
    """
    Format message content with images for pydantic-ai Agent.
    Returns either a string (text only) or a list containing text and BinaryContent objects (text + images).
    
    Args:
        text: The text message content
        images: Optional list of base64-encoded images (may include data URI prefix)
    
    Returns:
        str if no images, or list[str, BinaryContent, ...] if images are present
    """
    if not images or len(images) == 0:
        return text
    
    # Build content list: text first, then images as BinaryContent objects
    content_items: list[str | BinaryContent] = [text]
    
    for image_data in images:
        # Extract base64 data and media type
        # Format: "data:image/png;base64,{base64_data}" or just "{base64_data}"
        if image_data.startswith("data:image/"):
            # Extract media type and base64 data
            parts = image_data.split(",", 1)
            if len(parts) == 2:
                # Extract media type from "data:image/png;base64"
                media_type_part = parts[0].split(":")[1]  # "image/png;base64"
                media_type = media_type_part.split(";")[0]  # "image/png"
                base64_data = parts[1]
            else:
                # Fallback if format is unexpected
                media_type = "image/png"
                base64_data = image_data
        else:
            # Assume PNG if no prefix
            media_type = "image/png"
            base64_data = image_data
        
        # Decode base64 to binary data
        try:
            binary_data = base64.b64decode(base64_data)
            content_items.append(BinaryContent(data=binary_data, media_type=media_type))
        except Exception as e:
            logger.warning(f"Failed to decode base64 image data: {e}. Skipping image.")
            continue
    
    return content_items


@app.post("/api/v1/chat/stream", include_in_schema=True)
async def chat_stream(
    request: ChatRequest,
    api_key: str = Depends(verify_api_key)
):
    """Stream chat responses from the Claude agent using Server-Sent Events (SSE)."""
    async def generate_stream():
        try:
            try:
                # Get account_id from request, if provided
                account_id = request.account_id
                agent = get_claude_agent(api_key, account_id=account_id)
            except HTTPException as http_err:
                error_data = _serialize_error(http_err)
                try:
                    yield f"data: {json.dumps(error_data)}\n\n"
                except (TypeError, ValueError) as json_err:
                    logger.error(f"Failed to serialize error: {json_err}")
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Failed to initialize AI agent', 'error_type': type(http_err).__name__})}\n\n"
                return
            except Exception as agent_err:
                logger.error(f"Failed to get/create agent for API key {api_key[:8]}...: {agent_err}", exc_info=True)
                error_data = _serialize_error(agent_err)
                error_data["error"] = f"Failed to initialize AI agent: {error_data['error']}"
                try:
                    yield f"data: {json.dumps(error_data)}\n\n"
                except (TypeError, ValueError) as json_err:
                    logger.error(f"Failed to serialize error: {json_err}")
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Failed to initialize AI agent', 'error_type': type(agent_err).__name__})}\n\n"
                return
            
            # Get conversation history - prefer message_history from request, otherwise use tabId history
            conversation_history: list[ChatMessage] = []
            if request.message_history:
                # Explicit message_history takes precedence
                conversation_history = request.message_history
                logger.debug(f"Using explicit message_history with {len(conversation_history)} messages")
            elif request.tab_id:
                # Use stored conversation history for this tab
                conversation_history = get_conversation_history(api_key, request.tab_id)
                logger.debug(f"Using stored conversation history for tab_id={request.tab_id} with {len(conversation_history)} messages")
            
            # Extract symbol from tabId for context
            current_symbol: str | None = None
            if request.tab_id:
                current_symbol = extract_symbol_from_tab_id(request.tab_id)
                if current_symbol:
                    logger.info(f"Extracted symbol '{current_symbol}' from tab_id: {request.tab_id}")
            
            # Build user message with history context and images
            user_message = request.message
            if conversation_history:
                context_parts = []
                for msg in conversation_history:
                    context_parts.append(f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}")
                context = "\n".join(context_parts)
                user_message = f"Previous conversation:\n{context}\n\nCurrent question: {request.message}"
            
            # Add symbol context at the beginning if available (highly visible)
            if current_symbol:
                symbol_context = f"Current symbol context: {current_symbol}\n\n"
                user_message = symbol_context + user_message
            
            # Format message content with images if provided
            message_content = format_message_with_images(user_message, request.images)
            if request.images:
                image_count = len(request.images)
                logger.info(f"Processing chat request with {image_count} image(s)")
            
            # Log message preview (handle both string and list formats)
            message_preview = message_content if isinstance(message_content, str) else f"{user_message[:50]}... (with {len(request.images or [])} image(s))"
            logger.info(f"Starting streaming Claude agent run for message: {message_preview[:100]}...")
            
            # Send initial "thinking" event for immediate feedback
            yield f"data: {json.dumps({'type': 'status', 'content': 'Processing request...'})}\n\n"
            
            try:
                async with agent:
                    # Send "analyzing" status
                    yield f"data: {json.dumps({'type': 'status', 'content': 'Analyzing market data and preparing response...'})}\n\n"
                    
                    # Run the agent with message (supports both string and list with BinaryContent)
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
                    elif hasattr(result, 'all_messages'):
                        # Try to extract from messages
                        messages = result.all_messages() if callable(result.all_messages) else result.all_messages
                        if messages:
                            # Get the last assistant message
                            for msg in reversed(messages):
                                content = getattr(msg, 'content', None)
                                if content is not None:
                                    response_text = str(content)
                                    break
                    
                    if not response_text:
                        response_text = str(result) if result else "No response generated"
                    
                    # Save conversation messages to history if tabId is provided
                    if request.tab_id:
                        try:
                            # Save user message
                            save_conversation_message(
                                api_key, 
                                request.tab_id, 
                                ChatMessage(role="user", content=request.message)
                            )
                            # Save assistant response
                            save_conversation_message(
                                api_key,
                                request.tab_id,
                                ChatMessage(role="assistant", content=response_text)
                            )
                            logger.debug(f"Saved conversation messages for tab_id={request.tab_id}")
                        except Exception as save_err:
                            logger.warning(f"Failed to save conversation messages for tab_id={request.tab_id}: {save_err}")
                    
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
                timeout_error = {
                    "type": "error",
                    "error": "Request timed out. The query may be too complex.",
                    "error_type": "TimeoutError",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                try:
                    yield f"data: {json.dumps(timeout_error)}\n\n"
                except (TypeError, ValueError) as json_err:
                    logger.error(f"Failed to serialize timeout error: {json_err}")
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Request timed out'})}\n\n"
            except Exception as e:
                error_msg = str(e)
                # Provide more helpful error messages for common issues
                if "exceeded max retries" in error_msg:
                    # Tool retry exceeded - provide context
                    tool_name = "unknown tool"
                    if "'" in error_msg:
                        tool_name = error_msg.split("'")[1] if len(error_msg.split("'")) > 1 else "unknown tool"
                    logger.error(f"Error in streaming chat: {e}", exc_info=True)
                    error_data = _serialize_error(e)
                    error_data["error"] = f"The {tool_name} tool failed after retries. This may indicate a temporary issue with the trading system or invalid parameters. Please try again or check your request parameters."
                    error_data["tool_name"] = tool_name
                else:
                    logger.error(f"Error in streaming chat: {e}", exc_info=True)
                    error_data = _serialize_error(e)
                
                try:
                    yield f"data: {json.dumps(error_data)}\n\n"
                except (TypeError, ValueError) as json_err:
                    logger.error(f"Failed to serialize error: {json_err}")
                    yield f"data: {json.dumps({'type': 'error', 'error': 'An error occurred while processing your request', 'error_type': type(e).__name__})}\n\n"
        except Exception as e:
            logger.error(f"Error in chat stream endpoint: {e}", exc_info=True)
            error_data = _serialize_error(e)
            error_data["error"] = f"Error processing chat message: {error_data['error']}"
            try:
                yield f"data: {json.dumps(error_data)}\n\n"
            except (TypeError, ValueError) as json_err:
                logger.error(f"Failed to serialize outer error: {json_err}")
                yield f"data: {json.dumps({'type': 'error', 'error': 'Error processing chat message', 'error_type': type(e).__name__})}\n\n"
    
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
            # Get account_id from request, if provided
            account_id = request.account_id
            agent = get_claude_agent(api_key, account_id=account_id)
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
        
        # Get conversation history - prefer message_history from request, otherwise use tabId history
        conversation_history: list[ChatMessage] = []
        if request.message_history:
            # Explicit message_history takes precedence
            conversation_history = request.message_history
            logger.debug(f"Using explicit message_history with {len(conversation_history)} messages")
        elif request.tab_id:
            # Use stored conversation history for this tab
            conversation_history = get_conversation_history(api_key, request.tab_id)
            logger.debug(f"Using stored conversation history for tab_id={request.tab_id} with {len(conversation_history)} messages")
        
        # Extract symbol from tabId for context
        current_symbol: str | None = None
        if request.tab_id:
            current_symbol = extract_symbol_from_tab_id(request.tab_id)
            if current_symbol:
                logger.info(f"Extracted symbol '{current_symbol}' from tab_id: {request.tab_id}")
        
        # Build user message with history context
        user_message = request.message
        if conversation_history:
            # Build context from history
            context_parts = []
            for msg in conversation_history:
                context_parts.append(f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}")
            context = "\n".join(context_parts)
            user_message = f"Previous conversation:\n{context}\n\nCurrent question: {request.message}"
        
        # Add symbol context at the beginning if available (highly visible)
        if current_symbol:
            symbol_context = f"Current symbol context: {current_symbol}\n\n"
            user_message = symbol_context + user_message
        
        # Format message content with images if provided
        message_content = format_message_with_images(user_message, request.images)
        if request.images:
            image_count = len(request.images)
            logger.info(f"Processing chat request with {image_count} image(s)")
        
        # Run Claude agent with timeout
        # Note: We use async with to ensure proper cleanup, but the agent is cached per API key
        # so the MCP server connection is reused across requests
        import asyncio
        # Log message preview (handle both string and list formats)
        message_preview = message_content if isinstance(message_content, str) else f"{user_message[:50]}... (with {len(request.images or [])} image(s))"
        logger.info(f"Starting Claude agent run for message: {message_preview[:100]}...")
        start_time = asyncio.get_event_loop().time()
        
        try:
            async with agent:
                result = await asyncio.wait_for(
                    agent.run(message_content),
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
        elif hasattr(result, 'all_messages'):
            # Try to extract from messages
            messages = result.all_messages() if callable(result.all_messages) else result.all_messages
            if messages:
                # Get the last assistant message
                for msg in reversed(messages):
                    content = getattr(msg, 'content', None)
                    if content is not None:
                        response_text = str(content)
                        break
        
        if not response_text:
            response_text = str(result) if result else "No response generated"
            logger.warning(f"Could not extract response text from result: {type(result)}, attributes: {dir(result)}")
        
        # Get new messages from result
        new_messages = result.new_messages() if hasattr(result, 'new_messages') else []
        
        # Save conversation messages to history if tabId is provided
        if request.tab_id:
            try:
                # Save user message
                save_conversation_message(
                    api_key,
                    request.tab_id,
                    ChatMessage(role="user", content=request.message)
                )
                # Save assistant response
                save_conversation_message(
                    api_key,
                    request.tab_id,
                    ChatMessage(role="assistant", content=response_text)
                )
                logger.debug(f"Saved conversation messages for tab_id={request.tab_id}")
            except Exception as save_err:
                logger.warning(f"Failed to save conversation messages for tab_id={request.tab_id}: {save_err}")
        
        # Build updated history for response
        updated_history = conversation_history.copy()
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


# =============================================================================
# SETTINGS ENDPOINTS
# =============================================================================

@app.get("/api/v1/settings")
async def get_settings(
    api_key: str = Depends(verify_api_key)
) -> UserSettings:
    """Get user settings for the authenticated API key."""
    settings = get_user_settings(api_key)
    return settings


@app.post("/api/v1/settings")
async def create_or_update_settings(
    settings_update: UserSettingsUpdate,
    api_key: str = Depends(verify_api_key)
) -> UserSettings:
    """Create or update user settings for the authenticated API key."""
    global api_key_settings
    
    # Get current settings or defaults
    current_settings = get_user_settings(api_key)
    
    # Update with provided values
    updated_dict = current_settings.model_dump()
    update_dict = settings_update.model_dump(exclude_unset=True)
    updated_dict.update(update_dict)
    
    # Create new settings object
    new_settings = UserSettings(**updated_dict)
    
    # Save to memory and file
    api_key_settings[api_key] = new_settings
    save_settings(api_key_settings)
    
    logger.info(f"Updated settings for API key {api_key[:8]}...")
    return new_settings


@app.patch("/api/v1/settings")
async def patch_settings(
    settings_update: UserSettingsUpdate,
    api_key: str = Depends(verify_api_key)
) -> UserSettings:
    """Partially update user settings (same as POST, but semantically PATCH)."""
    return await create_or_update_settings(settings_update, api_key)


# =============================================================================
# TRADING MODE ENDPOINTS
# =============================================================================

class TradingModeRequest(BaseModel):
    """Request model for updating trading mode."""
    mode: Literal['custom', 'day', 'scalp', 'swing'] = Field(..., description="Trading mode: custom, day, scalp, or swing")


@app.get("/api/v1/trading-mode")
async def get_trading_mode(
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get current trading mode from environment variable, including full instructions."""
    current_mode = os.getenv("MODE", "custom").strip().lower()
    
    # Validate mode
    valid_modes = ['custom', 'day', 'scalp', 'swing']
    if current_mode not in valid_modes:
        logger.warning(f"Invalid MODE value in environment: {current_mode}. Defaulting to 'custom'.")
        current_mode = "custom"
    
    mode_descriptions = {
        "custom": "Custom trading strategy (uses backend custom prompt)",
        "day": "Day Trader - Multiple trades per day, intraday positions only, end-of-day exits",
        "scalp": "Scalper - Quick in-and-out trades, 1-5 minute timeframes, tight stops, high frequency",
        "swing": "Swing Trader - Multi-day holds, trend following, support/resistance focus, larger targets"
    }
    
    # Load full instructions for the current mode
    full_instructions = load_mode_instructions()
    
    response = {
        "mode": current_mode,
        "description": mode_descriptions.get(current_mode, "Unknown mode"),
        "valid_modes": valid_modes
    }
    
    # Include full instructions if available
    if full_instructions:
        response["instructions"] = full_instructions
        response["instructions_length"] = len(full_instructions)
    
    return response


@app.post("/api/v1/trading-mode")
async def update_trading_mode(
    request: TradingModeRequest,
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Update trading mode in .env file and clear instruction cache."""
    global _instruction_prompt_cache, _mode_instructions_cache, _current_mode
    
    new_mode = request.mode.strip().lower()
    valid_modes = ['custom', 'day', 'scalp', 'swing']
    
    if new_mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{new_mode}'. Valid modes: {', '.join(valid_modes)}"
        )
    
    try:
        # Update .env file
        update_env_file("MODE", new_mode)
        
        # Update environment variable in current process
        os.environ["MODE"] = new_mode
        
        # Clear caches to force reload with new mode
        _instruction_prompt_cache = None
        _mode_instructions_cache = None
        _current_mode = None
        
        # Clear all agent caches so they reload with new instructions
        global api_key_agents
        api_key_agents.clear()
        logger.info(f"Cleared agent cache after mode change to {new_mode}")
        
        mode_descriptions = {
            "custom": "Custom trading strategy (uses backend custom prompt)",
            "day": "Day Trader - Multiple trades per day, intraday positions only, end-of-day exits",
            "scalp": "Scalper - Quick in-and-out trades, 1-5 minute timeframes, tight stops, high frequency",
            "swing": "Swing Trader - Multi-day holds, trend following, support/resistance focus, larger targets"
        }
        
        logger.info(f"Trading mode updated to {new_mode}")
        return {
            "mode": new_mode,
            "description": mode_descriptions.get(new_mode, "Unknown mode"),
            "message": f"Trading mode updated to {new_mode}. Agent cache cleared. New agents will use {new_mode} mode instructions."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update trading mode: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update trading mode: {e}"
        ) from e


# =============================================================================
# LOG ENDPOINTS
# =============================================================================

@app.get("/api/v1/logs")
async def list_logs(
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """List all available log files"""
    try:
        log_files = []
        if LOGS_DIR.exists():
            for log_file in sorted(LOGS_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True):
                stat = log_file.stat()
                # Extract service name and date from filename (e.g., "server_2025-12-25.log")
                stem = log_file.stem
                if "_" in stem:
                    parts = stem.rsplit("_", 1)
                    service = parts[0]
                    date_str = parts[1] if len(parts) > 1 else None
                else:
                    service = stem
                    date_str = None
                
                log_files.append({
                    "filename": log_file.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "service": service,
                    "date": date_str,
                })
        return {"logs": log_files}
    except Exception as e:
        logger.error(f"Error listing logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list logs: {str(e)}"
        )


@app.get("/api/v1/logs/{filename:path}")
async def get_log_content(
    filename: str,
    lines: int = Query(500, description="Number of lines to return (from end of file)"),
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get log file content"""
    try:
        # Security: Only allow reading .log files from LOGS_DIR
        log_file = LOGS_DIR / filename
        if not log_file.exists() or not str(log_file.resolve()).startswith(str(LOGS_DIR.resolve())):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Log file not found"
            )
        
        if not log_file.name.endswith('.log'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .log files can be accessed"
            )
        
        # Read file content
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        
        # Return last N lines
        start_line = max(0, len(all_lines) - lines) if lines else 0
        content_lines = all_lines[start_line:]
        
        return {
            "filename": filename,
            "content": "".join(content_lines),
            "total_lines": len(all_lines),
            "returned_lines": len(content_lines),
            "start_line": start_line + 1,  # 1-indexed
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading log file {filename}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read log file: {str(e)}"
        )


@app.get("/api/v1/admin/logs/docker-compose")
async def get_docker_compose_logs(
    project_name: str = Query(..., description="Docker compose project name"),
    compose_file: str = Query("docker-compose.yml", description="Docker compose file path"),
    tail: int = Query(1000, description="Number of lines to tail"),
    follow: bool = Query(False, description="Follow log output (not implemented for API)"),
    api_key: str = Depends(verify_api_key)
) -> dict[str, Any]:
    """Get docker compose logs for a specific project"""
    try:
        # Build the docker compose command
        cmd = [
            "docker", "compose",
            "--file", compose_file,
            "--project-name", project_name,
            "logs",
            "--tail", str(tail)
        ]
        
        # Note: --follow flag is not supported in this API endpoint as it would require streaming
        # Auto-refresh on the frontend handles continuous updates instead
        
        # Execute the command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,  # 30 second timeout
            cwd=os.getcwd()  # Run from current working directory
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or f"Docker compose command failed with return code {result.returncode}"
            logger.error(f"Docker compose logs command failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get docker compose logs: {error_msg}"
            )
        
        return {
            "content": result.stdout
        }
    except subprocess.TimeoutExpired:
        logger.error(f"Docker compose logs command timed out after 30 seconds")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Docker compose logs command timed out"
        )
    except FileNotFoundError:
        logger.error("Docker compose command not found")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Docker compose is not installed or not available"
        )
    except Exception as e:
        logger.error(f"Error getting docker compose logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get docker compose logs: {str(e)}"
        )


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
    
    # Initialize logging before anything else
    from tasty_agent.logging_config import initialize_tasty_agent_loggers
    initialize_tasty_agent_loggers()
    
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
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


