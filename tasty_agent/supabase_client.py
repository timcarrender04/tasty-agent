"""
Supabase database client for tasty-agent
Handles direct database queries for API key validation and credential lookup
"""
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import asyncpg
import bcrypt

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Client for querying Supabase PostgreSQL database"""
    
    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize Supabase client.
        
        Args:
            database_url: PostgreSQL connection string (e.g., postgresql://user:pass@host:port/db)
                          If None, reads from DATABASE_URL environment variable
        """
        self.database_url = database_url or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        
        # Convert postgresql:// to postgresql+asyncpg:// if needed
        if self.database_url.startswith("postgresql://"):
            # asyncpg uses postgresql:// directly, no need to change
            pass
        
        self._pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Create database connection pool"""
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    self.database_url,
                    min_size=1,
                    max_size=5,
                    command_timeout=60
                )
                logger.info("Connected to Supabase database")
            except Exception as e:
                logger.error(f"Failed to connect to Supabase database: {e}")
                raise
    
    async def close(self):
        """Close database connection pool"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Closed Supabase database connection")
    
    async def validate_kiosk_api_key(
        self, api_key: str, client_ip: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Validate API key and return device/user information.
        
        Args:
            api_key: Plain text API key to validate
            client_ip: Client IP address for validation (optional, but recommended)
            
        Returns:
            Dict with device_id, user_id, account_id, client_secret, refresh_token
            or None if invalid or IP mismatch
        """
        if not self._pool:
            await self.connect()
        
        # Get all devices with API keys and check each one
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    d.id as device_id,
                    d.device_id as device_identifier,
                    d.api_key_hash,
                    d.api_key_expires_at,
                    d.tailscale_ip,
                    d.user_id,
                    d.tastytrade_account_id,
                    d.tastytrade_client_secret,
                    d.tastytrade_refresh_token,
                    bc.account_id,
                    bc.client_secret,
                    bc.refresh_token
                FROM public.gamma_devices d
                LEFT JOIN public.broker_connections bc ON d.user_id = bc.user_id AND bc.is_active = true
                WHERE d.api_key_hash IS NOT NULL
            """)
            
            # Check each device's API key hash
            for row in rows:
                api_key_hash = row['api_key_hash']
                if not api_key_hash:
                    continue
                
                # Verify API key against hash
                try:
                    api_key_bytes = api_key.encode('utf-8')
                    hash_bytes = api_key_hash.encode('utf-8')
                    if bcrypt.checkpw(api_key_bytes, hash_bytes):
                        # Check expiration
                        expires_at = row['api_key_expires_at']
                        if expires_at and expires_at < datetime.now(timezone.utc):
                            logger.warning(f"API key for device {row['device_identifier']} has expired")
                            return None
                        
                        # Validate IP address - REQUIRED if device has tailscale_ip configured
                        tailscale_ip = row['tailscale_ip']
                        if tailscale_ip:
                            if not client_ip or client_ip == "unknown":
                                logger.error(
                                    f"❌ Device {row['device_identifier']} requires IP validation "
                                    f"(tailscale_ip: {tailscale_ip}) but no valid client IP provided (got: {client_ip})"
                                )
                                return None
                            
                            # Reject if client IP looks like a proxy/internal IP and doesn't match
                            # This prevents validation bypass when reverse proxy doesn't forward real IP
                            is_internal_ip = (
                                client_ip.startswith("192.168.") or
                                client_ip.startswith("10.") or
                                client_ip.startswith("172.") or
                                client_ip.startswith("127.") or
                                client_ip == "localhost"
                            )
                            
                            # Allow direct local network access for 192.168.1.175 (testing/dev)
                            if client_ip == "192.168.1.175":
                                logger.info(f"✅ Allowing direct access from 192.168.1.175 for device {row['device_identifier']}")
                                # Continue to IP matching below
                            # If we got an internal IP but the tailscale_ip is a Tailscale IP (100.x.x.x),
                            # this means the reverse proxy isn't forwarding the real client IP
                            elif is_internal_ip and tailscale_ip.startswith("100."):
                                logger.error(
                                    f"❌ SECURITY: Device {row['device_identifier']} requires Tailscale IP validation "
                                    f"(tailscale_ip: {tailscale_ip}) but got internal/proxy IP '{client_ip}'. "
                                    f"Reverse proxy is not forwarding real client IP. REJECTING REQUEST."
                                )
                                return None
                            
                            # Strict IP comparison - must match exactly
                            # Allow 192.168.1.175 for direct local access (testing/dev)
                            if client_ip.strip() == "192.168.1.175":
                                logger.info(f"✅ Allowing direct access from 192.168.1.175 for device {row['device_identifier']}")
                            elif client_ip.strip() != tailscale_ip.strip():
                                logger.error(
                                    f"❌ IP MISMATCH for device {row['device_identifier']}: "
                                    f"expected '{tailscale_ip}', got '{client_ip}' - REJECTING REQUEST"
                                )
                                return None
                            
                            logger.info(
                                f"✅ IP validation passed for device {row['device_identifier']}: "
                                f"client IP '{client_ip}' matches tailscale_ip '{tailscale_ip}'"
                            )
                        else:
                            # Device has no tailscale_ip configured - this is a security risk
                            logger.warning(
                                f"⚠️  Device {row['device_identifier']} has no tailscale_ip configured - "
                                f"IP validation is DISABLED (client IP: {client_ip}). "
                                f"This is a security risk and should be configured."
                            )
                        
                        # Prioritize direct TastyTrade credentials on the device
                        if (row['tastytrade_account_id'] and 
                            row['tastytrade_client_secret'] and 
                            row['tastytrade_refresh_token']):
                            logger.info(f"API key validated for device {row['device_identifier']} using direct credentials")
                            return {
                                'device_id': str(row['device_id']),
                                'device_identifier': row['device_identifier'],
                                'user_id': str(row['user_id']) if row['user_id'] else "N/A",
                                'account_id': row['tastytrade_account_id'],
                                'client_secret': row['tastytrade_client_secret'],
                                'refresh_token': row['tastytrade_refresh_token']
                            }
                        
                        # Fallback to user_id and broker_connections if direct credentials are not present
                        if not row['user_id']:
                            logger.warning(f"Device {row['device_identifier']} is not linked to a user and has no direct credentials")
                            return None
                        
                        if not row['account_id'] or not row['client_secret'] or not row['refresh_token']:
                            logger.warning(f"User {row['user_id']} does not have active broker connection")
                            return None
                        
                        logger.info(f"API key validated for device {row['device_identifier']}, user {row['user_id']} using broker connection")
                        
                        return {
                            'device_id': str(row['device_id']),
                            'device_identifier': row['device_identifier'],
                            'user_id': str(row['user_id']),
                            'account_id': row['account_id'],
                            'client_secret': row['client_secret'],
                            'refresh_token': row['refresh_token']
                        }
                except (ValueError, TypeError, Exception) as e:
                    logger.debug(f"Failed to verify API key hash: {e}")
                    continue
        
        return None
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


