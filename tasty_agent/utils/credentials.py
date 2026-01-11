"""Credential handling utilities."""
from typing import Any


def unpack_credentials(creds: dict[str, Any] | tuple[str, str] | list[str]) -> tuple[str, str]:
    """
    Unpack credentials from various formats (dict, tuple, or list).
    
    Args:
        creds: Credentials in one of these formats:
            - dict with 'client_secret' and 'refresh_token' keys
            - tuple/list with (client_secret, refresh_token) as first two elements
    
    Returns:
        Tuple of (client_secret, refresh_token)
    
    Raises:
        ValueError: If credentials cannot be unpacked or are missing required fields
        TypeError: If credentials are in an unexpected format
    """
    if isinstance(creds, dict):
        client_secret = creds.get('client_secret')
        refresh_token = creds.get('refresh_token')
        if not client_secret or not refresh_token:
            raise ValueError(
                "Missing client_secret or refresh_token in credentials dict"
            )
        return (client_secret, refresh_token)
    
    elif isinstance(creds, (tuple, list)) and len(creds) >= 2:
        client_secret = creds[0]
        refresh_token = creds[1]
        if not client_secret or not refresh_token:
            raise ValueError(
                "Missing client_secret or refresh_token in credentials tuple/list"
            )
        return (client_secret, refresh_token)
    
    else:
        # Try to unpack directly (may work for tuple[str, str])
        try:
            if len(creds) != 2:
                raise ValueError(f"Expected 2 credentials, got {len(creds)}")
            client_secret, refresh_token = creds
            if not client_secret or not refresh_token:
                raise ValueError("client_secret or refresh_token is empty")
            return (client_secret, refresh_token)
        except (ValueError, TypeError) as e:
            raise TypeError(
                f"Unexpected credential format: {type(creds)}. "
                f"Expected dict, tuple, or list. Error: {e}"
            ) from e

