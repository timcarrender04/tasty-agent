"""ThetaData client for fetching market data (1-minute candles, real-time prices)."""
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

try:
    import pandas as pd
except ImportError:
    pd = None  # Will handle gracefully

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    try:
        from urllib.request import urlopen, Request
        from urllib.parse import urlencode
        URLLIB_AVAILABLE = True
    except ImportError:
        URLLIB_AVAILABLE = False

logger = logging.getLogger(__name__)


class ThetaDataClient:
    """Client for ThetaData REST API to fetch market data."""
    
    def __init__(self, url: str | None = None):
        """
        Initialize ThetaData client using REST API.
        
        Args:
            url: ThetaData server URL (from THETA_URL env if not provided)
        """
        if not REQUESTS_AVAILABLE and not URLLIB_AVAILABLE:
            raise ImportError(
                "Neither requests nor urllib available. Install requests with: pip install requests"
            )
        
        self.base_url = url or os.getenv("THETA_URL", "http://100.106.78.116:25503")
        # Remove trailing slash if present
        self.base_url = self.base_url.rstrip('/')
        
        logger.info(f"ThetaData REST API URL: {self.base_url}")
    
    def _make_request(self, endpoint: str, params: dict | None = None) -> dict | str:
        """
        Make a request to ThetaData REST API.
        
        Args:
            endpoint: API endpoint (e.g., '/v3/stock/history/trade')
            params: Query parameters
        
        Returns:
            JSON response as dict, or error message as string
        """
        url = f"{self.base_url}{endpoint}"
        
        # Build query string
        if params:
            query_string = urlencode(params)
            url = f"{url}?{query_string}" if '?' not in url else f"{url}&{query_string}"
        
        try:
            if REQUESTS_AVAILABLE:
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    error_text = response.text
                    logger.warning(f"ThetaData API returned status {response.status_code}: {error_text[:100]}")
                    return error_text
                try:
                    return response.json()
                except ValueError:
                    return response.text
            elif URLLIB_AVAILABLE:
                # Use urllib as fallback
                req = Request(url)
                with urlopen(req, timeout=10) as response:
                    content = response.read().decode('utf-8')
                    # Try to parse JSON
                    try:
                        import json
                        return json.loads(content)
                    except ValueError:
                        return content
            else:
                raise ImportError("No HTTP library available")
        except Exception as e:
            logger.error(f"ThetaData API request failed: {e}")
            raise
    
    async def get_current_price(self, symbol: str) -> float:
        """
        Get current real-time price for a symbol using ThetaData REST API v3.
        
        Args:
            symbol: Stock symbol (e.g., 'AAPL', 'SPY')
        
        Returns:
            Current price (last trade price)
        """
        try:
            # ThetaData API v3 format: /v3/stock/history/trade?symbol=SPY&date=YYYYMMDD
            today = datetime.now()
            date_str = today.strftime("%Y%m%d")  # Format: YYYYMMDD
            
            # Get trades from today (last few minutes)
            params = {
                "symbol": symbol,
                "date": date_str,
                "format": "json",
                "start_time": (today - timedelta(minutes=5)).strftime("%H:%M:%S"),
                "end_time": today.strftime("%H:%M:%S")
            }
            
            endpoint = "/v3/stock/history/trade"
            try:
                data = self._make_request(endpoint, params)
            except Exception:
                # Try without time limits to get latest
                params = {"symbol": symbol, "date": date_str, "format": "json"}
                data = self._make_request(endpoint, params)
            
            # Handle error response
            if isinstance(data, str) and ("No data" in data or "error" in data.lower()):
                # Try previous day if today has no data
                yesterday = today - timedelta(days=1)
                date_str_yesterday = yesterday.strftime("%Y%m%d")
                params_yesterday = {"symbol": symbol, "date": date_str_yesterday, "format": "json"}
                try:
                    data = self._make_request(endpoint, params_yesterday)
                except:
                    raise ValueError(f"No price data available for {symbol} (tried today and yesterday)")
            
            # Parse response - ThetaData v3 returns {"response": [array of trades]}
            trades_list = data.get("response", data) if isinstance(data, dict) else data
            if not isinstance(trades_list, list):
                trades_list = [trades_list] if trades_list else []
            
            if len(trades_list) > 0:
                # Get last trade (most recent)
                last_trade = trades_list[-1]
                if isinstance(last_trade, dict) and 'price' in last_trade:
                    return float(last_trade['price'])
                elif isinstance(last_trade, dict):
                    # Try alternative field names
                    for price_field in ['PRICE', 'Price', 'close', 'CLOSE']:
                        if price_field in last_trade:
                            return float(last_trade[price_field])
            
            raise ValueError(f"No price data in response for {symbol}")
            
        except Exception as e:
            logger.error(f"Error getting current price for {symbol}: {e}")
            raise
        """
        Get current real-time price for a symbol.
        
        Args:
            symbol: Stock symbol (e.g., 'AAPL', 'SPY')
        
        Returns:
            Current price (last trade price)
        """
        await self._ensure_connected()
        
        try:
            # Get the most recent trade/quote
            # ThetaData API for last trade
            end_date = datetime.now()
            start_date = end_date - timedelta(minutes=1)
            
            # Use ThetaData's get_hist_stock_v2 for recent data
            # Note: ThetaData API may vary, this is a common pattern
            try:
                data = self._client.get_hist_stock_v2(
                    req=StockReqType.TRADE,
                    root=symbol,
                    start=start_date,
                    end=end_date
                )
            except (AttributeError, TypeError) as e:
                # Try alternative API method signature
                logger.debug(f"Trying alternative ThetaData API call: {e}")
                try:
                    data = self._client.get_hist_stock(
                        req=StockReqType.TRADE,
                        root=symbol,
                        start=start_date,
                        end=end_date
                    )
                except Exception:
                    # Last resort: try without req parameter
                    data = self._client.get_hist_stock_v2(
                        root=symbol,
                        start=start_date,
                        end=end_date
                    )
            
            if data is not None and len(data) > 0:
                # Return the last price from the most recent trade
                # Handle different column name variations
                price_col = None
                for col in ['PRICE', 'Price', 'price', 'LAST', 'Last', 'last']:
                    if col in data.columns:
                        price_col = col
                        break
                
                if price_col:
                    return float(data.iloc[-1][price_col])
                else:
                    # Try first numeric column
                    numeric_cols = data.select_dtypes(include=['float64', 'int64']).columns
                    if len(numeric_cols) > 0:
                        return float(data.iloc[-1][numeric_cols[0]])
            else:
                # Fallback: try to get from quote data
                try:
                    quote_data = self._client.get_hist_stock_v2(
                        req=StockReqType.QUOTE,
                        root=symbol,
                        start=start_date,
                        end=end_date
                    )
                    if quote_data is not None and len(quote_data) > 0:
                        # Use mid price from quote
                        last_quote = quote_data.iloc[-1]
                        if 'BID' in last_quote.index and 'ASK' in last_quote.index:
                            return float((last_quote['BID'] + last_quote['ASK']) / 2)
                        elif 'Bid' in last_quote.index and 'Ask' in last_quote.index:
                            return float((last_quote['Bid'] + last_quote['Ask']) / 2)
                except Exception:
                    pass
                
                raise ValueError(f"No price data available for {symbol}")
        except Exception as e:
            logger.error(f"Error getting current price for {symbol}: {e}")
            raise
    
    async def get_1min_candles(self, symbol: str, count: int = 10) -> Any:
        """
        Get last N one-minute candlesticks (OHLCV data) using ThetaData REST API v3.
        
        Uses quote data with 1-minute interval to build OHLCV candles.
        
        Args:
            symbol: Stock symbol (e.g., 'AAPL', 'SPY')
            count: Number of 1-minute candles to fetch (default: 10)
        
        Returns:
            DataFrame with columns: ['OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']
        """
        try:
            today = datetime.now()
            date_str = today.strftime("%Y%m%d")  # Format: YYYYMMDD
            
            # Use trade data to build 1-minute candles (more reliable than quote endpoint)
            params = {
                "symbol": symbol,
                "date": date_str,
                "format": "json"
            }
            
            # Use trade data to build 1-minute candles
            endpoint = "/v3/stock/history/trade"
            response_data = self._make_request(endpoint, params)
            
            # Handle error response - try previous day if needed
            if isinstance(response_data, str) and ("No data" in response_data or "error" in response_data.lower()):
                yesterday = today - timedelta(days=1)
                date_str_yesterday = yesterday.strftime("%Y%m%d")
                params_yesterday = {"symbol": symbol, "date": date_str_yesterday, "format": "json"}
                try:
                    response_data = self._make_request(endpoint, params_yesterday)
                except:
                    raise ValueError(f"No trade data available for {symbol} (tried today and yesterday)")
            
            # Parse response - ThetaData v3 returns {"response": [array of trades]}
            trades_list = response_data.get("response", response_data) if isinstance(response_data, dict) else response_data
            if not isinstance(trades_list, list):
                trades_list = [trades_list] if trades_list else []
            
            if not trades_list or len(trades_list) == 0:
                raise ValueError(f"No trade data available for {symbol}")
            
            # Group trades by minute and build OHLCV candles
            from collections import defaultdict
            candles_by_minute = defaultdict(lambda: {'prices': [], 'volumes': []})
            
            for trade in trades_list:
                if not isinstance(trade, dict) or 'price' not in trade:
                    continue
                
                timestamp_str = trade.get('timestamp', '')
                if not timestamp_str:
                    continue
                
                # Parse timestamp and get minute
                try:
                    from datetime import datetime as dt
                    ts = dt.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    minute_key = ts.strftime("%Y-%m-%d %H:%M")  # Group by minute
                except:
                    continue
                
                price = float(trade['price'])
                size = float(trade.get('size', 0))
                candles_by_minute[minute_key]['prices'].append(price)
                candles_by_minute[minute_key]['volumes'].append(size)
            
            # Build OHLCV from grouped data
            candles_list = []
            for minute_key in sorted(candles_by_minute.keys())[-count:]:
                candle_data = candles_by_minute[minute_key]
                prices = candle_data['prices']
                volumes = candle_data['volumes']
                
                if prices:
                    candles_list.append({
                        'OPEN': prices[0],
                        'HIGH': max(prices),
                        'LOW': min(prices),
                        'CLOSE': prices[-1],
                        'VOLUME': sum(volumes)
                    })
            
            if not candles_list:
                raise ValueError(f"Could not build candles from trade data for {symbol}")
            
            # Convert to DataFrame if pandas available
            if pd is not None:
                df = pd.DataFrame(candles_list)
                df.columns = [col.upper() for col in df.columns]
                required_cols = ['OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']
                return df[required_cols].tail(count)
            else:
                # Return dict structure
                return {
                    'OPEN': [c['OPEN'] for c in candles_list[-count:]],
                    'HIGH': [c['HIGH'] for c in candles_list[-count:]],
                    'LOW': [c['LOW'] for c in candles_list[-count:]],
                    'CLOSE': [c['CLOSE'] for c in candles_list[-count:]],
                    'VOLUME': [c['VOLUME'] for c in candles_list[-count:]]
                }
            
        except Exception as e:
            logger.error(f"Error getting 1-minute candles for {symbol}: {e}")
            raise


# Singleton instance (lazy initialization)
_thetadata_client: ThetaDataClient | None = None


def get_thetadata_client(url: str | None = None) -> ThetaDataClient:
    """Get or create ThetaData client singleton."""
    global _thetadata_client
    if _thetadata_client is None:
        _thetadata_client = ThetaDataClient(url=url)
    return _thetadata_client
