"""
Logging Configuration for TastyAgent Services
Sets up separate log files for each service in the tasty-agent flow
"""
import logging
import logging.handlers
import os
from pathlib import Path
from datetime import datetime
import pytz

# ET timezone for market hours
ET = pytz.timezone("America/New_York")

# Base logs directory (relative to project root)
LOGS_DIR = Path(__file__).parent.parent / "Logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def get_log_filename(service_name: str) -> Path:
    """
    Get log file path for a service, with date suffix
    
    Args:
        service_name: Name of the service (e.g., 'server', 'http_server')
        
    Returns:
        Path to the log file (e.g., Logs/server_2025-01-15.log)
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    return LOGS_DIR / f"{service_name}_{today}.log"


def setup_service_logger(
    service_name: str,
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 7  # Keep 7 days of logs
) -> logging.Logger:
    """
    Set up a logger for a specific service with file handler
    
    Args:
        service_name: Name of the service (used for logger name and log file)
        log_level: Logging level (default: INFO)
        max_bytes: Maximum size of log file before rotation (default: 10MB)
        backup_count: Number of backup log files to keep (default: 7)
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(service_name)
    logger.setLevel(log_level)
    
    # Check if file handler already exists (to avoid duplicates)
    has_file_handler = any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)
    if has_file_handler:
        return logger
    
    # Create formatter with timestamp, level, and message
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S %Z'
    )
    
    # Create rotating file handler
    log_file = get_log_filename(service_name)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(file_handler)
    
    # Also add console handler for immediate visibility
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.info(f"Logger initialized for {service_name} - Logging to {log_file}")
    
    return logger


# Service-specific logger setup functions
def get_server_logger() -> logging.Logger:
    """Get logger for MCP server"""
    return setup_service_logger("server")


def get_http_server_logger() -> logging.Logger:
    """Get logger for HTTP server"""
    return setup_service_logger("http_server")


def get_database_logger() -> logging.Logger:
    """Get logger for database operations"""
    return setup_service_logger("database")


# Initialize loggers for all tasty-agent services
def initialize_tasty_agent_loggers():
    """Initialize all loggers for tasty-agent flow"""
    services = [
        get_server_logger,
        get_http_server_logger,
        get_database_logger,
    ]
    
    for service_logger_func in services:
        try:
            service_logger_func()
        except Exception as e:
            print(f"Warning: Failed to initialize logger: {e}")


