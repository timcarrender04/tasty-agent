++ b/backend_server/tasty-agent/scripts/update_device_ip.sql
-- SQL script to update device IP address
-- Usage: Run this in your Supabase/PostgreSQL database

-- Step 1: Find devices with API keys and their current IPs
SELECT 
    device_id,
    device_identifier,
    tailscale_ip,
    network_ip,
    api_key_prefix,
    created_at,
    updated_at
FROM gamma_devices
WHERE api_key_hash IS NOT NULL
ORDER BY updated_at DESC;

-- Step 2: Verify the device IP is set correctly (should be 100.77.64.79)
-- The device's tailscale_ip should be 100.77.64.79
SELECT device_id, tailscale_ip 
FROM gamma_devices 
WHERE tailscale_ip = '100.77.64.79';

-- Step 3: Only update if the device IP actually changed from 100.77.64.79
-- Replace 'DEVICE_IDENTIFIER' with actual value from Step 1
-- Replace 'NEW_TAILSCALE_IP' only if the device IP actually changed
-- UPDATE gamma_devices 
-- SET tailscale_ip = 'NEW_TAILSCALE_IP',
--     updated_at = NOW()
-- WHERE device_id = 'DEVICE_IDENTIFIER';

-- Step 3: Verify the update
SELECT 
    device_id,
    device_identifier,
    tailscale_ip,
    updated_at
FROM gamma_devices
WHERE device_id = 'DEVICE_IDENTIFIER';

-- Alternative: Update by database UUID (if you know the internal ID)
-- UPDATE gamma_devices 
-- SET tailscale_ip = '150.221.204.134',
--     updated_at = NOW()
-- WHERE id = 'YOUR_UUID_HERE';


