#!/usr/bin/env python3
"""Wrapper script to run MCP server in stdio mode."""
import asyncio
from tasty_agent.server import mcp_app

if __name__ == "__main__":
    asyncio.run(mcp_app.run_stdio_async())



