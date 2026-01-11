"""Session management utilities for TastyTrade."""
import os
from typing import Optional

from tastytrade import Account, Session


def is_sandbox_mode() -> bool:
    """
    Determine if we're in sandbox/paper trading mode.
    Checks environment variables TASTYTRADE_PAPER_MODE and TASTYTRADE_SANDBOX.
    
    Returns:
        True if in sandbox/paper mode, False otherwise
    """
    paper_mode = os.getenv("TASTYTRADE_PAPER_MODE", "false").lower() in ("true", "1", "yes")
    sandbox_mode = os.getenv("TASTYTRADE_SANDBOX", "false").lower() in ("true", "1", "yes")
    return paper_mode or sandbox_mode


def create_session(
    client_secret: str,
    refresh_token: str,
    is_test: Optional[bool] = None
) -> Session:
    """
    Create a TastyTrade session.
    
    Args:
        client_secret: TastyTrade OAuth client secret
        refresh_token: TastyTrade OAuth refresh token
        is_test: If True, use sandbox mode. If None, auto-detect from environment.
                 If False, force live mode.
    
    Returns:
        Authenticated TastyTrade Session object
    """
    if is_test is None:
        is_test = is_sandbox_mode()
    
    return Session(client_secret, refresh_token, is_test=is_test)


def select_account(
    accounts: list[Account],
    account_id: Optional[str] = None
) -> Account:
    """
    Select an account from a list of accounts.
    
    Args:
        accounts: List of Account objects
        account_id: Optional account number to select. If None, returns first account.
    
    Returns:
        Selected Account object
    
    Raises:
        ValueError: If account_id is specified but not found in accounts list
    """
    if not accounts:
        raise ValueError("No accounts available")
    
    if account_id:
        account = next((acc for acc in accounts if acc.account_number == account_id), None)
        if not account:
            available_accounts = [acc.account_number for acc in accounts]
            raise ValueError(
                f"Account '{account_id}' not found. Available accounts: {available_accounts}"
            )
        return account
    
    return accounts[0]

