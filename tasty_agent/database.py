"""
Database module for SQLite credential storage.
Handles database initialization, CRUD operations, and migration from JSON.
"""
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from tasty_agent.logging_config import get_database_logger

logger = get_database_logger()


class CredentialsDB:
    """SQLite database manager for credentials storage."""
    
    def __init__(self, db_path: Path):
        """Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn
    
    def _init_database(self) -> None:
        """Initialize database schema if it doesn't exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    api_key TEXT PRIMARY KEY,
                    client_secret TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")
    
    def get_credentials(self, api_key: str) -> Optional[tuple[str, str]]:
        """Get credentials for an API key.
        
        Args:
            api_key: API key identifier
            
        Returns:
            Tuple of (client_secret, refresh_token) or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT client_secret, refresh_token FROM credentials WHERE api_key = ?",
                (api_key,)
            )
            row = cursor.fetchone()
            if row:
                return (row["client_secret"], row["refresh_token"])
            return None
    
    def get_all_credentials(self) -> dict[str, tuple[str, str]]:
        """Get all credentials from database.
        
        Returns:
            Dictionary mapping api_key to (client_secret, refresh_token) tuple
        """
        credentials: dict[str, tuple[str, str]] = {}
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT api_key, client_secret, refresh_token FROM credentials"
            )
            for row in cursor:
                credentials[row["api_key"]] = (row["client_secret"], row["refresh_token"])
        return credentials
    
    def insert_or_update_credentials(
        self, api_key: str, client_secret: str, refresh_token: str
    ) -> None:
        """Insert or update credentials for an API key.
        
        Args:
            api_key: API key identifier
            client_secret: TastyTrade client secret
            refresh_token: TastyTrade refresh token
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO credentials (api_key, client_secret, refresh_token, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(api_key) DO UPDATE SET
                    client_secret = excluded.client_secret,
                    refresh_token = excluded.refresh_token,
                    updated_at = CURRENT_TIMESTAMP
            """, (api_key, client_secret, refresh_token))
            conn.commit()
            logger.info(f"Inserted/updated credentials for API key {api_key[:8]}...")
    
    def delete_credentials(self, api_key: str) -> bool:
        """Delete credentials for an API key.
        
        Args:
            api_key: API key identifier
            
        Returns:
            True if deleted, False if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM credentials WHERE api_key = ?",
                (api_key,)
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted credentials for API key {api_key[:8]}...")
            return deleted
    
    def list_api_keys(self) -> list[str]:
        """List all API keys in the database.
        
        Returns:
            List of API key strings
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT api_key FROM credentials ORDER BY api_key")
            return [row["api_key"] for row in cursor]
    
    def is_empty(self) -> bool:
        """Check if database is empty.
        
        Returns:
            True if no credentials exist, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM credentials")
            count = cursor.fetchone()["count"]
            return count == 0
    
    def migrate_from_json(self, json_path: Path) -> int:
        """Migrate credentials from JSON file to database.
        
        Args:
            json_path: Path to credentials.json file
            
        Returns:
            Number of credentials migrated
        """
        if not json_path.exists():
            logger.warning(f"JSON file not found at {json_path}, skipping migration")
            return 0
        
        try:
            with open(json_path, "r") as f:
                creds_dict = json.load(f)
            
            migrated_count = 0
            for api_key, cred_data in creds_dict.items():
                if isinstance(cred_data, dict):
                    client_secret = cred_data.get("client_secret")
                    refresh_token = cred_data.get("refresh_token")
                    if client_secret and refresh_token:
                        self.insert_or_update_credentials(api_key, client_secret, refresh_token)
                        migrated_count += 1
                    else:
                        logger.warning(f"Missing client_secret or refresh_token for API key: {api_key}")
                else:
                    logger.warning(f"Invalid credential format for API key: {api_key}")
            
            logger.info(f"Migrated {migrated_count} credential(s) from {json_path} to database")
            return migrated_count
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON file {json_path}: {e}")
            return 0
        except Exception as e:
            logger.error(f"Error migrating from JSON file {json_path}: {e}")
            return 0


def get_db_path(project_root: Path) -> Path:
    """Get the path to the credentials database.
    
    Args:
        project_root: Root directory of the project
        
    Returns:
        Path to credentials.db file
    """
    return project_root / "credentials.db"


