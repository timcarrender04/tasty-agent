"""Position tracking service with automatic stop-loss management."""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tastytrade import Account, Session
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

from tasty_agent.utils.technical_analysis import (
    calculate_support_resistance,
    calculate_vwap,
    calculate_vwap_stop_loss,
    detect_breakout,
    get_position_direction,
)
from tasty_agent.utils.thetadata_client import ThetaDataClient, get_thetadata_client
from tasty_agent.utils.session import is_sandbox_mode

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    """Data class for tracking a position."""
    
    order_id: int
    entry_price: float
    legs: list[Any]  # OrderLeg objects
    quantity: int
    api_key: str
    entry_time: datetime
    symbol: str  # Primary symbol for tracking (from first leg)
    position_direction: str  # 'long' or 'short'
    current_stop_order_id: int | None = None
    current_stop_price: float | None = None
    last_vwap: float | None = None
    last_support: float | None = None
    last_resistance: float | None = None
    breakout_detected: bool = False
    _monitor_task: asyncio.Task | None = field(default=None, init=False, repr=False)


class PositionTracker:
    """Manages background tracking tasks for positions."""
    
    def __init__(self):
        self._tracked_positions: dict[str, TrackedPosition] = {}  # order_id -> position
        self._thetadata_client: ThetaDataClient | None = None
    
    async def _get_thetadata_client(self) -> ThetaDataClient:
        """Get or create ThetaData client."""
        if self._thetadata_client is None:
            try:
                self._thetadata_client = get_thetadata_client()
            except Exception as e:
                logger.warning(f"Could not initialize ThetaData client: {e}")
                raise
        return self._thetadata_client
    
    async def start_tracking(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account
    ):
        """
        Start tracking a position in the background.
        
        Args:
            position: TrackedPosition to monitor
            session: TastyTrade session
            account: TastyTrade account
        """
        position_key = str(position.order_id)
        
        if position_key in self._tracked_positions:
            logger.warning(f"Position {position.order_id} is already being tracked")
            return
        
        self._tracked_positions[position_key] = position
        
        # Start background monitoring task
        task = asyncio.create_task(
            self._monitor_position(position, session, account)
        )
        position._monitor_task = task
        
        paper_mode = is_sandbox_mode()
        mode_str = "📝 PAPER" if paper_mode else "💰 LIVE"
        logger.info(
            f"Started tracking position {position.order_id} for {position.symbol} "
            f"({position.position_direction}, entry: ${position.entry_price:.2f}) [{mode_str} MODE]"
        )
    
    async def _monitor_position(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account
    ):
        """
        Background task to monitor position and update stop-loss.
        
        Works for both paper and live trading - the session/account determines
        which mode orders are placed in. Position tracking automatically uses
        the same mode as the original order.
        """
        polling_interval = 10  # Poll every 10 seconds
        paper_mode = is_sandbox_mode()
        mode_str = "📝 PAPER" if paper_mode else "💰 LIVE"
        
        logger.debug(f"Monitoring position {position.order_id} in {mode_str} mode")
        
        try:
            while True:
                await asyncio.sleep(polling_interval)
                
                # Check if position still exists
                if not await self._position_exists(session, account, position):
                    logger.info(f"Position {position.order_id} no longer exists, stopping tracking")
                    break
                
                # Get current price and candles from ThetaData
                try:
                    td_client = await self._get_thetadata_client()
                    current_price = await td_client.get_current_price(position.symbol)
                    candles = await td_client.get_1min_candles(position.symbol, count=10)
                except Exception as e:
                    logger.warning(
                        f"Error fetching ThetaData for {position.symbol}: {e}. "
                        "Falling back to TastyTrade position API."
                    )
                    # Fallback: try to get price from TastyTrade positions
                    try:
                        positions = await account.a_get_positions(session, include_marks=True)
                        for pos in positions:
                            if pos.symbol == position.symbol and pos.quantity != 0:
                                if hasattr(pos, 'mark_price') and pos.mark_price:
                                    current_price = float(pos.mark_price)
                                    # For candles, create minimal dataframe from current price
                                    # This is a fallback - won't have proper OHLCV but will work for basic VWAP
                                    import pandas as pd
                                    candles = pd.DataFrame({
                                        'OPEN': [current_price] * 10,
                                        'HIGH': [current_price] * 10,
                                        'LOW': [current_price] * 10,
                                        'CLOSE': [current_price] * 10,
                                        'VOLUME': [1000] * 10  # Dummy volume
                                    })
                                    break
                        else:
                            logger.warning(f"Could not get price for {position.symbol} from TastyTrade either")
                            continue
                    except Exception as fallback_error:
                        logger.error(f"Fallback to TastyTrade also failed: {fallback_error}")
                        continue
                
                # Calculate VWAP, support, resistance
                vwap = calculate_vwap(candles)
                support, resistance = calculate_support_resistance(candles)
                
                position.last_vwap = vwap
                position.last_support = support
                position.last_resistance = resistance
                
                # Calculate ITM status
                is_itm = await self._calculate_itm_status(position, current_price)
                
                # Detect breakout
                if not position.breakout_detected:
                    position.breakout_detected = detect_breakout(
                        current_price, support, resistance, position.position_direction
                    )
                    if position.breakout_detected:
                        logger.info(
                            f"Breakout detected for position {position.order_id} "
                            f"({position.symbol}): price {current_price:.2f} "
                            f"{'above' if position.position_direction == 'long' else 'below'} "
                            f"{'resistance' if position.position_direction == 'long' else 'support'} "
                            f"{resistance if position.position_direction == 'long' else support:.2f}"
                        )
                
                # Update stop-loss strategy
                await self._update_stop_loss_strategy(
                    position, session, account, current_price, vwap, support, resistance, is_itm
                )
        
        except asyncio.CancelledError:
            logger.info(f"Monitoring task cancelled for position {position.order_id}")
        except Exception as e:
            logger.error(f"Error monitoring position {position.order_id}: {e}", exc_info=True)
        finally:
            # Cleanup
            position_key = str(position.order_id)
            if position_key in self._tracked_positions:
                del self._tracked_positions[position_key]
    
    async def _position_exists(
        self,
        session: Session,
        account: Account,
        position: TrackedPosition
    ) -> bool:
        """Check if position still exists in account."""
        try:
            positions = await account.a_get_positions(session, include_marks=True)
            
            # Check if any position matches our tracked position
            for pos in positions:
                if pos.symbol == position.symbol and pos.quantity != 0:
                    return True
            
            return False
        except Exception as e:
            logger.warning(f"Error checking position existence: {e}")
            return True  # Assume exists on error to continue monitoring
    
    async def _calculate_itm_status(
        self,
        position: TrackedPosition,
        current_price: float
    ) -> bool:
        """
        Calculate if position is ITM (In The Money).
        
        ITM = current value > entry cost for long positions
        ITM = current value < entry cost for short positions
        """
        current_value = current_price * position.quantity
        entry_cost = position.entry_price * position.quantity
        
        if position.position_direction == 'long':
            return current_value > entry_cost
        else:  # short
            return current_value < entry_cost
    
    async def _update_stop_loss_strategy(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account,
        current_price: float,
        vwap: float,
        support: float,
        resistance: float,
        is_itm: bool
    ):
        """Update stop-loss based on ITM status and contract count."""
        try:
            if not is_itm:
                # Not ITM: Place VWAP-based stop-loss
                await self._place_vwap_stop_loss(
                    position, session, account, vwap, current_price
                )
            elif position.quantity == 1:
                # ITM + 1 contract: 2% trailing stop from VWAP
                await self._update_trailing_stop(
                    position, session, account, vwap, current_price
                )
            else:
                # ITM + 2+ contracts: Support/Resistance strategy
                await self._apply_resistance_strategy(
                    position, session, account, current_price, vwap, support, resistance
                )
        except Exception as e:
            logger.error(f"Error updating stop-loss for position {position.order_id}: {e}", exc_info=True)
    
    async def _place_vwap_stop_loss(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account,
        vwap: float,
        current_price: float
    ):
        """Place VWAP-based stop-loss for non-ITM positions."""
        stop_price = calculate_vwap_stop_loss(
            vwap, current_price, position.position_direction, trailing_percent=0.02
        )
        
        # Only update if stop price has changed significantly (avoid excessive API calls)
        if position.current_stop_price is not None:
            price_diff = abs(stop_price - position.current_stop_price)
            if price_diff < 0.05:  # Less than 5 cents change
                return
        
        await self._update_or_place_stop_order(
            position, session, account, stop_price, "VWAP-based stop-loss"
        )
    
    async def _update_trailing_stop(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account,
        vwap: float,
        current_price: float
    ):
        """Update 2% trailing stop from VWAP for single contract ITM positions."""
        stop_price = calculate_vwap_stop_loss(
            vwap, current_price, position.position_direction, trailing_percent=0.02
        )
        
        # Only update if stop price has moved favorably (trailing up for longs, down for shorts)
        if position.current_stop_price is not None:
            if position.position_direction == 'long':
                # For longs: only move stop up, not down
                if stop_price <= position.current_stop_price:
                    return
            else:  # short
                # For shorts: only move stop down, not up
                if stop_price >= position.current_stop_price:
                    return
        
        # Only update if change is significant (avoid excessive API calls)
        if position.current_stop_price is not None:
            price_diff = abs(stop_price - position.current_stop_price)
            if price_diff < 0.01:  # Less than 1 cent change
                return
        
        await self._update_or_place_stop_order(
            position, session, account, stop_price, "2% trailing stop from VWAP"
        )
    
    async def _apply_resistance_strategy(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account,
        current_price: float,
        vwap: float,
        support: float,
        resistance: float
    ):
        """Apply support/resistance strategy for 2+ contract positions."""
        if position.breakout_detected:
            # Breakout: Keep holding, adjust stop to VWAP or breakeven
            breakeven = position.entry_price
            stop_price = min(vwap, breakeven) if position.position_direction == 'long' else max(vwap, breakeven)
            
            await self._update_or_place_stop_order(
                position, session, account, stop_price, "Breakout: stop at VWAP/breakeven"
            )
        else:
            # No breakout: Set stop at VWAP or below support
            if position.position_direction == 'long':
                stop_price = min(vwap, support * 0.99)  # Slightly below support
            else:  # short
                stop_price = max(vwap, resistance * 1.01)  # Slightly above resistance
            
            await self._update_or_place_stop_order(
                position, session, account, stop_price, "No breakout: stop at VWAP/support"
            )
    
    async def _update_or_place_stop_order(
        self,
        position: TrackedPosition,
        session: Session,
        account: Account,
        stop_price: float,
        reason: str
    ):
        """Update existing stop order or place new one."""
        try:
            # Determine stop action based on position direction
            first_leg = position.legs[0]
            action_lower = first_leg.action.lower()
            
            if position.position_direction == 'long':
                # Long position: stop-loss is a sell order
                stop_action = 'Sell to Close' if 'option' in str(type(first_leg)).lower() else 'Sell'
            else:
                # Short position: stop-loss is a buy order
                stop_action = 'Buy to Close' if 'option' in str(type(first_leg)).lower() else 'Buy'
            
            # Build stop order leg - need to reconstruct from stored leg data
            # The legs stored in TrackedPosition should be the original OrderLeg BaseModel objects
            first_leg_data = position.legs[0]
            
            # Import here to avoid circular dependency
            from tasty_agent.http_server import InstrumentSpec, get_instrument_details
            
            # Convert leg to InstrumentSpec and build order
            instrument_spec = InstrumentSpec(
                symbol=first_leg_data.symbol,
                option_type=getattr(first_leg_data, 'option_type', None),
                strike_price=getattr(first_leg_data, 'strike_price', None),
                expiration_date=getattr(first_leg_data, 'expiration_date', None)
            )
            
            instrument_details = await get_instrument_details(session, [instrument_spec])
            if not instrument_details:
                logger.error(f"Could not get instrument details for {first_leg_data.symbol}")
                return
            
            instrument = instrument_details[0].instrument
            order_action = (
                OrderAction(stop_action) if isinstance(instrument, type) and hasattr(OrderAction, stop_action.replace(' ', '_').upper())
                else (OrderAction.BUY if 'buy' in stop_action.lower() else OrderAction.SELL)
            )
            
            # Determine proper OrderAction based on instrument type and stop action
            from tastytrade.instruments import Option
            
            if isinstance(instrument, Option):
                if 'buy to close' in stop_action.lower():
                    order_action = OrderAction.BUY_TO_CLOSE
                elif 'sell to close' in stop_action.lower():
                    order_action = OrderAction.SELL_TO_CLOSE
                elif 'buy to open' in stop_action.lower():
                    order_action = OrderAction.BUY_TO_OPEN
                elif 'sell to open' in stop_action.lower():
                    order_action = OrderAction.SELL_TO_OPEN
                else:
                    # Default based on position direction
                    order_action = OrderAction.BUY_TO_CLOSE if position.position_direction == 'short' else OrderAction.SELL_TO_CLOSE
            else:
                # Equity
                order_action = OrderAction.BUY if 'buy' in stop_action.lower() else OrderAction.SELL
            
            stop_leg = instrument.build_leg(Decimal(str(position.quantity)), order_action)
            
            # Build stop order
            stop_order = NewOrder(
                order_type=OrderType.STOP,
                time_in_force=OrderTimeInForce.DAY,
                legs=[stop_leg],
                stop_price=Decimal(str(stop_price))
            )
            
            if position.current_stop_order_id:
                # Try to replace existing order
                try:
                    from tasty_agent.http_server import build_new_order
                    # For replace, we'd need the existing order structure
                    # For simplicity, delete and recreate
                    await account.a_delete_order(session, position.current_stop_order_id)
                    position.current_stop_order_id = None
                except Exception as e:
                    logger.warning(f"Could not delete old stop order: {e}")
            
            # Place new stop order (respect sandbox mode - TastyTrade handles this via session)
            paper_mode = is_sandbox_mode()
            # In paper mode, orders go to sandbox. In live mode, real orders.
            # dry_run=False means actually place the order (whether paper or live based on session)
            result = await account.a_place_order(session, stop_order, dry_run=False)
            result_dict = result.model_dump() if hasattr(result, 'model_dump') else {}
            
            if isinstance(result_dict, dict):
                new_order_id = result_dict.get('id') or result_dict.get('order_id')
                if new_order_id:
                    position.current_stop_order_id = int(new_order_id)
                    position.current_stop_price = stop_price
                    paper_mode = is_sandbox_mode()
                    mode_str = "📝 PAPER" if paper_mode else "💰 LIVE"
                    logger.info(
                        f"Updated stop-loss for position {position.order_id} ({position.symbol}): "
                        f"${stop_price:.2f} - {reason} [{mode_str} MODE]"
                    )
        except Exception as e:
            logger.error(f"Error updating stop order for position {position.order_id}: {e}", exc_info=True)


# Global position tracker instance
_position_tracker: PositionTracker | None = None


def get_position_tracker() -> PositionTracker:
    """Get or create global position tracker instance."""
    global _position_tracker
    if _position_tracker is None:
        _position_tracker = PositionTracker()
    return _position_tracker
