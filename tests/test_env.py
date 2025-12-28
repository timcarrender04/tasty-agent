#!/usr/bin/env python3
"""Test script to validate .env file configuration."""

import os
import sys

# Try to load dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
    dotenv_available = True
except ImportError:
    dotenv_available = False
    print("⚠️  python-dotenv not available, reading .env file directly...")
    print()

# Read .env file directly to check lines 1-10
env_file_path = ".env"
if os.path.exists(env_file_path):
    print("=" * 60)
    print("Testing .env file configuration (lines 1-10)")
    print("=" * 60)
    print()
    
    with open(env_file_path, 'r') as f:
        lines = f.readlines()
    
    # Show first 10 lines
    print("First 10 lines of .env file:")
    print("-" * 60)
    for i, line in enumerate(lines[:10], 1):
        # Mask sensitive values
        if '=' in line:
            key, value = line.split('=', 1)
            value = value.strip()
            if any(sensitive in key.upper() for sensitive in ['SECRET', 'TOKEN', 'KEY', 'PASSWORD']):
                if len(value) > 8:
                    masked_value = value[:4] + "..." + value[-4:]
                else:
                    masked_value = "***"
                print(f"{i:2d}: {key}={masked_value}")
            else:
                print(f"{i:2d}: {key}={value}")
        else:
            print(f"{i:2d}: {line.rstrip()}")
    print("-" * 60)
    print()
    
    # Parse environment variables from .env file
    env_vars = {}
    for line in lines[:10]:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            env_vars[key.strip()] = value.strip()
    
    # Expected environment variables
    expected_vars = [
        "TASTYTRADE_CLIENT_SECRET",
        "TASTYTRADE_REFRESH_TOKEN",
        "TASTYTRADE_ACCOUNT_ID",
        "OPENAI_API_KEY",
        "MODEL_IDENTIFIER",
    ]
    
    print("Environment variable validation:")
    print("-" * 60)
    all_present = True
    for var in expected_vars:
        # Check in parsed env_vars first
        if var in env_vars:
            value = env_vars[var]
            if any(sensitive in var.upper() for sensitive in ['SECRET', 'TOKEN', 'KEY']):
                masked_value = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
                print(f"✅ {var}: {masked_value} (from .env file)")
            else:
                print(f"✅ {var}: {value} (from .env file)")
        elif dotenv_available and os.getenv(var):
            value = os.getenv(var)
            if any(sensitive in var.upper() for sensitive in ['SECRET', 'TOKEN', 'KEY']):
                masked_value = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
                print(f"✅ {var}: {masked_value} (from environment)")
            else:
                print(f"✅ {var}: {value} (from environment)")
        else:
            print(f"❌ {var}: NOT SET")
            all_present = False
    print("-" * 60)
    print()
    
    print("=" * 60)
    if all_present:
        print("✅ All required environment variables are set!")
    else:
        print("⚠️  Some environment variables are missing.")
    print("=" * 60)
    
    # Count total variables in first 10 lines
    print()
    print(f"Total variables found in first 10 lines: {len(env_vars)}")
    print(f"Total lines in .env file: {len(lines)}")
    
else:
    print("❌ .env file not found!")
    sys.exit(1)
