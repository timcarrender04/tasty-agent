#!/usr/bin/env python3
"""
Helper script to find a device by API key and show its current IP configuration.
Useful for debugging IP mismatch issues.
"""
import os
import sys
import asyncpg
import bcrypt
from typing import Optional

async def find_device_by_api_key(api_key: str, database_url: Optional[str] = None):
    """Find device information by API key."""
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL environment variable is required")
        sys.exit(1)
    
    conn = await asyncpg.connect(database_url)
    
    try:
        # Get all devices with API keys
        rows = await conn.fetch("""
            SELECT 
                d.id as device_id,
                d.device_id as device_identifier,
                d.api_key_hash,
                d.api_key_expires_at,
                d.tailscale_ip,
                d.network_ip,
                d.user_id,
                d.created_at,
                d.updated_at
            FROM public.gamma_devices d
            WHERE d.api_key_hash IS NOT NULL
        """)
        
        print(f"Checking {len(rows)} devices with API keys...\n")
        
        matched_device = None
        for row in rows:
            api_key_hash = row['api_key_hash']
            if not api_key_hash:
                continue
            
            try:
                api_key_bytes = api_key.encode('utf-8')
                hash_bytes = api_key_hash.encode('utf-8')
                if bcrypt.checkpw(api_key_bytes, hash_bytes):
                    matched_device = row
                    break
            except Exception:
                continue
        
        if not matched_device:
            print("❌ No device found with the provided API key")
            return
        
        print("✅ Device found!\n")
        print(f"Device ID: {matched_device['device_identifier']}")
        print(f"Database ID: {matched_device['device_id']}")
        print(f"Current tailscale_ip: {matched_device['tailscale_ip'] or '(not set)'}")
        print(f"Network IP: {matched_device['network_ip']}")
        print(f"User ID: {matched_device['user_id'] or '(not set)'}")
        print(f"API Key Expires: {matched_device['api_key_expires_at'] or '(never)'}")
        print(f"Created: {matched_device['created_at']}")
        print(f"Updated: {matched_device['updated_at']}")
        
        print("\n" + "="*60)
        print("To update the device IP, run:")
        print("="*60)
        print(f"""
-- Option 1: Update via SQL (replace YOUR_DEVICE_IDENTIFIER with the Device ID above)
UPDATE gamma_devices 
SET tailscale_ip = '150.221.204.134',
    updated_at = NOW()
WHERE device_id = '{matched_device['device_identifier']}';

-- Option 2: Update specific device by database ID
UPDATE gamma_devices 
SET tailscale_ip = '150.221.204.134',
    updated_at = NOW()
WHERE id = '{matched_device['device_id']}';
        """)
        
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_device_by_api_key.py <api_key>")
        print("\nExample:")
        print("  python find_device_by_api_key.py 'your-api-key-here'")
        sys.exit(1)
    
    api_key = sys.argv[1]
    
    import asyncio
    asyncio.run(find_device_by_api_key(api_key))


