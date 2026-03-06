# config/selectors.py
from __future__ import annotations

from typing import List, Tuple
from .symbols import SYMBOLS


def get_price_symbols() -> List[str]:
    """Symbols that should have pricing recorded."""
    return [k for k, sc in SYMBOLS.items() if sc.is_enabled]


def get_trading_symbols() -> List[str]:
    """Symbols that executor is allowed to trade."""
    return [k for k, sc in SYMBOLS.items() if sc.is_enabled and sc.is_trading_enabled]


def get_strategies_for_symbol(symbol: str) -> Tuple[str, ...]:
    """
    Returns the active strategy names for a symbol.
    Empty tuple if symbol not found or no strategies enabled.
    """
    sc = SYMBOLS.get(symbol)
    if sc is None:
        return ()
    return sc.strategies


def get_all_symbol_strategies() -> List[Tuple[str, str]]:
    """
    Returns a flat list of (symbol, strategy_name) pairs
    for every trading-enabled symbol with at least one strategy active.

    Used by the executor runner to spawn threads.

    Example output:
        [("XAUUSD", "astra_hawk"), ("EURUSD", "apex_harrier")]
    """
    pairs = []
    for symbol in get_trading_symbols():
        sc = SYMBOLS.get(symbol)
        if sc is None:
            continue
        for strategy_name in sc.strategies:
            pairs.append((symbol, strategy_name))
    return pairs