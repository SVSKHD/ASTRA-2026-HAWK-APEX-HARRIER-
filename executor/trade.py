# trade.py
from __future__ import annotations

"""
MT5 Trade Execution Module — FOK Orders with Retry

Public API (matches executor/runner.py stubs):
    place_market_order_fok(symbol, side, lot, comment) → dict
    close_all_positions_fok(symbol, comment)           → dict
    close_position_fok(ticket, comment)                → dict
    get_positions_snapshot(symbol)                     → dict
    get_realized_profit_since(symbol, since_dt)        → float

Simulation:
    calc_profit(symbol, side, lot, open_price, close_price) → float

Internal:
    _ensure_mt5()           — init with retry
    _ensure_symbol(symbol)  — select symbol in MarketWatch
    _retry(func, ...)       — retry wrapper for transient failures
"""

import time
import functools
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Literal
from dataclasses import dataclass, field

import MetaTrader5 as mt5

from config.symbols import SYMBOLS, SymbolConfig

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("trade")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(_h)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021

RETRIABLE_RETCODES = frozenset({
    TRADE_RETCODE_REQUOTE,
    TRADE_RETCODE_PRICE_CHANGED,
    TRADE_RETCODE_PRICE_OFF,
    10006,  # TRADE_RETCODE_REJECT (temporary)
    10007,  # TRADE_RETCODE_CANCEL
    10013,  # TRADE_RETCODE_INVALID_VOLUME (can be transient)
    10014,  # TRADE_RETCODE_INVALID_PRICE
    10015,  # TRADE_RETCODE_INVALID_STOPS
    10016,  # TRADE_RETCODE_TRADE_DISABLED (market closed, retry later)
    10018,  # TRADE_RETCODE_MARKET_CLOSED
    10024,  # TRADE_RETCODE_TOO_MANY_REQUESTS (rate limit)
    10031,  # TRADE_RETCODE_CONNECTION (connection issue)
})

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 0.3  # seconds
DEFAULT_DEVIATION = 20     # points slippage


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TradeResult:
    """Unified result for all trade operations."""
    success: bool
    retcode: int
    symbol: str = ""
    side: str = ""
    ticket: int = 0
    volume: float = 0.0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    comment: str = ""
    error: str = ""
    attempts: int = 1
    _stub: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "retcode": self.retcode,
            "symbol": self.symbol,
            "side": self.side,
            "ticket": self.ticket,
            "volume": self.volume,
            "price": self.price,
            "sl": self.sl,
            "tp": self.tp,
            "profit": self.profit,
            "comment": self.comment,
            "error": self.error,
            "attempts": self.attempts,
            "_stub": self._stub,
        }


# ---------------------------------------------------------------------------
# MT5 Connection Management
# ---------------------------------------------------------------------------

_mt5_initialized = False
_mt5_lock = None  # Could add threading.Lock() if needed

def _ensure_mt5(max_retries: int = 5, delay: float = 1.0) -> bool:
    """
    Initialize MT5 connection with retry.
    Safe to call multiple times — returns immediately if already connected.
    """
    global _mt5_initialized

    if _mt5_initialized and mt5.terminal_info() is not None:
        return True

    for attempt in range(1, max_retries + 1):
        if mt5.initialize():
            _mt5_initialized = True
            logger.info(f"MT5 initialized (attempt {attempt})")
            return True

        err = mt5.last_error()
        logger.warning(f"MT5 init attempt {attempt}/{max_retries} failed: {err}")

        if attempt < max_retries:
            time.sleep(delay)

    logger.error(f"MT5 init failed after {max_retries} attempts")
    return False


def _ensure_symbol(symbol: str) -> bool:
    """
    Ensure symbol is visible in MarketWatch for tick streaming.
    Returns False if symbol doesn't exist.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol {symbol} not found")
        return False

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Failed to select symbol {symbol}")
            return False

    return True


def _get_tick(symbol: str) -> Optional[Tuple[float, float, float]]:
    """
    Get current bid/ask/mid for symbol.
    Returns (bid, ask, mid) or None.
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.time == 0:
        return None

    bid = float(tick.bid)
    ask = float(tick.ask)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0

    return (bid, ask, mid)


def shutdown():
    """Shutdown MT5 connection."""
    global _mt5_initialized
    mt5.shutdown()
    _mt5_initialized = False
    logger.info("MT5 shutdown")


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def _retry(
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    delay: float = DEFAULT_RETRY_DELAY,
    retriable_codes: frozenset = RETRIABLE_RETCODES,
):
    """
    Decorator for retrying trade operations on transient failures.
    Retries if retcode is in retriable_codes.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> TradeResult:
            last_result = None

            for attempt in range(1, max_attempts + 1):
                result = func(*args, **kwargs)
                result.attempts = attempt

                if result.success:
                    return result

                last_result = result

                # Check if retriable
                if result.retcode not in retriable_codes:
                    logger.warning(
                        f"{func.__name__} failed (non-retriable): "
                        f"retcode={result.retcode} error={result.error}"
                    )
                    return result

                # Retry
                if attempt < max_attempts:
                    logger.info(
                        f"{func.__name__} retry {attempt}/{max_attempts}: "
                        f"retcode={result.retcode}"
                    )
                    time.sleep(delay)

            logger.error(
                f"{func.__name__} failed after {max_attempts} attempts: "
                f"retcode={last_result.retcode if last_result else 'N/A'}"
            )
            return last_result or TradeResult(
                success=False,
                retcode=-1,
                error="Max retries exceeded",
            )

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# FOK Order Execution
# ---------------------------------------------------------------------------

@_retry(max_attempts=DEFAULT_RETRY_ATTEMPTS, delay=DEFAULT_RETRY_DELAY)
def _execute_order_fok(
    symbol: str,
    side: Literal["buy", "sell"],
    lot: float,
    comment: str = "",
    deviation: int = DEFAULT_DEVIATION,
    sl: float = 0.0,
    tp: float = 0.0,
    magic: int = 0,
) -> TradeResult:
    """
    Internal FOK order execution with fresh price fetch.
    Called by place_market_order_fok after retry wrapper.
    """
    # Ensure MT5 connected
    if not _ensure_mt5():
        return TradeResult(
            success=False,
            retcode=-1,
            symbol=symbol,
            side=side,
            error="MT5 not initialized",
        )

    # Ensure symbol visible
    if not _ensure_symbol(symbol):
        return TradeResult(
            success=False,
            retcode=-1,
            symbol=symbol,
            side=side,
            error=f"Symbol {symbol} not available",
        )

    # Get fresh tick price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return TradeResult(
            success=False,
            retcode=-1,
            symbol=symbol,
            side=side,
            error="Failed to get tick data",
        )

    # Determine order type and price
    if side.lower() == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid

    if price <= 0:
        return TradeResult(
            success=False,
            retcode=-1,
            symbol=symbol,
            side=side,
            error=f"Invalid price: {price}",
        )

    # Build request
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot),
        "type": order_type,
        "price": price,
        "sl": float(sl),
        "tp": float(tp),
        "deviation": deviation,
        "magic": magic,
        "comment": comment[:31] if comment else "",  # MT5 limit
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    # Send order
    result = mt5.order_send(request)

    if result is None:
        err = mt5.last_error()
        return TradeResult(
            success=False,
            retcode=err[0] if err else -1,
            symbol=symbol,
            side=side,
            error=str(err) if err else "order_send returned None",
        )

    success = result.retcode == TRADE_RETCODE_DONE

    return TradeResult(
        success=success,
        retcode=result.retcode,
        symbol=symbol,
        side=side,
        ticket=result.order if success else 0,
        volume=result.volume if success else 0.0,
        price=result.price if success else 0.0,
        comment=comment,
        error="" if success else result.comment,
    )


def place_market_order_fok(
    symbol: str,
    side: str,
    lot: float = None,
    comment: str = "",
    sl: float = 0.0,
    tp: float = 0.0,
    magic: int = 0,
) -> Dict[str, Any]:
    """
    Place a market order with FOK (Fill or Kill) execution.
    Price is fetched internally from MT5.

    Args:
        symbol:  e.g. "XAUUSD"
        side:    "buy" or "sell"
        lot:     lot size (uses config default if None)
        comment: order comment
        sl:      stop loss price (0 = no SL)
        tp:      take profit price (0 = no TP)
        magic:   magic number for EA identification

    Returns:
        dict with keys: retcode, price, volume, ticket, success, error, _stub
    """
    # Get lot from config if not provided
    if lot is None:
        sc = SYMBOLS.get(symbol)
        lot = sc.lot_size if sc else 0.01

    logger.info(f"FOK {side.upper()} {symbol} {lot} lots | comment={comment}")

    result = _execute_order_fok(
        symbol=symbol,
        side=side.lower(),
        lot=lot,
        comment=comment,
        sl=sl,
        tp=tp,
        magic=magic,
    )

    if result.success:
        logger.info(
            f"✅ FOK {side.upper()} executed: {symbol} {result.volume} lots "
            f"@ {result.price} | ticket={result.ticket}"
        )
    else:
        logger.error(
            f"❌ FOK {side.upper()} failed: {symbol} | "
            f"retcode={result.retcode} | {result.error} | attempts={result.attempts}"
        )

    return result.to_dict()


# ---------------------------------------------------------------------------
# Close Positions
# ---------------------------------------------------------------------------

@_retry(max_attempts=DEFAULT_RETRY_ATTEMPTS, delay=DEFAULT_RETRY_DELAY)
def _close_single_position(
    ticket: int,
    comment: str = "",
    deviation: int = DEFAULT_DEVIATION,
) -> TradeResult:
    """Close a single position by ticket."""
    if not _ensure_mt5():
        return TradeResult(
            success=False,
            retcode=-1,
            error="MT5 not initialized",
        )

    # Get position
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return TradeResult(
            success=False,
            retcode=-1,
            ticket=ticket,
            error=f"Position {ticket} not found",
        )

    pos = positions[0]
    symbol = pos.symbol

    # Get fresh tick
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return TradeResult(
            success=False,
            retcode=-1,
            symbol=symbol,
            ticket=ticket,
            error="Failed to get tick data",
        )

    # Determine close type and price
    if pos.type == mt5.POSITION_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    # Build request
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": deviation,
        "magic": pos.magic,
        "comment": comment[:31] if comment else "close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)

    if result is None:
        err = mt5.last_error()
        return TradeResult(
            success=False,
            retcode=err[0] if err else -1,
            symbol=symbol,
            ticket=ticket,
            error=str(err) if err else "order_send returned None",
        )

    success = result.retcode == TRADE_RETCODE_DONE

    return TradeResult(
        success=success,
        retcode=result.retcode,
        symbol=symbol,
        side="sell" if pos.type == mt5.POSITION_TYPE_BUY else "buy",
        ticket=ticket,
        volume=pos.volume,
        price=result.price if success else 0.0,
        profit=pos.profit if success else 0.0,
        comment=comment,
        error="" if success else result.comment,
    )


def close_position_fok(ticket: int, comment: str = "") -> Dict[str, Any]:
    """
    Close a single position by ticket using FOK.

    Args:
        ticket:  position ticket number
        comment: close comment

    Returns:
        dict with keys: retcode, success, profit, price, error
    """
    logger.info(f"Closing position ticket={ticket} | comment={comment}")

    result = _close_single_position(ticket=ticket, comment=comment)

    if result.success:
        logger.info(
            f"✅ Closed position {ticket}: {result.symbol} "
            f"@ {result.price} | profit={result.profit:.2f}"
        )
    else:
        logger.error(
            f"❌ Close position {ticket} failed: "
            f"retcode={result.retcode} | {result.error}"
        )

    return result.to_dict()


def close_all_positions_fok(
    symbol: str = None,
    comment: str = "",
    magic: int = None,
) -> Dict[str, Any]:
    """
    Close all positions, optionally filtered by symbol and/or magic.

    Args:
        symbol:  filter by symbol (None = all symbols)
        comment: close comment
        magic:   filter by magic number (None = all)

    Returns:
        dict with keys: retcode, closed, total, failed, results, total_profit
    """
    if not _ensure_mt5():
        return {
            "retcode": -1,
            "closed": False,
            "error": "MT5 not initialized",
            "_stub": False,
        }

    # Get positions
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()

    if positions is None:
        positions = []

    # Filter by magic if specified
    if magic is not None:
        positions = [p for p in positions if p.magic == magic]

    if not positions:
        logger.info(f"No positions to close (symbol={symbol}, magic={magic})")
        return {
            "retcode": TRADE_RETCODE_DONE,
            "closed": True,
            "total": 0,
            "failed": 0,
            "results": [],
            "total_profit": 0.0,
            "_stub": False,
        }

    logger.info(
        f"Closing {len(positions)} position(s) | "
        f"symbol={symbol or 'ALL'} | comment={comment}"
    )

    results = []
    total_profit = 0.0
    failed = 0

    for pos in positions:
        result = _close_single_position(ticket=pos.ticket, comment=comment)
        results.append(result.to_dict())

        if result.success:
            total_profit += result.profit
            logger.info(
                f"  ✅ Closed {pos.symbol} ticket={pos.ticket} "
                f"profit={result.profit:.2f}"
            )
        else:
            failed += 1
            logger.error(
                f"  ❌ Failed {pos.symbol} ticket={pos.ticket}: "
                f"{result.error}"
            )

    success = failed == 0

    return {
        "retcode": TRADE_RETCODE_DONE if success else -1,
        "closed": success,
        "total": len(positions),
        "failed": failed,
        "results": results,
        "total_profit": total_profit,
        "_stub": False,
    }


# ---------------------------------------------------------------------------
# Position Snapshot & P&L
# ---------------------------------------------------------------------------

def get_positions_snapshot(symbol: str = None) -> Dict[str, Any]:
    """
    Get snapshot of open positions.

    Args:
        symbol: filter by symbol (None = all)

    Returns:
        dict with keys: total_profit_usd, positions, count
    """
    if not _ensure_mt5():
        return {
            "total_profit_usd": 0.0,
            "positions": [],
            "count": 0,
            "error": "MT5 not initialized",
        }

    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()

    if positions is None:
        positions = []

    total_profit = 0.0
    pos_list = []

    for pos in positions:
        total_profit += pos.profit

        pos_list.append({
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "type": "buy" if pos.type == mt5.POSITION_TYPE_BUY else "sell",
            "volume": pos.volume,
            "price_open": pos.price_open,
            "price_current": pos.price_current,
            "sl": pos.sl,
            "tp": pos.tp,
            "profit": pos.profit,
            "swap": pos.swap,
            "magic": pos.magic,
            "comment": pos.comment,
            "time": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
        })

    return {
        "total_profit_usd": total_profit,
        "positions": pos_list,
        "count": len(pos_list),
    }


def get_realized_profit_since(
    symbol: str = None,
    since_dt: datetime = None,
) -> float:
    """
    Get realized profit from closed deals since a datetime.

    Args:
        symbol:   filter by symbol (None = all)
        since_dt: start datetime (None = today 00:00 UTC)

    Returns:
        float: total realized profit in account currency
    """
    if not _ensure_mt5():
        return 0.0

    # Default to today 00:00 UTC
    if since_dt is None:
        now = datetime.now(timezone.utc)
        since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Get deals
    from_ts = int(since_dt.timestamp())
    to_ts = int(datetime.now(timezone.utc).timestamp()) + 3600  # +1hr buffer

    deals = mt5.history_deals_get(from_ts, to_ts)

    if deals is None:
        return 0.0

    total_profit = 0.0

    for deal in deals:
        # Filter by symbol if specified
        if symbol and deal.symbol != symbol:
            continue

        # Only count exit deals (DEAL_ENTRY_OUT)
        if deal.entry == mt5.DEAL_ENTRY_OUT:
            total_profit += deal.profit + deal.swap + deal.commission

    return total_profit


# ---------------------------------------------------------------------------
# Simulation using order_calc_profit
# ---------------------------------------------------------------------------

def calc_profit(
    symbol: str,
    side: str,
    lot: float,
    open_price: float,
    close_price: float,
) -> float:
    """
    Calculate theoretical profit using MT5's order_calc_profit.

    Args:
        symbol:      e.g. "XAUUSD"
        side:        "buy" or "sell"
        lot:         lot size
        open_price:  entry price
        close_price: exit price

    Returns:
        float: profit in account currency (negative = loss)
    """
    if not _ensure_mt5():
        # Fallback calculation
        sc = SYMBOLS.get(symbol)
        if sc is None:
            return 0.0

        pip_size = sc.pip_size
        if side.lower() == "buy":
            pips = (close_price - open_price) / pip_size
        else:
            pips = (open_price - close_price) / pip_size

        # Rough estimate: $10 per pip per standard lot for gold
        return round(pips * lot * 10.0, 2)

    # Use MT5's built-in calculator
    order_type = mt5.ORDER_TYPE_BUY if side.lower() == "buy" else mt5.ORDER_TYPE_SELL

    profit = mt5.order_calc_profit(
        order_type,
        symbol,
        lot,
        open_price,
        close_price,
    )

    if profit is None:
        logger.warning(
            f"order_calc_profit returned None for {symbol} "
            f"{side} {lot} @ {open_price} → {close_price}"
        )
        return 0.0

    return float(profit)


def calc_profit_pips(
    symbol: str,
    side: str,
    open_price: float,
    close_price: float,
) -> float:
    """
    Calculate profit in pips.

    Args:
        symbol:      e.g. "XAUUSD"
        side:        "buy" or "sell"
        open_price:  entry price
        close_price: exit price

    Returns:
        float: profit in pips (negative = loss)
    """
    sc = SYMBOLS.get(symbol)
    pip_size = sc.pip_size if sc else 0.01

    if side.lower() == "buy":
        return (close_price - open_price) / pip_size
    else:
        return (open_price - close_price) / pip_size


# ---------------------------------------------------------------------------
# Simulation Mode (for BACKTEST)
# ---------------------------------------------------------------------------

class SimulatedPosition:
    """In-memory simulated position for backtesting."""
    def __init__(
        self,
        ticket: int,
        symbol: str,
        side: str,
        volume: float,
        open_price: float,
        open_time: datetime,
        sl: float = 0.0,
        tp: float = 0.0,
        magic: int = 0,
        comment: str = "",
    ):
        self.ticket = ticket
        self.symbol = symbol
        self.side = side
        self.volume = volume
        self.open_price = open_price
        self.open_time = open_time
        self.sl = sl
        self.tp = tp
        self.magic = magic
        self.comment = comment

    def calc_profit(self, current_price: float) -> float:
        """Calculate current P&L using MT5 order_calc_profit."""
        return calc_profit(
            self.symbol,
            self.side,
            self.volume,
            self.open_price,
            current_price,
        )


class SimulatedTrader:
    """
    Simulated trader for BACKTEST mode.
    Maintains in-memory positions and uses MT5 order_calc_profit for P&L.
    """

    def __init__(self):
        self._positions: Dict[int, SimulatedPosition] = {}
        self._ticket_counter = 90000000
        self._closed_trades: List[Dict] = []
        self._realized_profit = 0.0

    def _next_ticket(self) -> int:
        self._ticket_counter += 1
        return self._ticket_counter

    def open_position(
        self,
        symbol: str,
        side: str,
        lot: float,
        price: float,
        sl: float = 0.0,
        tp: float = 0.0,
        magic: int = 0,
        comment: str = "",
    ) -> Dict[str, Any]:
        """Open a simulated position."""
        ticket = self._next_ticket()

        pos = SimulatedPosition(
            ticket=ticket,
            symbol=symbol,
            side=side,
            volume=lot,
            open_price=price,
            open_time=datetime.now(timezone.utc),
            sl=sl,
            tp=tp,
            magic=magic,
            comment=comment,
        )

        self._positions[ticket] = pos

        logger.info(
            f"[SIM] Opened {side.upper()} {symbol} {lot} lots @ {price} | "
            f"ticket={ticket}"
        )

        return {
            "success": True,
            "retcode": TRADE_RETCODE_DONE,
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "volume": lot,
            "price": price,
            "_stub": False,
            "_simulated": True,
        }

    def close_position(
        self,
        ticket: int,
        close_price: float,
        comment: str = "",
    ) -> Dict[str, Any]:
        """Close a simulated position."""
        if ticket not in self._positions:
            return {
                "success": False,
                "retcode": -1,
                "error": f"Position {ticket} not found",
                "_simulated": True,
            }

        pos = self._positions.pop(ticket)
        profit = pos.calc_profit(close_price)
        self._realized_profit += profit

        trade = {
            "ticket": ticket,
            "symbol": pos.symbol,
            "side": pos.side,
            "volume": pos.volume,
            "open_price": pos.open_price,
            "close_price": close_price,
            "profit": profit,
            "open_time": pos.open_time.isoformat(),
            "close_time": datetime.now(timezone.utc).isoformat(),
        }
        self._closed_trades.append(trade)

        logger.info(
            f"[SIM] Closed {pos.side.upper()} {pos.symbol} @ {close_price} | "
            f"profit={profit:.2f} | ticket={ticket}"
        )

        return {
            "success": True,
            "retcode": TRADE_RETCODE_DONE,
            "ticket": ticket,
            "symbol": pos.symbol,
            "profit": profit,
            "price": close_price,
            "_simulated": True,
        }

    def close_all(self, symbol: str, close_price: float) -> Dict[str, Any]:
        """Close all simulated positions for symbol."""
        to_close = [
            t for t, p in self._positions.items()
            if p.symbol == symbol
        ]

        results = []
        total_profit = 0.0

        for ticket in to_close:
            r = self.close_position(ticket, close_price)
            results.append(r)
            if r["success"]:
                total_profit += r.get("profit", 0.0)

        return {
            "success": True,
            "retcode": TRADE_RETCODE_DONE,
            "closed": len(results),
            "total_profit": total_profit,
            "results": results,
            "_simulated": True,
        }

    def get_positions(self, symbol: str = None) -> List[Dict]:
        """Get open simulated positions."""
        result = []
        for ticket, pos in self._positions.items():
            if symbol and pos.symbol != symbol:
                continue
            result.append({
                "ticket": ticket,
                "symbol": pos.symbol,
                "side": pos.side,
                "volume": pos.volume,
                "open_price": pos.open_price,
                "sl": pos.sl,
                "tp": pos.tp,
                "magic": pos.magic,
            })
        return result

    def get_floating_pnl(self, symbol: str, current_price: float) -> float:
        """Get unrealized P&L for symbol."""
        total = 0.0
        for pos in self._positions.values():
            if pos.symbol == symbol:
                total += pos.calc_profit(current_price)
        return total

    def get_realized_pnl(self) -> float:
        """Get total realized P&L."""
        return self._realized_profit

    def reset(self):
        """Reset simulator state."""
        self._positions.clear()
        self._closed_trades.clear()
        self._realized_profit = 0.0
        logger.info("[SIM] Reset complete")


# Global simulator instance
simulator = SimulatedTrader()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def health_check() -> Dict[str, Any]:
    """
    MT5 connection health check.

    Returns:
        dict with connection status and account info
    """
    if not _ensure_mt5():
        return {
            "connected": False,
            "error": "MT5 not initialized",
        }

    term = mt5.terminal_info()
    acc = mt5.account_info()

    if term is None or acc is None:
        return {
            "connected": False,
            "error": "Failed to get terminal/account info",
        }

    return {
        "connected": term.connected,
        "trade_allowed": term.trade_allowed,
        "account": acc.login,
        "server": acc.server,
        "balance": acc.balance,
        "equity": acc.equity,
        "margin_free": acc.margin_free,
        "leverage": acc.leverage,
        "currency": acc.currency,
    }


# ---------------------------------------------------------------------------
# Convenience exports
# ---------------------------------------------------------------------------

__all__ = [
    # Order execution
    "place_market_order_fok",
    "close_position_fok",
    "close_all_positions_fok",

    # Position info
    "get_positions_snapshot",
    "get_realized_profit_since",

    # Simulation
    "calc_profit",
    "calc_profit_pips",
    "simulator",
    "SimulatedTrader",

    # Connection
    "shutdown",
    "health_check",

    # Result type
    "TradeResult",
]