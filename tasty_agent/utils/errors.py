"""Error handling utilities for TastyTrade."""
from fastapi import HTTPException


def handle_tastytrade_auth_error(
    e: Exception,
    api_key: str,
    detail_suffix: str = ""
) -> HTTPException:
    """
    Handle TastyTrade authentication errors and return appropriate HTTPException.
    
    Args:
        e: The exception that occurred
        api_key: API key identifier (for logging/messages)
        detail_suffix: Optional additional detail to append to error message
    
    Returns:
        HTTPException with appropriate status code and error message
    """
    error_msg = str(e)
    api_key_preview = api_key[:8] + "..." if len(api_key) > 8 else api_key
    
    # Check for invalid/revoked token errors
    if "invalid_grant" in error_msg.lower() or "grant revoked" in error_msg.lower():
        base_detail = (
            f"TastyTrade refresh token is invalid or revoked for API key {api_key_preview}."
        )
        detail = f"{base_detail} {detail_suffix}".strip() if detail_suffix else base_detail
        detail += f" Error: {error_msg}"
        
        return HTTPException(
            status_code=401,
            detail=detail
        )
    
    # Generic authentication error
    detail = f"Failed to authenticate with TastyTrade for API key {api_key_preview}: {error_msg}"
    if detail_suffix:
        detail += f" {detail_suffix}"
    
    return HTTPException(
        status_code=500,
        detail=detail
    )

