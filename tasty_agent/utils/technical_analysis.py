"""Technical analysis functions: VWAP, support/resistance, breakout detection."""
import logging
from typing import Literal

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    pd = None

logger = logging.getLogger(__name__)


def calculate_vwap(candles) -> float:
    """
    Calculate VWAP (Volume Weighted Average Price) from candlestick data.
    
    VWAP = Σ(Price × Volume) / Σ(Volume)
    Price = (High + Low + Close) / 3 for each candle
    
    Args:
        candles: DataFrame with columns ['OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']
                 or ['Open', 'High', 'Low', 'Close', 'Volume']
    
    Returns:
        VWAP value as float
    """
    # Handle both DataFrame and dict formats
    if HAS_PANDAS and isinstance(candles, pd.DataFrame):
        candles_df = candles.copy()
        candles_df.columns = [col.upper() for col in candles_df.columns]
    elif isinstance(candles, dict):
        # Convert dict to lists for calculation
        candles_df = candles
    else:
        raise ValueError(f"Unsupported candles format: {type(candles)}")
    
    # Ensure required columns exist
    required_cols = ['HIGH', 'LOW', 'CLOSE', 'VOLUME']
    
    # Get data as lists
    if HAS_PANDAS and isinstance(candles_df, pd.DataFrame):
        for col in required_cols:
            if col not in candles_df.columns:
                raise ValueError(f"Missing required column for VWAP calculation: {col}")
        highs = candles_df['HIGH'].tolist()
        lows = candles_df['LOW'].tolist()
        closes = candles_df['CLOSE'].tolist()
        volumes = candles_df['VOLUME'].tolist()
    else:
        # Dict format
        for col in required_cols:
            if col not in candles_df:
                raise ValueError(f"Missing required column for VWAP calculation: {col}")
        highs = candles_df['HIGH'] if isinstance(candles_df['HIGH'], list) else [candles_df['HIGH']]
        lows = candles_df['LOW'] if isinstance(candles_df['LOW'], list) else [candles_df['LOW']]
        closes = candles_df['CLOSE'] if isinstance(candles_df['CLOSE'], list) else [candles_df['CLOSE']]
        volumes = candles_df['VOLUME'] if isinstance(candles_df['VOLUME'], list) else [candles_df['VOLUME']]
    
    # Calculate typical price and price × volume for each candle
    total_price_volume = 0.0
    total_volume = 0.0
    
    for high, low, close, volume in zip(highs, lows, closes, volumes):
        typical_price = (float(high) + float(low) + float(close)) / 3.0
        total_price_volume += typical_price * float(volume)
        total_volume += float(volume)
    
    if total_volume == 0:
        logger.warning("Total volume is zero, cannot calculate VWAP. Using average of closes.")
        avg_close = sum(float(c) for c in closes) / len(closes) if closes else 0.0
        return float(avg_close)
    
    vwap = total_price_volume / total_volume
    return float(vwap)


def calculate_support_resistance(candles) -> tuple[float, float]:
    """
    Calculate support and resistance levels from candlestick data.
    
    Support = Minimum Low from candles
    Resistance = Maximum High from candles
    
    Args:
        candles: DataFrame or dict with HIGH and LOW columns/keys
    
    Returns:
        Tuple of (support, resistance) as floats
    """
    # Handle both DataFrame and dict formats
    if HAS_PANDAS and isinstance(candles, pd.DataFrame):
        candles_df = candles.copy()
        candles_df.columns = [col.upper() for col in candles_df.columns]
        if 'LOW' not in candles_df.columns or 'HIGH' not in candles_df.columns:
            raise ValueError("Missing required columns for S/R calculation: HIGH and LOW")
        support = float(candles_df['LOW'].min())
        resistance = float(candles_df['HIGH'].max())
    elif isinstance(candles, dict):
        if 'LOW' not in candles or 'HIGH' not in candles:
            raise ValueError("Missing required keys for S/R calculation: HIGH and LOW")
        lows = candles['LOW'] if isinstance(candles['LOW'], list) else [candles['LOW']]
        highs = candles['HIGH'] if isinstance(candles['HIGH'], list) else [candles['HIGH']]
        support = float(min(float(l) for l in lows))
        resistance = float(max(float(h) for h in highs))
    else:
        raise ValueError(f"Unsupported candles format: {type(candles)}")
    
    return (support, resistance)


def detect_breakout(
    current_price: float,
    support: float,
    resistance: float,
    position_direction: Literal['long', 'short']
) -> bool:
    """
    Detect if price has broken above resistance (long) or below support (short).
    
    Args:
        current_price: Current market price
        support: Support level (minimum low)
        resistance: Resistance level (maximum high)
        position_direction: 'long' or 'short'
    
    Returns:
        True if breakout detected, False otherwise
    """
    if position_direction.lower() == 'long':
        # Long position: breakout = price above resistance
        return current_price > resistance
    elif position_direction.lower() == 'short':
        # Short position: breakout = price below support
        return current_price < support
    else:
        raise ValueError(f"Invalid position_direction: {position_direction}. Must be 'long' or 'short'.")


def calculate_vwap_stop_loss(
    vwap: float,
    current_price: float,
    position_direction: Literal['long', 'short'],
    trailing_percent: float = 0.02
) -> float:
    """
    Calculate stop-loss price based on VWAP and trailing percentage.
    
    For long positions: stop_loss = min(VWAP, current_price) * (1 - trailing_percent)
    For short positions: stop_loss = max(VWAP, current_price) * (1 + trailing_percent)
    
    Args:
        vwap: Current VWAP value
        current_price: Current market price
        position_direction: 'long' or 'short'
        trailing_percent: Trailing percentage (default: 0.02 = 2%)
    
    Returns:
        Stop-loss price
    """
    if position_direction.lower() == 'long':
        # For longs: stop below VWAP or current price (whichever is lower)
        base_price = min(vwap, current_price)
        stop_loss = base_price * (1 - trailing_percent)
        return stop_loss
    elif position_direction.lower() == 'short':
        # For shorts: stop above VWAP or current price (whichever is higher)
        base_price = max(vwap, current_price)
        stop_loss = base_price * (1 + trailing_percent)
        return stop_loss
    else:
        raise ValueError(f"Invalid position_direction: {position_direction}. Must be 'long' or 'short'.")


def get_position_direction(action: str) -> Literal['long', 'short']:
    """
    Determine position direction from order action.
    
    Args:
        action: Order action string (e.g., 'Buy to Open', 'Sell to Open', 'Buy', 'Sell')
    
    Returns:
        'long' or 'short'
    """
    action_lower = action.lower()
    
    # Long positions
    if 'buy to open' in action_lower or ('buy' in action_lower and 'sell' not in action_lower):
        return 'long'
    
    # Short positions
    if 'sell to open' in action_lower or ('sell' in action_lower and 'buy' not in action_lower):
        return 'short'
    
    # Default to long if unclear
    logger.warning(f"Could not determine position direction from action '{action}', defaulting to 'long'")
    return 'long'
